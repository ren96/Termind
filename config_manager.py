import json
import os
import shutil
import tempfile
import keyring

from constants import CONFIG_FILE, KEYRING_SERVICE

# 迁移只尝试一次，避免每次 load_servers 都做磁盘检查
_migration_checked = False


def _config_dir():
    return os.path.dirname(os.path.abspath(CONFIG_FILE)) or "."


def _ensure_config_dir():
    try:
        os.makedirs(_config_dir(), exist_ok=True)
    except OSError:
        pass


def _migrate_legacy_config():
    """早期版本把 servers.json 放在当前工作目录，迁移到新位置（只跑一次）。"""
    global _migration_checked
    if _migration_checked:
        return
    _migration_checked = True

    if os.path.exists(CONFIG_FILE):
        return
    legacy = os.path.join(os.getcwd(), "servers.json")
    if os.path.abspath(legacy) == os.path.abspath(CONFIG_FILE):
        return
    if not os.path.exists(legacy):
        return
    try:
        _ensure_config_dir()
        shutil.copy2(legacy, CONFIG_FILE)
    except OSError:
        pass


def load_servers():
    _migrate_legacy_config()
    if not os.path.exists(CONFIG_FILE):
        return []
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        result = []
        seen = set()
        for s in data:
            if not isinstance(s, dict):
                continue
            if not (s.get("host") and s.get("user")):
                continue
            key = (s["host"], s["user"])
            if key in seen:
                continue
            seen.add(key)
            s.setdefault("name", s["host"])
            s.setdefault("port", 22)
            s.setdefault("auth_type", "password")
            s.setdefault("key_path", "")
            s.setdefault("group", "")
            result.append(s)
        return result
    except (json.JSONDecodeError, OSError):
        return []


def save_servers(servers):
    """原子写入，避免中途崩溃损坏配置文件。"""
    try:
        dir_name = _config_dir()
        os.makedirs(dir_name, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=dir_name, prefix=".servers_", suffix=".json.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(servers, f, ensure_ascii=False, indent=4)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, CONFIG_FILE)
            if os.name == "posix":
                try:
                    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                    dirfd = os.open(dir_name, flags)
                    try:
                        os.fsync(dirfd)
                    finally:
                        os.close(dirfd)
                except OSError:
                    pass
            return True
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except OSError as e:
        print(f"保存服务器配置失败: {e}")
        return False


def _key(host, username):
    return f"{username}@{host}"


def save_password(host, username, password):
    try:
        keyring.set_password(KEYRING_SERVICE, _key(host, username), password)
        return True
    except Exception as e:
        print(f"密码保存到系统凭据失败，请检查环境: {e}")
        return False


def get_password(host, username):
    try:
        return keyring.get_password(KEYRING_SERVICE, _key(host, username))
    except Exception:
        return None


def delete_password(host, username):
    try:
        keyring.delete_password(KEYRING_SERVICE, _key(host, username))
    except Exception:
        pass