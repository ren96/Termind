"""解析 ~/.ssh/config 为 Termind 的服务器配置列表。"""
import os

from constants import SSH_CONFIG_GROUP


def default_ssh_config_path():
    return os.path.expanduser("~/.ssh/config")


def parse_ssh_config(path=None):
    """返回 [{name, host, user, port, auth_type, key_path, group}, ...]"""
    path = path or default_ssh_config_path()
    if not os.path.exists(path):
        return []

    try:
        import paramiko
    except ImportError:
        return []

    cfg = paramiko.SSHConfig()
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            cfg.parse(f)
    except Exception:
        return []

    base_dir = os.path.dirname(os.path.abspath(path))
    results = []
    try:
        hostnames = sorted(cfg.get_hostnames())
    except Exception:
        return []

    for host in hostnames:
        if not host or '*' in host or '?' in host or host.startswith('!'):
            continue
        try:
            d = cfg.lookup(host)
        except Exception:
            continue

        hostname = d.get("hostname", host) or host
        user = d.get("user", "root") or "root"
        try:
            port = int(d.get("port", 22))
            if not (1 <= port <= 65535):
                port = 22
        except (ValueError, TypeError):
            port = 22

        identity = d.get("identityfile") or []
        if isinstance(identity, str):
            identity = [identity]
        key_path = ""
        auth_type = "password"
        for ip in identity:
            try:
                kp = os.path.expanduser(ip)
            except Exception:
                continue
            if not os.path.isabs(kp):
                kp = os.path.join(base_dir, kp)
            if kp and os.path.exists(kp):
                key_path = kp
                auth_type = "key"
                break

        results.append({
            "name": host,
            "host": hostname,
            "user": user,
            "port": port,
            "auth_type": auth_type,
            "key_path": key_path,
            "group": SSH_CONFIG_GROUP,
        })

    return results