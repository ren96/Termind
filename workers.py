import os
import sys
import queue
import time
import codecs
import socket
import select
import errno
import threading
import shutil
import subprocess
import paramiko
from PyQt6.QtCore import QThread, pyqtSignal, QObject, pyqtSlot, QSettings

from constants import (ORG_NAME, APP_NAME,
                       DEFAULT_SSH_TIMEOUT, DEFAULT_SSH_KEEPALIVE)

try:
    from paramiko.ssh_exception import (
        PasswordRequiredException, AuthenticationException, NoValidConnectionsError,
    )
except ImportError:
    PasswordRequiredException = paramiko.PasswordRequiredException
    AuthenticationException = paramiko.AuthenticationException
    NoValidConnectionsError = Exception

_HAS_WINPTY = False
if sys.platform == "win32":
    try:
        from winpty import PtyProcess as _WinPtyProcess
        _HAS_WINPTY = True
    except ImportError:
        _HAS_WINPTY = False


def _qs():
    return QSettings(ORG_NAME, APP_NAME)


def _known_hosts_path():
    return os.path.expanduser("~/.ssh/known_hosts")


_ERR_IN_PROGRESS = frozenset(filter(None, [
    getattr(errno, 'EINPROGRESS', None),
    getattr(errno, 'EWOULDBLOCK', None),
    getattr(errno, 'EALREADY', None),
    10035,
    10036,
]))


class _ConnectCancelled(Exception):
    pass


class _InteractivePolicy(paramiko.MissingHostKeyPolicy):
    def __init__(self, worker):
        self.worker = worker

    def missing_host_key(self, client, hostname, key):
        fp = key.get_fingerprint().hex()
        accept = self.worker.request_host_key_confirm(
            hostname, key.get_name(), fp)
        if not accept:
            raise paramiko.SSHException(f"用户拒绝信任主机密钥或确认超时: {hostname}")

        client.get_host_keys().add(hostname, key.get_name(), key)
        try:
            kh = _known_hosts_path()
            os.makedirs(os.path.dirname(kh), exist_ok=True)
            client.save_host_keys(kh)
        except Exception:
            pass


