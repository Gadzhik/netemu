"""Classroom mode transport and the teacher's roster panel (Qt sockets, newline-delimited JSON)."""
from __future__ import annotations

import json
import random

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QHostAddress, QTcpServer, QTcpSocket
from PySide6.QtWidgets import QHeaderView, QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from ..core.classroom import Roster
from ..i18n import tr

CLASS_PORT = 47900


def close_socket(s) -> None:
    """Drop a connection without letting its signals reach handlers of objects being torn down.

    A graceful disconnectFromHost() emits 'disconnected' later, possibly while the Python owner is
    already being destroyed; that corrupted the heap on Windows. Block the signals, abort, delete later."""
    if s is None:
        return
    s.blockSignals(True)
    s.abort()
    s.deleteLater()


def _send(sock, obj) -> None:
    if sock is not None and sock.state() == QTcpSocket.ConnectedState:
        sock.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))


class TeacherClass(QObject):
    changed = Signal()

    def __init__(self, assignment: dict, title: str):
        super().__init__()
        self.assignment = assignment
        self.roster = Roster(title)
        self.server = QTcpServer(self)
        self.pin = f"{random.randint(0, 9999):04d}"
        self.clients: dict = {}                     # socket -> {"name", "buf"}

    def start(self, port: int = CLASS_PORT) -> bool:
        if not self.server.listen(QHostAddress.AnyIPv4, port):
            return False
        self.server.newConnection.connect(self._accept)
        return True

    def stop(self) -> None:
        self.blockSignals(True)
        for s in list(self.clients):
            close_socket(s)
        self.clients.clear()
        self.server.blockSignals(True)
        self.server.close()

    def redistribute(self, assignment: dict, title: str) -> None:
        self.assignment = assignment
        self.roster.lab_title = title
        for s, info in self.clients.items():
            if info.get("name"):
                _send(s, {"assign": assignment})

    def _accept(self) -> None:
        while self.server.hasPendingConnections():
            s = self.server.nextPendingConnection()
            self.clients[s] = {"name": None, "buf": b""}
            s.readyRead.connect(lambda s=s: self._read(s))
            s.disconnected.connect(lambda s=s: self._gone(s))

    def _gone(self, s) -> None:
        info = self.clients.pop(s, None)
        if info and info.get("name"):
            self.roster.leave(info["name"])
            self.changed.emit()
        s.deleteLater()

    def _read(self, s) -> None:
        info = self.clients.get(s)
        if info is None:
            return
        info["buf"] += bytes(s.readAll())
        while b"\n" in info["buf"]:
            line, info["buf"] = info["buf"].split(b"\n", 1)
            try:
                msg = json.loads(line.decode("utf-8"))
            except ValueError:
                continue
            if info["name"] is None:
                if msg.get("pin") != self.pin:
                    _send(s, {"error": "bad pin"})
                    s.disconnectFromHost()
                    return
                st = self.roster.join(str(msg.get("hello", "Student"))[:40])
                info["name"] = st.name
                _send(s, {"welcome": st.name, "assign": self.assignment})
                self.changed.emit()
            elif "progress" in msg:
                self.roster.progress(info["name"], msg["progress"])
                self.changed.emit()


class StudentClass(QObject):
    assigned = Signal(dict)
    status = Signal(str)

    def __init__(self, name: str):
        super().__init__()
        self.name = name
        self.sock = QTcpSocket(self)
        self.buf = b""
        self.pin = ""

    def join(self, address: str, port: int, pin: str) -> None:
        self.pin = pin
        self.sock.connected.connect(lambda: _send(self.sock, {"hello": self.name, "pin": self.pin}))
        self.sock.readyRead.connect(self._read)
        self.sock.disconnected.connect(lambda: self.status.emit("lost"))
        self.sock.errorOccurred.connect(lambda *_: self.status.emit("error"))
        self.sock.connectToHost(address, port)

    def stop(self) -> None:
        self.blockSignals(True)
        close_socket(self.sock)
        self.sock = None

    def report(self, results) -> None:
        if self.sock is None:
            return
        _send(self.sock, {"progress": [[c.text, bool(ok), d] for c, ok, d in results]})

    def _read(self) -> None:
        self.buf += bytes(self.sock.readAll())
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            try:
                msg = json.loads(line.decode("utf-8"))
            except ValueError:
                continue
            if msg.get("error"):
                self.status.emit("error")
            if msg.get("welcome"):
                self.name = msg["welcome"]
                self.status.emit("joined")
            if msg.get("assign"):
                self.assigned.emit(msg["assign"])


class RosterPanel(QWidget):
    """Teacher's live table: one row per student with the share of checks passed."""

    def __init__(self, teacher: TeacherClass):
        super().__init__()
        self.t = teacher
        lay = QVBoxLayout(self)
        self.info = QLabel()
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([tr("Ученик"), tr("Проверки"), "%", tr("Последняя проверка")])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        lay.addWidget(self.table, 1)
        teacher.changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        r = self.t.roster
        self.info.setText(tr("Задание: {title}. PIN: {pin}. Подключено: {n}.", title=r.lab_title or "—", pin=self.t.pin,
                             n=sum(1 for s in r.students.values() if s.connected)))
        sts = sorted(r.students.values(), key=lambda s: s.name.lower())
        self.table.setRowCount(len(sts))
        for i, st in enumerate(sts):
            for c, text in enumerate((st.name + ("" if st.connected else " ✕"), f"{st.passed} / {st.total}",
                                      f"{st.percent}%", st.updated or "—")):
                self.table.setItem(i, c, QTableWidgetItem(text))
