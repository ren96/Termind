"""隧道生命周期管理，供端口转发 / 开发面板共用。"""
from PyQt6.QtCore import QObject, pyqtSignal


class TunnelRecord:
    __slots__ = ("tunnel_id", "remote_host", "remote_port",
                 "local_port", "label", "service")

    def __init__(self, tunnel_id, remote_host, remote_port,
                 local_port, label="", service=""):
        self.tunnel_id = tunnel_id
        self.remote_host = remote_host
        self.remote_port = remote_port
        self.local_port = local_port
        self.label = label
        self.service = service


class TunnelManager(QObject):
    tunnel_opened = pyqtSignal(object)
    tunnel_closed = pyqtSignal(str)

    def __init__(self, worker, parent=None):
        super().__init__(parent)
        self.worker = worker
        self.records = {}
        self._seq = 0
        worker.tunnel_ready.connect(self._on_ready)
        worker.tunnel_closed.connect(self._on_closed)

    def open(self, remote_host, remote_port, local_port=0,
             label="", service=""):
        self._seq += 1
        tid = f"t{self._seq}"
        self.records[tid] = TunnelRecord(
            tid, remote_host, remote_port, local_port, label, service)
        self.worker.open_local_tunnel(
            tid, remote_host, remote_port, local_port)
        return tid

    def close(self, tunnel_id):
        self.worker.close_tunnel(tunnel_id)
        self.records.pop(tunnel_id, None)

    def close_all(self):
        for tid in list(self.records):
            self.close(tid)

    def _on_ready(self, tid, port):
        rec = self.records.get(tid)
        if rec:
            rec.local_port = port
            self.tunnel_opened.emit(rec)

    def _on_closed(self, tid):
        self.records.pop(tid, None)
        self.tunnel_closed.emit(tid)