class SSHWorker(QThread):
    data_received = pyqtSignal(str)
    connection_lost = pyqtSignal()
    connected = pyqtSignal()
    host_key_verify = pyqtSignal(str, str, str)
    disconnected_with_reason = pyqtSignal(str)
    remote_exec_done = pyqtSignal(str, str)
    tunnel_ready = pyqtSignal(str, int)
    tunnel_closed = pyqtSignal(str)
    stream_data = pyqtSignal(str, str)
    stream_closed = pyqtSignal(str)

    _MAX_INFLIGHT_EXEC = 8

    def __init__(self, host, port, user, pwd=None, key_path=None, jump=None):
        super().__init__()
        self.host = host
        self.port = port
        self.user = user
        self.pwd = pwd
        self.key_path = key_path
        self.jump = jump
        self.client = None
        self.channel = None
        self._disconnect_requested = False
        self._send_queue = queue.Queue(maxsize=4000)
        self.decoder = codecs.getincrementaldecoder('utf-8')(errors='ignore')
        self._disconnect_reason = ""
        self._tunnels = {}
        self._tunnel_lock = threading.Lock()
        self._exec_lock = threading.Lock()
        self._exec_inflight = 0
        self._exec_inflight_lock = threading.Lock()
        self._sock = None

        self._streams = {}
        self._stream_lock = threading.Lock()

        self._jump_client = None

        self._hk_lock = threading.Lock()
        self._hk_event = None
        self._hk_accept = False

        s = _qs()
        self._timeout = max(3, s.value("ssh/timeout", DEFAULT_SSH_TIMEOUT, type=int))
        self._keepalive = max(0, s.value("ssh/keepalive", DEFAULT_SSH_KEEPALIVE, type=int))

    # ---------------- 主机密钥确认 ----------------
    def request_host_key_confirm(self, hostname, key_type, fp):
        with self._hk_lock:
            self._hk_event = threading.Event()
            self._hk_accept = False
            ev = self._hk_event

        try:
            self.host_key_verify.emit(hostname, key_type, fp)
        except RuntimeError:
            with self._hk_lock:
                self._hk_event = None
            return False

        deadline = time.monotonic() + 120
        try:
            while not ev.wait(0.2):
                if self._disconnect_requested:
                    return False
                if time.monotonic() > deadline:
                    return False
        finally:
            pass

        with self._hk_lock:
            accept = self._hk_accept
            self._hk_event = None
        return accept

    def respond_host_key(self, accept):
        with self._hk_lock:
            self._hk_accept = bool(accept)
            ev = self._hk_event
        if ev is not None:
            ev.set()

    def cancel_pending_host_key(self):
        self.respond_host_key(False)

    # ---------------- 常规状态 ----------------
    def _set_reason(self, reason):
        if not self._disconnect_reason:
            self._disconnect_reason = reason or ""

    def _emit_disconnect(self):
        try:
            self.disconnected_with_reason.emit(self._disconnect_reason or "连接结束")
        except Exception:
            pass
        try:
            self.connection_lost.emit()
        except Exception:
            pass

    def exec_command_sync(self, cmd, timeout=15):
        if not self.client or self._disconnect_requested:
            return ""
        try:
            with self._exec_lock:
                if self._disconnect_requested or not self.client:
                    return ""
                try:
                    stdin, stdout, stderr = self.client.exec_command(
                        cmd, timeout=timeout)
                except Exception as e:
                    return f"[ERROR] {e}"

                chan = stdout.channel
                try:
                    chan.settimeout(timeout)
                except Exception:
                    pass

                try:
                    out = stdout.read().decode('utf-8', errors='replace')
                    err = stderr.read().decode('utf-8', errors='replace')
                except socket.timeout:
                    try:
                        chan.close()
                    except Exception:
                        pass
                    return f"[ERROR] 命令执行超时（{timeout}s）: {cmd[:80]}"
                except Exception as e:
                    try:
                        chan.close()
                    except Exception:
                        pass
                    return f"[ERROR] 读取输出失败: {e}"
            return out + err
        except Exception as e:
            return f"[ERROR] {e}"

    def exec_command_async(self, cmd, cmd_id="", timeout=15):
        if not self.client or self._disconnect_requested:
            try:
                self.remote_exec_done.emit(cmd_id, "[ERROR] 连接已断开")
            except RuntimeError:
                pass
            return

        with self._exec_inflight_lock:
            if self._exec_inflight >= self._MAX_INFLIGHT_EXEC:
                too_many = True
            else:
                self._exec_inflight += 1
                too_many = False

        if too_many:
            try:
                self.remote_exec_done.emit(
                    cmd_id, "[ERROR] 命令队列繁忙，请稍后再试")
            except RuntimeError:
                pass
            return

        def _run():
            try:
                try:
                    out = self.exec_command_sync(cmd, timeout=timeout)
                except Exception as e:
                    out = f"[ERROR] {e}"
                if not self._disconnect_requested:
                    try:
                        self.remote_exec_done.emit(cmd_id, out)
                    except RuntimeError:
                        pass
            finally:
                with self._exec_inflight_lock:
                    self._exec_inflight -= 1

        threading.Thread(target=_run, daemon=True).start()

    def exec_sync(self, cmd):
        return self.exec_command_sync(cmd, timeout=30) or "执行完毕无输出"

    # ---------------- 隧道 ----------------
    def open_local_tunnel(self, tunnel_id, remote_host, remote_port, local_port=0):
        if not self.client or self._disconnect_requested:
            self.tunnel_closed.emit(tunnel_id)
            return
        transport = self.client.get_transport()
        if transport is None or not transport.is_active():
            self.tunnel_closed.emit(tunnel_id)
            return

        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind(("127.0.0.1", local_port))
            server.listen(16)
            server.settimeout(0.5)
        except OSError:
            try:
                server.close()
            except Exception:
                pass
            self.tunnel_closed.emit(tunnel_id)
            return

        actual_port = server.getsockname()[1]

        def _accept_loop():
            while not self._disconnect_requested:
                try:
                    conn, addr = server.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                threading.Thread(
                    target=self._bridge_socket,
                    args=(conn, transport, remote_host, remote_port),
                    daemon=True).start()
            try:
                server.close()
            except Exception:
                pass
            self.tunnel_closed.emit(tunnel_id)

        t = threading.Thread(target=_accept_loop, daemon=True)
        with self._tunnel_lock:
            self._tunnels[tunnel_id] = (server, t)
        t.start()
        self.tunnel_ready.emit(tunnel_id, actual_port)

    def _bridge_socket(self, conn, transport, remote_host, remote_port):
        chan = None
        try:
            chan = transport.open_channel(
                "direct-tcpip",
                (remote_host, remote_port),
                conn.getpeername()[:2])
            while not self._disconnect_requested:
                r, _, _ = select.select([conn, chan], [], [], 0.5)
                if conn in r:
                    data = conn.recv(4096)
                    if not data:
                        break
                    chan.sendall(data)
                if chan in r:
                    data = chan.recv(4096)
                    if not data:
                        break
                    conn.sendall(data)
        except Exception:
            pass
        finally:
            try:
                if chan:
                    chan.close()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass

    def close_tunnel(self, tunnel_id):
        with self._tunnel_lock:
            entry = self._tunnels.pop(tunnel_id, None)
        if entry:
            server, _ = entry
            try:
                server.close()
            except Exception:
                pass

    def close_all_tunnels(self):
        with self._tunnel_lock:
            ids = list(self._tunnels.keys())
        for tid in ids:
            self.close_tunnel(tid)

    # ---------------- 流式命令 ----------------
    def open_stream_command(self, cmd, stream_id):
        if not self.client or self._disconnect_requested:
            return False
        try:
            transport = self.client.get_transport()
            if not transport or not transport.is_active():
                return False
            chan = transport.open_session(timeout=10)
            chan.exec_command(cmd)
            chan.settimeout(0.5)
            with self._stream_lock:
                self._streams[stream_id] = chan
            t = threading.Thread(
                target=self._stream_reader,
                args=(stream_id, chan),
                daemon=True)
            t.start()
            return True
        except Exception:
            return False

    def _stream_reader(self, stream_id, chan):
        try:
            while not self._disconnect_requested:
                try:
                    data = chan.recv(4096)
                except socket.timeout:
                    if not chan.active:
                        break
                    continue
                if not data:
                    break
                try:
                    text = data.decode('utf-8', errors='replace')
                    self.stream_data.emit(stream_id, text)
                except RuntimeError:
                    break
        except Exception:
            pass
        finally:
            with self._stream_lock:
                self._streams.pop(stream_id, None)
            try:
                chan.close()
            except Exception:
                pass
            try:
                self.stream_closed.emit(stream_id)
            except RuntimeError:
                pass

    def close_stream(self, stream_id):
        with self._stream_lock:
            chan = self._streams.pop(stream_id, None)
        if chan is not None:
            try:
                chan.close()
            except Exception:
                pass

    def close_all_streams(self):
        with self._stream_lock:
            ids = list(self._streams.keys())
        for sid in ids:
            self.close_stream(sid)

    # ---------------- 连接（可中断） ----------------
    def _connect_interruptible(self, family, sock_type, proto, addr):
        sock = socket.socket(family, sock_type, proto)
        try:
            sock.setblocking(False)
            try:
                err = sock.connect_ex(addr)
            except OSError as e:
                sock.close()
                raise OSError(e.errno or 0, str(e))

            if err == 0:
                sock.setblocking(True)
                sock.settimeout(self._timeout)
                return sock

            if err not in _ERR_IN_PROGRESS:
                sock.close()
                raise OSError(err, os.strerror(err))

            deadline = time.monotonic() + self._timeout
            while True:
                if self._disconnect_requested:
                    sock.close()
                    raise _ConnectCancelled()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    sock.close()
                    raise socket.timeout("connect timeout")
                chunk = min(0.2, remaining)
                try:
                    _, writable, exceptional = select.select(
                        [], [sock], [sock], chunk)
                except (OSError, ValueError) as e:
                    sock.close()
                    raise OSError(0, f"select failed: {e}")
                if not writable and not exceptional:
                    continue
                err = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if err == 0:
                    sock.setblocking(True)
                    sock.settimeout(self._timeout)
                    return sock
                sock.close()
                raise OSError(err, os.strerror(err))
        except Exception:
            try:
                sock.close()
            except Exception:
                pass
            raise

    # ---------------- 跳板机连接 ----------------
    def _connect_through_jump(self):
        jh = self.jump or {}
        jump_host = jh.get('host', '')
        jump_user = jh.get('user', '')
        jump_port = int(jh.get('port', 22) or 22)
        jump_key = jh.get('key_path', '') or ''
        jump_pwd = jh.get('pwd')

        if not jump_host or not jump_user:
            self._set_reason("跳板机配置不完整")
            self.data_received.emit(
                "\r\n\x1b[31m[连接错误] 跳板机配置不完整\x1b[0m\r\n")
            return None

        jump_client = paramiko.SSHClient()
        try:
            jump_client.load_system_host_keys()
        except Exception:
            pass
        kh = _known_hosts_path()
        if os.path.exists(kh):
            try:
                jump_client.load_host_keys(kh)
            except Exception:
                pass
        jump_client.set_missing_host_key_policy(_InteractivePolicy(self))

        try:
            addr_info = socket.getaddrinfo(
                jump_host, jump_port, socket.AF_UNSPEC, socket.SOCK_STREAM)
        except OSError as e:
            self._set_reason(f"跳板机 DNS 解析失败: {e}")
            self.data_received.emit(
                f"\r\n\x1b[31m[连接错误] 跳板机 DNS: {e}\x1b[0m\r\n")
            try: jump_client.close()
            except Exception: pass
            return None

        jump_sock = None
        last_err = None
        for family, sock_type, proto, _, sock_addr in addr_info:
            if self._disconnect_requested:
                try: jump_client.close()
                except Exception: pass
                return None
            try:
                jump_sock = self._connect_interruptible(
                    family, sock_type, proto, sock_addr)
                break
            except _ConnectCancelled:
                try: jump_client.close()
                except Exception: pass
                return None
            except (socket.timeout, OSError) as e:
                last_err = e
                continue

        if jump_sock is None:
            self._set_reason(f"跳板机连接失败: {last_err}")
            self.data_received.emit(
                f"\r\n\x1b[31m[连接错误] 跳板机连接失败: {last_err}\x1b[0m\r\n")
            try: jump_client.close()
            except Exception: pass
            return None

        connect_kw = dict(
            sock=jump_sock, username=jump_user,
            timeout=self._timeout,
            banner_timeout=max(15, self._timeout),
            auth_timeout=max(15, self._timeout),
        )
        try:
            if jump_key:
                if not os.path.exists(jump_key):
                    raise FileNotFoundError(f"跳板机私钥不存在: {jump_key}")
                jump_client.connect(
                    jump_host, port=jump_port,
                    key_filename=jump_key,
                    passphrase=jump_pwd or None,
                    **connect_kw)
            else:
                jump_client.connect(
                    jump_host, port=jump_port,
                    password=jump_pwd,
                    **connect_kw)
        except paramiko.AuthenticationException:
            if not self._disconnect_requested:
                self._set_reason(f"跳板机认证失败: {jump_user}@{jump_host}")
                self.data_received.emit(
                    f"\r\n\x1b[31m[连接错误] 跳板机认证失败: "
                    f"{jump_user}@{jump_host}\x1b[0m\r\n")
            try: jump_client.close()
            except Exception: pass
            return None
        except _ConnectCancelled:
            try: jump_client.close()
            except Exception: pass
            return None
        except Exception as e:
            if not self._disconnect_requested:
                self._set_reason(f"跳板机连接失败: {e}")
                self.data_received.emit(
                    f"\r\n\x1b[31m[连接错误] 跳板机: {e}\x1b[0m\r\n")
            try: jump_client.close()
            except Exception: pass
            return None

        if self._keepalive > 0:
            try:
                jump_client.get_transport().set_keepalive(self._keepalive)
            except Exception:
                pass

        transport = jump_client.get_transport()
        if transport is None or not transport.is_active():
            self._set_reason("跳板 transport 不可用")
            try: jump_client.close()
            except Exception: pass
            return None

        try:
            chan = transport.open_channel(
                "direct-tcpip",
                (self.host, self.port),
                ("127.0.0.1", 0))
        except Exception as e:
            if not self._disconnect_requested:
                self._set_reason(f"跳板通道建立失败: {e}")
                self.data_received.emit(
                    f"\r\n\x1b[31m[连接错误] 跳板通道: {e}\x1b[0m\r\n")
            try: jump_client.close()
            except Exception: pass
            return None

        self._jump_client = jump_client
        return chan

    def run(self):
        sock = None
        try:
            self.client = paramiko.SSHClient()
            try:
                self.client.load_system_host_keys()
            except Exception:
                pass
            kh = _known_hosts_path()
            if os.path.exists(kh):
                try:
                    self.client.load_host_keys(kh)
                except Exception:
                    pass
            self.client.set_missing_host_key_policy(_InteractivePolicy(self))

            if self._disconnect_requested:
                return

            if self.jump:
                sock = self._connect_through_jump()
                if sock is None:
                    return
                self._sock = sock
            else:
                try:
                    addr_info = socket.getaddrinfo(
                        self.host, self.port, socket.AF_UNSPEC, socket.SOCK_STREAM)
                except OSError as e:
                    if not self._disconnect_requested:
                        self._set_reason(f"DNS 解析失败: {e}")
                        self.data_received.emit(
                            f"\r\n\x1b[31m[连接错误] 无法解析地址 {self.host}: {e}\x1b[0m\r\n")
                    return

                if not addr_info:
                    if not self._disconnect_requested:
                        self._set_reason("DNS 解析失败")
                        self.data_received.emit(
                            f"\r\n\x1b[31m[连接错误] 无法解析地址: {self.host}\x1b[0m\r\n")
                    return

                last_err = None
                for family, sock_type, proto, _, sock_addr in addr_info:
                    if self._disconnect_requested:
                        return
                    try:
                        sock = self._connect_interruptible(
                            family, sock_type, proto, sock_addr)
                        self._sock = sock
                        break
                    except _ConnectCancelled:
                        return
                    except (socket.timeout, OSError) as e:
                        last_err = e
                        continue

                if sock is None:
                    if not self._disconnect_requested and last_err is not None:
                        self._set_reason(f"无法连接: {last_err}")
                        self.data_received.emit(
                            f"\r\n\x1b[31m[连接错误] 无法连接: {last_err}\x1b[0m\r\n")
                    return

            if self._disconnect_requested:
                return

            connect_kw = dict(
                sock=sock, username=self.user,
                timeout=self._timeout,
                banner_timeout=max(15, self._timeout),
                auth_timeout=max(15, self._timeout),
            )
            if self.key_path:
                if not os.path.exists(self.key_path):
                    self._set_reason(f"私钥文件不存在: {self.key_path}")
                    self.data_received.emit(
                        f"\r\n\x1b[31m[连接错误] 私钥文件不存在: {self.key_path}\x1b[0m\r\n")
                    return
                try:
                    self.client.connect(
                        self.host, key_filename=self.key_path,
                        passphrase=self.pwd or None, **connect_kw)
                except PasswordRequiredException:
                    if not self._disconnect_requested:
                        self._set_reason("私钥已加密，需要密码")
                        self.data_received.emit(
                            "\r\n\x1b[31m[连接错误] 私钥已被加密，请填写正确的私钥密码！\x1b[0m\r\n")
                    return
                except AuthenticationException:
                    if not self._disconnect_requested:
                        self._set_reason("密钥认证失败")
                        self.data_received.emit(
                            "\r\n\x1b[31m[连接错误] 密钥认证失败，请检查密钥是否正确！\x1b[0m\r\n")
                    return
            else:
                try:
                    self.client.connect(self.host, password=self.pwd, **connect_kw)
                except AuthenticationException:
                    if not self._disconnect_requested:
                        self._set_reason("用户名或密码错误")
                        self.data_received.emit(
                            "\r\n\x1b[31m[连接错误] 密码认证失败，请检查用户名和密码！\x1b[0m\r\n")
                    return

            if self._disconnect_requested:
                return

            if self._keepalive > 0:
                self.client.get_transport().set_keepalive(self._keepalive)
            self.channel = self.client.invoke_shell(
                term='xterm-256color', width=80, height=24)
            self.channel.settimeout(0.1)
            self.connected.emit()

            while not self._disconnect_requested:
                self._drain_send_queue()
                try:
                    if not self.channel.active:
                        self._set_reason("通道已被对端关闭")
                        break
                except Exception:
                    self._set_reason("通道状态异常")
                    break
                try:
                    data = self.channel.recv(4096)
                except socket.timeout:
                    continue
                except Exception as e:
                    self._set_reason(f"接收失败: {e}")
                    break
                if not data:
                    self._set_reason("对端关闭了连接")
                    break
                text = self.decoder.decode(data)
                if text:
                    self.data_received.emit(text)
        except socket.timeout:
            if not self._disconnect_requested:
                self._set_reason("连接超时")
                self.data_received.emit(
                    "\r\n\x1b[31m[连接错误] 连接超时，请检查 IP 和端口是否正确。\x1b[0m\r\n")
        except NoValidConnectionsError as e:
            if not self._disconnect_requested:
                self._set_reason("无法建立连接")
                self.data_received.emit(
                    f"\r\n\x1b[31m[连接错误] 无法建立连接: {e}\x1b[0m\r\n")
        except Exception as e:
            if not self._disconnect_requested:
                self._set_reason(f"异常: {e}")
                self.data_received.emit(f"\r\n\x1b[31m[连接错误] {e}\x1b[0m\r\n")
        finally:
            if self._disconnect_requested:
                self._set_reason("用户主动断开")
            try:
                self.respond_host_key(False)
            except Exception:
                pass
            self.close_all_tunnels()

            try:
                self.close_all_streams()
            except Exception:
                pass
            try:
                if self._jump_client is not None:
                    self._jump_client.close()
            except Exception:
                pass
            self._jump_client = None

            try:
                with self._exec_lock:
                    try:
                        if self.channel:
                            self.channel.close()
                    except Exception:
                        pass
                    try:
                        if self.client:
                            self.client.close()
                    except Exception:
                        pass
            except Exception:
                pass

            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
            self._sock = None
            self._emit_disconnect()

    def _drain_send_queue(self):
        while True:
            try:
                kind, payload = self._send_queue.get_nowait()
            except queue.Empty:
                return
            try:
                if kind == 'data':
                    if self.channel and self.channel.active:
                        self.channel.send(payload)
                elif kind == 'resize':
                    cols, rows = payload
                    if self.channel and self.channel.active:
                        self.channel.resize_pty(width=cols, height=rows)
            except Exception:
                pass

    def send_data(self, data):
        if self._disconnect_requested:
            return
        try:
            self._send_queue.put_nowait(('data', data))
        except queue.Full:
            pass

    def resize_pty(self, cols, rows):
        if cols <= 0 or rows <= 0:
            return
        try:
            self._send_queue.put_nowait(('resize', (cols, rows)))
        except queue.Full:
            pass

    def disconnect(self):
        self._disconnect_requested = True
        self.cancel_pending_host_key()
        sock = self._sock
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass


