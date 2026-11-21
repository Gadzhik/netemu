"""LAN collaboration transport (Qt sockets, newline-delimited JSON).

The host listens on a TCP port and relays every participant's changes to the others;
participants connect with the host's address and the 4-digit session PIN it shows."""
from __future__ import annotations

import json
import random

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QHostAddress, QNetworkInterface, QTcpServer, QTcpSocket

from ..core.collab import SyncPeer

DEFAULT_PORT = 47800
SYNC_MS = 400


def local_addresses() -> list[str]:
    out = []
    for a in QNetworkInterface.allAddresses():
        if a.protocol() == QHostAddress.IPv4Protocol and not a.isLoopback():
            out.append(a.toString())
    return out or ["127.0.0.1"]


class CollabSession(QObject):
    statusChanged = Signal(str)
    remoteApplied = Signal()

    def __init__(self, topology, name: str = "NetEmu"):
        super().__init__()
        self.topology = topology
        self.name = name
        self.peer = SyncPeer(topology)
        self.server: QTcpServer | None = None
        self.sock: QTcpSocket | None = None          # client side
        self.clients: dict[QTcpSocket, dict] = {}    # host side: socket -> {"name", "ok", "buf"}
        self.pin = ""
        self._buf = b""
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)

    # -- roles ----------------------------------------------------------------------------
    @property
    def active(self) -> bool:
        return self.server is not None or self.sock is not None

    @property
    def is_host(self) -> bool:
        return self.server is not None

    def host(self, port: int = DEFAULT_PORT) -> bool:
        self.stop()
        self.server = QTcpServer(self)
        if not self.server.listen(QHostAddress.AnyIPv4, port):
            err = self.server.errorString()
            self.server = None
            self.statusChanged.emit(f"error: {err}")
            return False
        self.pin = f"{random.randint(0, 9999):04d}"
        self.server.newConnection.connect(self._accept)
        self.peer.start_from_local()
        self.timer.start(SYNC_MS)
        self._status()
        return True

    def join(self, address: str, port: int, pin: str) -> None:
        self.stop()
        self.pin = pin
        self.peer.last = None
        self.sock = QTcpSocket(self)
        self.sock.connected.connect(lambda: self._write(self.sock, {"hello": self.name, "pin": self.pin}))
        self.sock.readyRead.connect(lambda: self._read(self.sock))
        self.sock.disconnected.connect(self._lost)
        self.sock.errorOccurred.connect(lambda *_: self.statusChanged.emit(f"error: {self.sock.errorString()}"
                                                                           if self.sock else "error"))
        self.sock.connectToHost(address, port)
        self.timer.start(SYNC_MS)
        self.statusChanged.emit("connecting")

    def stop(self) -> None:
        from .classroom import close_socket
        self.timer.stop()
        for s in list(self.clients):
            close_socket(s)
        self.clients.clear()
        if self.server is not None:
            self.server.blockSignals(True)
            self.server.close()
            self.server = None
        if self.sock is not None:
            s, self.sock = self.sock, None
            close_socket(s)
        self.statusChanged.emit("off")

    # -- network --------------------------------------------------------------------------
    def _accept(self) -> None:
        while self.server is not None and self.server.hasPendingConnections():
            s = self.server.nextPendingConnection()
            self.clients[s] = {"name": "?", "ok": False, "buf": b""}
            s.readyRead.connect(lambda s=s: self._read(s))
            s.disconnected.connect(lambda s=s: self._drop(s))

    def _drop(self, s) -> None:
        self.clients.pop(s, None)
        s.deleteLater()
        self._status()

    def _lost(self) -> None:
        if self.sock is not None:
            self.sock = None
            self.timer.stop()
            self.statusChanged.emit("lost")

    @staticmethod
    def _write(s, obj) -> None:
        if s is not None and s.state() == QTcpSocket.ConnectedState:
            s.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))

    def _read(self, s) -> None:
        if self.is_host:
            info = self.clients.get(s)
            if info is None:
                return
            info["buf"] += bytes(s.readAll())
            while b"\n" in info["buf"]:
                line, info["buf"] = info["buf"].split(b"\n", 1)
                self._host_message(s, info, line)
        else:
            self._buf += bytes(s.readAll())
            while b"\n" in self._buf:
                line, self._buf = self._buf.split(b"\n", 1)
                self._client_message(line)

    def _host_message(self, s, info, line) -> None:
        try:
            msg = json.loads(line.decode("utf-8"))
        except ValueError:
            return
        if not info["ok"]:
            if msg.get("pin") != self.pin:
                self._write(s, {"error": "bad pin"})
                s.disconnectFromHost()
                return
            info.update(ok=True, name=str(msg.get("hello", "?"))[:40])
            self._flush_local()                              # catch up before the full state goes out
            self._write(s, {"ops": [{"op": "full", "state": self.peer.last}]})
            self._status()
            return
        ops = msg.get("ops") or []
        if not ops:
            return
        pending = self.peer.receive(ops)
        for other, oi in self.clients.items():
            if other is not s and oi["ok"]:
                self._write(other, {"ops": ops})
        if pending:
            self._broadcast(pending)
        self.remoteApplied.emit()

    def _client_message(self, line) -> None:
        try:
            msg = json.loads(line.decode("utf-8"))
        except ValueError:
            return
        if msg.get("error"):
            self.statusChanged.emit("error: " + msg["error"])
            return
        ops = msg.get("ops") or []
        if not ops:
            return
        first = self.peer.last is None
        pending = self.peer.receive(ops)
        if first:
            pending = []                                     # joining replaces our local project
            self._status()
        if pending:
            self._write(self.sock, {"ops": pending})
        self.remoteApplied.emit()

    def _broadcast(self, ops) -> None:
        for s, info in self.clients.items():
            if info["ok"]:
                self._write(s, {"ops": ops})

    def _flush_local(self) -> None:
        ops = self.peer.local_ops()
        if ops:
            self._broadcast(ops)

    def _tick(self) -> None:
        if self.is_host:
            self._flush_local()
        elif self.sock is not None and self.peer.last is not None:
            ops = self.peer.local_ops()
            if ops:
                self._write(self.sock, {"ops": ops})

    def _status(self) -> None:
        if self.is_host:
            names = [i["name"] for i in self.clients.values() if i["ok"]]
            self.statusChanged.emit(f"host:{len(names)}:" + ", ".join(names))
        elif self.sock is not None:
            self.statusChanged.emit("joined")

    def participants(self) -> list[str]:
        return [i["name"] for i in self.clients.values() if i["ok"]]
