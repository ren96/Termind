# Termind

Termind is a remote development workstation built with PyQt6, Qt WebEngine, xterm.js, and Paramiko. It combines an SSH terminal, SFTP file transfer, port forwarding, remote development launchers, a Git panel, and system monitoring into a single desktop application.

## Features

- **SSH connections**: password authentication, private key authentication, jump hosts, `~/.ssh/config` import, and host key verification.
- **Terminal**: xterm.js rendering, search, clickable URLs, `Ctrl + mouse wheel` font scaling, and broadcast input.
- **SFTP**: dual-pane file manager, recursive upload/download, online file editing, and permission changes.
- **Port forwarding**: scan remote listening ports and map them to local ports with one click.
- **Remote development**: launch VS Code Server or Jupyter Lab on the remote host.
- **System panel**: listening ports, systemd services, Docker containers, real-time monitoring, and live logs.
- **Git panel**: visual `status`, `log`, and `diff`, plus `pull` and `stash` actions.
- **Safety features**: dangerous command interception, multi-line paste confirmation, and production environment watermarks.
- **Credential storage**: passwords are stored in the system keyring instead of plain text configuration files.

## Requirements

- Python 3.9+
- PyQt6
- PyQt6-WebEngine
- Paramiko
- keyring
- Pygments

On Windows, an additional package is required for local interactive terminals:

- pywinpty

On Linux, Qt WebEngine may require additional system libraries. For Debian/Ubuntu-based distributions, common dependencies include:

```bash
sudo apt install libxcb-cursor0 libxkbcommon-x11-0 libnss3