# ============================================================
# 本地 Shell
# ============================================================
class _LocalShellBridge(QObject):
    data_received = pyqtSignal(str)
    connection_lost = pyqtSignal()
    connected = pyqtSignal()

    def __init__(self, shell="auto"):
        super().__init__()
        self.shell = shell
        self._proc = None
        self._pid = None
        self._master_fd = None
        self._running = False
        self._dims = (80, 24)
        self._decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        self._reader_thread = None
        self._writer_thread = None
        self._write_queue = queue.Queue(maxsize=1000)

    @pyqtSlot()
    def start_process(self):
        try:
            program, args = self._resolve_shell()
            if program is None:
                self._fail("找不到可用的本地 shell")
                return

            argv = [program] + list(args)
            self._running = True

            if sys.platform == "win32":
                self._start_windows(argv)
            else:
                self._start_unix(argv)

            if self._proc is None and self._master_fd is None:
                self._fail("PTY 启动失败")
                return

            self._reader_thread = threading.Thread(
                target=self._reader_loop, daemon=True)
            self._writer_thread = threading.Thread(
                target=self._writer_loop, daemon=True)
            self._reader_thread.start()
            self._writer_thread.start()

            if sys.platform == "win32" and 'cmd' in os.path.basename(program).lower():
                try:
                    self._write_queue.put_nowait(b"chcp 65001>nul\r")
                except queue.Full:
                    pass

            self.connected.emit()
        except Exception as e:
            self._fail(f"启动本地 shell 失败: {e}")

    def _fail(self, msg):
        try:
            self.data_received.emit(f"\r\n\x1b[31m[{msg}]\x1b[0m\r\n")
            self.connection_lost.emit()
        except Exception:
            pass

    def _start_windows(self, argv):
        if not _HAS_WINPTY:
            raise RuntimeError(
                "Windows 需要 pywinpty 才能提供交互式终端，请先执行:\n"
                "  pip install pywinpty")
        cmdline = subprocess.list2cmdline(argv)
        rows, cols = self._dims[1], self._dims[0]
        self._proc = _WinPtyProcess.spawn(cmdline, dimensions=(rows, cols))

    def _start_unix(self, argv):
        import pty as _pty
        env = os.environ.copy()
        env.setdefault("TERM", "xterm-256color")
        pid, master_fd = _pty.fork()
        if pid == 0:
            try:
                os.execvpe(argv[0], argv, env)
            except Exception:
                os._exit(127)
        self._pid = pid
        self._master_fd = master_fd
        self._apply_winsize_unix()

    def _apply_winsize_unix(self):
        if self._master_fd is None:
            return
        try:
            import fcntl
            import termios
            import struct
            cols, rows = self._dims
            fcntl.ioctl(
                self._master_fd, termios.TIOCSWINSZ,
                struct.pack("HHHH", rows, cols, 0, 0))
        except Exception:
            pass

    def _reader_loop(self):
        try:
            while self._running:
                if sys.platform == "win32":
                    if self._proc is None:
                        break
                    try:
                        data = self._proc.read(4096)
                    except (EOFError, OSError):
                        break
                    except Exception:
                        break
                    if not data:
                        try:
                            if not self._proc.isalive():
                                break
                        except Exception:
                            break
                        continue
                    if isinstance(data, bytes):
                        text = self._decoder.decode(data)
                    else:
                        text = data
                    if text:
                        self.data_received.emit(text)
                else:
                    if self._master_fd is None:
                        break
                    try:
                        r, _, _ = select.select([self._master_fd], [], [], 0.2)
                    except Exception:
                        break
                    if not r:
                        continue
                    try:
                        data = os.read(self._master_fd, 4096)
                    except OSError:
                        break
                    if not data:
                        break
                    text = self._decoder.decode(data)
                    if text:
                        self.data_received.emit(text)
        except Exception:
            pass
        finally:
            self._running = False
            try:
                self.connection_lost.emit()
            except Exception:
                pass

    def _writer_loop(self):
        while self._running:
            try:
                data = self._write_queue.get(timeout=0.3)
            except queue.Empty:
                continue
            if data is None:
                return
            try:
                if sys.platform == "win32":
                    if self._proc is not None:
                        if isinstance(data, bytes):
                            self._proc.write(data.decode('utf-8', errors='replace'))
                        else:
                            self._proc.write(data)
                else:
                    if self._master_fd is not None:
                        if isinstance(data, str):
                            os.write(self._master_fd, data.encode('utf-8'))
                        else:
                            os.write(self._master_fd, data)
            except Exception:
                pass

    @pyqtSlot(object)
    def write_data(self, data):
        try:
            self._write_queue.put_nowait(data)
        except queue.Full:
            pass

    @pyqtSlot(int, int)
    def resize_pty(self, cols, rows):
        if cols <= 0 or rows <= 0:
            return
        self._dims = (cols, rows)
        if sys.platform == "win32":
            if self._proc is not None:
                try:
                    self._proc.setwinsize(rows, cols)
                except Exception:
                    pass
        else:
            self._apply_winsize_unix()

    @pyqtSlot()
    def kill_process(self):
        self._running = False
        try:
            self._write_queue.put_nowait(None)
        except queue.Full:
            pass

        if sys.platform == "win32":
            if self._proc is not None:
                try:
                    self._proc.terminate(force=True)
                except Exception:
                    pass
                self._proc = None
        else:
            if self._master_fd is not None:
                try:
                    os.close(self._master_fd)
                except Exception:
                    pass
                self._master_fd = None
            if self._pid:
                try:
                    os.kill(self._pid, 9)
                except Exception:
                    pass
                try:
                    os.waitpid(self._pid, 0)
                except Exception:
                    pass
                self._pid = None

    def _resolve_shell(self):
        # ★ 修复：原 `return "cmd.exe", ["-K"] if False else "cmd.exe", ["/K"]`
        #   因逗号优先级低于 if-else，实际返回 3 元组，解包时抛 ValueError。
        if os.name != 'nt':
            # ★ 优化：优先尊重用户 $SHELL，回退到 bash
            shell_env = os.environ.get("SHELL", "")
            if shell_env and os.path.exists(shell_env):
                return shell_env, ["-i"]
            return "bash", ["-i"]
        shell = self.shell
        if shell == "powershell":
            return "powershell.exe", ["-NoLogo", "-NoExit"]
        if shell == "cmd":
            return "cmd.exe", ["/K"]
        if shell == "wsl":
            return "wsl.exe", []
        if shell == "gitbash":
            for p in (r"C:\Program Files\Git\bin\bash.exe",
                      r"C:\Program Files (x86)\Git\bin\bash.exe"):
                if os.path.exists(p):
                    return p, ["-i"]
            return None, None
        for exe in ("pwsh.exe", "powershell.exe"):
            if shutil.which(exe):
                return exe, ["-NoLogo", "-NoExit"]
        return "cmd.exe", ["/K"]


