# Termind

Termind 是一个基于 PyQt6 / Qt WebEngine / xterm.js / Paramiko 的远程开发工作站，集成 SSH 终端、SFTP 文件传输、端口转发、远程开发启动、Git 面板和系统监控。

## 功能

- SSH 连接：密码 / 密钥 / 跳板机 / `~/.ssh/config` 导入 / 主机密钥确认
- 终端：xterm.js 渲染、搜索、URL 点击、Ctrl+滚轮缩放、广播输入
- SFTP：双栏文件管理、递归上传下载、在线编辑、权限修改
- 端口转发：扫描远程监听端口，一键映射到本地
- 远程开发：VS Code Server / Jupyter Lab 启动入口
- 系统面板：端口 / systemd / Docker / 实时监控 / 实时日志
- Git：status / log / diff 可视化，pull / stash
- 安全：危险命令拦截、多行粘贴确认、生产环境水印
- 密码通过系统 keyring 保管

## 环境要求

- Python 3.9+
- PyQt6
- Qt WebEngine
- Paramiko
- keyring
- Pygments

Windows 本地终端还需要 `pywinpty`。

## 安装依赖

```bash
python -m pip install -r requirements.txt
```

Linux 如果启动 Qt WebEngine 报缺少系统库，请安装发行版对应的 Qt6 WebEngine 运行依赖，例如 Debian/Ubuntu 常见需要：

```bash
sudo apt install libxcb-cursor0 libxkbcommon-x11-0 libnss3
```

具体包名以你的 Linux 发行版为准。

## 运行

```bash
python main.py
```

Windows 下如果使用本地 PowerShell / CMD / WSL / Git Bash 终端，请确认已安装：

```bash
python -m pip install pywinpty
```

## 项目结构

```text
Termind/
├─ main.py                 # 程序入口
├─ constants.py            # 应用常量
├─ ui_main.py              # 主窗口
├─ terminal_tab.py         # 终端标签页
├─ workers.py              # SSH / 本地 Shell / SFTP 线程
├─ ui_sftp.py              # SFTP 面板
├─ remote_panels.py        # 端口转发 / Git / 系统面板
├─ tunnel_manager.py       # 隧道管理
├─ config_manager.py       # 配置与 keyring
├─ code_highlighter.py     # 代码高亮编辑器
├─ ssh_config_import.py    # ssh config 导入
├─ preferences.py          # 首选项
├─ about_dialog.py         # 关于对话框
├─ theme.py                # 主题样式
├─ terminal.html           # xterm.js 页面
├─ xterm_assets/           # xterm.js 前端资源
└─ tubiao/                 # 应用图标
```

## 配置与安全说明

- 服务器元数据保存在 `servers.json`。
- 密码保存在系统 keyring 中，不写入 `servers.json`。
- `servers.json` 和 `crash.log` 属于本机生成文件，已加入 `.gitignore`，不要提交到 GitHub。
- 首次连接未知主机时会弹出主机密钥指纹确认，确认后会写入 `~/.ssh/known_hosts`。

## 上传到 GitHub

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git push -u origin main
```

## 许可证

本项目使用 MIT License，详见 `LICENSE`。