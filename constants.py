"""应用级常量与配置。"""
import os
import sys

APP_NAME = "Termind"
APP_VERSION = "1.3.0"
ORG_NAME = "Termind"

CONFIG_FILE = "servers.json"
KEYRING_SERVICE = "LinuxSSH_AI_Assistant"

TERMINAL_HISTORY_MAX = 8000
CMD_HISTORY_MAX = 500
MAX_EDIT_FILE_SIZE = 10 * 1024 * 1024
MAX_SYNTAX_HIGHLIGHT_SIZE = 1024 * 1024
SFTP_PATH_CACHE_MAX = 3000

# 终端字体
DEFAULT_FONT_SIZE = 14
MIN_FONT_SIZE = 8
MAX_FONT_SIZE = 30
TERMINAL_FONT_FAMILY = 'Cascadia Code, Consolas, "Courier New", monospace'

# 终端配色
TERM_BG = "#1e1e2e"
TERM_FG = "#cdd6f4"

# 终端 / 连接默认值
DEFAULT_SCROLLBACK = 5000
DEFAULT_SSH_TIMEOUT = 10
DEFAULT_SSH_KEEPALIVE = 30
DEFAULT_AUTO_RECONNECT = False
DEFAULT_AUTO_RECONNECT_INTERVAL = 3
DEFAULT_LOCAL_SHELL = "auto"

SSH_CONFIG_GROUP = "ssh_config"

# 环境标签
ENV_PRODUCTION = "prod"
ENV_STAGING = "staging"
ENV_DEVELOPMENT = "dev"

ENV_META = {
    ENV_PRODUCTION: {
        "label": "生产", "color": "#EF4444",
        "watermark": "PRODUCTION", "warn": True,
    },
    ENV_STAGING: {
        "label": "预发", "color": "#F59E0B",
        "watermark": "STAGING", "warn": True,
    },
    ENV_DEVELOPMENT: {
        "label": "开发", "color": "#10B981",
        "watermark": "", "warn": False,
    },
}

# 危险命令黑名单（发送前检查）
# ★ 修复：原规则 ~(?:\*|/|\s|$) 会误伤 rm -rf ~/foo.txt（普通文件删除），
#   把「目标为家目录/根目录本身或其通配」与「目标为家目录/根目录下的具体子路径」
#   区分开。新规则精确匹配前者，放行后者。
DANGEROUS_PATTERNS = [
    # rm 指向 /、~、$HOME 的本身或其通配（不允许后面跟具体子路径）
    # 命中: rm -rf /  /*  ~  ~/  ~/*  $HOME  $HOME/  $HOME/*
    # 放行: rm ~/foo.txt  $HOME/foo.txt  /tmp/foo  file.txt
    r'(^|[\s;&|])rm\s+(?:-[A-Za-z]+\s+)*(?:(?:~|\$HOME|/)\/?(?:\*|\s|$))',
    r'\bmkfs(\.[a-z0-9]+)?\b',
    r'\bdd\s+[^\n]*of\s*=\s*/dev/(sd|hd|nvme|vd)',
    r'>\s*/dev/(sd|hd|nvme|vd)',
    r':\(\)\s*\{[^}]*\}\s*;\s*:',
    r'\bchmod\s+-R\s+0?777\s+/\s*$',
    r'\bshutdown\b',
    r'\breboot\b',
    r'\binit\s+0\b',
    r'\bpoweroff\b',
    r'\bwipefs\b',
    r'\bshred\s+/dev/',
]


def get_resource_path(relative_path):
    """获取资源的绝对路径，兼容 PyInstaller 打包环境"""
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)