class LocalShellWorker(QThread):
    data_received = pyqtSignal(str)
    connection_lost = pyqtSignal()
    connected = pyqtSignal()
    disconnected_with_reason = pyqtSignal(str)
    remote_exec_done = pyqtSignal(str, str)
    tunnel_ready = pyqtSignal(str, int)
    tunnel_closed = pyqtSignal(str)
    stream_data = pyqtSignal(str, str)
    stream_closed = pyqtSignal(str)

    _start_sig = pyqtSignal()
    _write_sig = pyqtSignal(object)
    _kill_sig = pyqtSignal()
    _resize_sig = pyqtSignal(int, int)

    def __init__(self, shell="auto"):
        super().__init__()
        self.shell = shell
        self._bridge = _LocalShellBridge(shell)
        self._bridge.moveToThread(self)
        self._bridge.data_received.connect(self.data_received)
        self._bridge.connected.connect(self.connected)
        self._bridge.connection_lost.connect(self._on_bridge_finished)
        self._start_sig.connect(self._bridge.start_process)
        self._write_sig.connect(self._bridge.write_data)
        self._kill_sig.connect(self._bridge.kill_process)
        self._resize_sig.connect(self._bridge.resize_pty)

        self._start_time = 0

    def run(self):
        self._start_time = time.time()
        self._start_sig.emit()
        self.exec()

    def _on_bridge_finished(self):
        uptime = time.time() - self._start_time
        if uptime < 1.5:
            reason = "本地 shell 启动失败或立即退出（请检查环境配置，如 WSL 是否安装）"
        else:
            reason = "本地 shell 进程已退出"

        try:
            self.disconnected_with_reason.emit(reason)
        except Exception:
            pass
        self.connection_lost.emit()
        self.quit()

    def send_data(self, data):
        self._write_sig.emit(data.encode('utf-8'))

    def resize_pty(self, cols, rows):
        if cols <= 0 or rows <= 0:
            return
        self._resize_sig.emit(cols, rows)

    def exec_sync(self, cmd):
        return "本地终端不支持静默执行"

    def exec_command_sync(self, cmd, timeout=15):
        return "[ERROR] 本地终端不支持静默执行"

    def exec_command_async(self, cmd, cmd_id="", timeout=15):
        try:
            self.remote_exec_done.emit(cmd_id, "[ERROR] 本地终端不支持静默执行")
        except RuntimeError:
            pass

    def open_local_tunnel(self, *a, **kw):
        try:
            self.tunnel_closed.emit(a[0] if a else "")
        except Exception:
            pass

    def close_tunnel(self, tunnel_id):
        pass

    def close_all_tunnels(self):
        pass

    def disconnect(self):
        self._kill_sig.emit()
        try:
            self.quit()
        except Exception:
            pass

    def cancel_pending_host_key(self):
        pass

    def respond_host_key(self, accept):
        pass

    def open_stream_command(self, cmd, stream_id):
        return False

    def close_stream(self, stream_id):
        pass

    def close_all_streams(self):
        pass


class SFTPWorker(QThread):
    progress = pyqtSignal(int)
    progress_detail = pyqtSignal(int, str)
    done = pyqtSignal(str)
    file_list_ready = pyqtSignal(list)
    content_ready = pyqtSignal(str)
    write_finished = pyqtSignal(bool, str)
    op_finished = pyqtSignal(bool, str)

    _CHANNEL_TIMEOUT = 30

    def __init__(self, client, action, **kwargs):
        super().__init__()
        self.client = client
        self.action = action
        self.kwargs = kwargs
        self._last_t = 0.0
        self._last_bytes = 0

    def run(self):
        sftp = None
        msg = ""
        try:
            sftp = self.client.open_sftp()
            try:
                sftp.get_channel().settimeout(self._CHANNEL_TIMEOUT)
            except Exception:
                pass

            if self.action == "put":
                sftp.put(self.kwargs['local'], self.kwargs['remote'], callback=self._cb)
                msg = "✅ 上传成功"
            elif self.action == "put_recursive":
                self._upload_dir_recursive(sftp, self.kwargs['local'], self.kwargs['remote'])
                msg = "✅ 文件夹上传完成"
            elif self.action == "get":
                sftp.get(self.kwargs['remote'], self.kwargs['local'], callback=self._cb)
                msg = "✅ 下载成功"
            elif self.action == "get_recursive":
                self._download_dir_recursive(sftp, self.kwargs['remote'], self.kwargs['local'])
                msg = "✅ 文件夹下载完成"
            elif self.action == "list":
                include_hidden = self.kwargs.get('include_hidden', True)
                files = []
                for attr in sftp.listdir_attr(self.kwargs['path']):
                    if not include_hidden and attr.filename.startswith('.'):
                        continue
                    files.append({
                        "name": attr.filename,
                        "size": attr.st_size or 0,
                        "is_dir": (attr.st_mode & 0o040000) != 0,
                        "mode": attr.st_mode or 0,
                        "mtime": attr.st_mtime or 0,
                    })
                self.file_list_ready.emit(files)
                msg = "✅ 列表加载完成"
            elif self.action == "read":
                with sftp.open(self.kwargs['remote'], 'r') as f:
                    f.prefetch()
                    raw = f.read()
                if b"\x00" in raw:
                    raw = raw.replace(b"\x00", b"")
                content = raw.decode('utf-8', errors='replace')
                self.content_ready.emit(content)
                msg = "✅ 读取完成"
            elif self.action == "write":
                with sftp.open(self.kwargs['remote'], 'w') as f:
                    f.write(self.kwargs['content'])
                self.write_finished.emit(True, "保存成功")
                msg = "✅ 保存成功"
            elif self.action == "delete":
                paths = self.kwargs.get('paths', [])
                errors = []
                for p in paths:
                    try:
                        try:
                            sftp.remove(p)
                        except IOError:
                            self._rmdir_recursive(sftp, p)
                    except Exception as e:
                        errors.append(f"{p}: {e}")
                if errors:
                    self.op_finished.emit(False, "部分删除失败:\n" + "\n".join(errors))
                    msg = "❌ 部分删除失败"
                else:
                    self.op_finished.emit(True, f"✅ 已删除 {len(paths)} 项")
                    msg = f"✅ 已删除 {len(paths)} 项"
            elif self.action == "rename":
                try:
                    sftp.rename(self.kwargs['old'], self.kwargs['new'])
                    self.op_finished.emit(True, "✅ 重命名成功")
                    msg = "✅ 重命名成功"
                except Exception as e:
                    self.op_finished.emit(False, f"重命名失败: {e}")
                    msg = f"❌ 重命名失败: {e}"
            elif self.action == "mkdir":
                try:
                    sftp.mkdir(self.kwargs['path'])
                    self.op_finished.emit(True, "✅ 创建文件夹成功")
                    msg = "✅ 创建文件夹成功"
                except Exception as e:
                    self.op_finished.emit(False, f"创建失败: {e}")
                    msg = f"❌ 创建失败: {e}"
            elif self.action == "chmod":
                try:
                    sftp.chmod(self.kwargs['path'], self.kwargs['mode'])
                    self.op_finished.emit(True, "✅ 权限修改成功")
                    msg = "✅ 权限修改成功"
                except Exception as e:
                    self.op_finished.emit(False, f"chmod 失败: {e}")
                    msg = f"❌ chmod 失败: {e}"
        except Exception as e:
            if self.action == "write":
                self.write_finished.emit(False, f"保存失败: {str(e)}")
            elif self.action == "read":
                self.content_ready.emit(f"[读取失败] {str(e)}")
            elif self.action in ("delete", "rename", "mkdir", "chmod"):
                self.op_finished.emit(False, f"操作失败: {str(e)}")
            msg = f"❌ 操作失败: {str(e)}"
        finally:
            if sftp:
                try:
                    sftp.close()
                except Exception:
                    pass
            self.done.emit(msg)

    def _upload_dir_recursive(self, sftp, local_dir, remote_dir):
        try:
            sftp.mkdir(remote_dir)
        except IOError:
            pass
        for entry in os.listdir(local_dir):
            lp = os.path.join(local_dir, entry)
            rp = remote_dir.rstrip('/') + '/' + entry
            if os.path.isdir(lp) and not os.path.islink(lp):
                self._upload_dir_recursive(sftp, lp, rp)
            elif os.path.isfile(lp):
                sftp.put(lp, rp, callback=self._cb)

    def _download_dir_recursive(self, sftp, remote_dir, local_dir):
        os.makedirs(local_dir, exist_ok=True)
        for attr in sftp.listdir_attr(remote_dir):
            rp = remote_dir.rstrip('/') + '/' + attr.filename
            lp = os.path.join(local_dir, attr.filename)
            mode = attr.st_mode or 0
            if (mode & 0o170000) == 0o120000:
                continue
            if (mode & 0o170000) == 0o040000:
                self._download_dir_recursive(sftp, rp, lp)
            else:
                sftp.get(rp, lp, callback=self._cb)

    def _rmdir_recursive(self, sftp, path):
        for attr in sftp.listdir_attr(path):
            child = path.rstrip('/') + '/' + attr.filename
            mode = attr.st_mode or 0
            if (mode & 0o170000) == 0o120000:
                sftp.remove(child)
            elif (mode & 0o170000) == 0o040000:
                self._rmdir_recursive(sftp, child)
            else:
                sftp.remove(child)
        sftp.rmdir(path)

    def _cb(self, transferred, total):
        if total > 0:
            self.progress.emit(int(transferred / total * 100))

        now = time.time()
        # ★ 修复：递归上传/下载时 transferred 在切换文件时重置为 0，
        #   原逻辑保留上一文件的 _last_bytes 导致速度变负、ETA 错乱。
        if self._last_t == 0.0 or transferred < self._last_bytes:
            self._last_t = now
            self._last_bytes = transferred
            return
        dt = now - self._last_t
        if dt < 0.4:
            return
        speed = (transferred - self._last_bytes) / dt if dt > 0 else 0
        self._last_t = now
        self._last_bytes = transferred

        pct = int(transferred / total * 100) if total > 0 else 0
        if speed > 0 and total > transferred:
            eta = (total - transferred) / speed
            detail = f"{self._fmt_speed(speed)} · {self._fmt_eta(eta)}"
        elif speed > 0:
            detail = self._fmt_speed(speed)
        else:
            detail = ""
        if detail:
            self.progress_detail.emit(pct, detail)

    @staticmethod
    def _fmt_speed(bps):
        if bps >= 1024 * 1024:
            return f"{bps/1024/1024:.1f} MB/s"
        if bps >= 1024:
            return f"{bps/1024:.1f} KB/s"
        return f"{bps:.0f} B/s"

    @staticmethod
    def _fmt_eta(seconds):
        seconds = int(seconds)
        if seconds < 60:
            return f"剩余 {seconds}s"
        m, s = divmod(seconds, 60)
        if m < 60:
            return f"剩余 {m}m{s:02d}s"
        h, m = divmod(m, 60)
        return f"剩余 {h}h{m:02d}m"