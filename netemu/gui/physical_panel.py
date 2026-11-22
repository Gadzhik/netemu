"""Physical view: where every device stands (building / floor / rack) and every cable's type and length."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
                               QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from ..core.port import parse_location
from ..i18n import tr

MEDIA = (("copper", "Медь Cat 6 (до 100 м)"), ("fiber", "Оптика MM (до 550 м)"), ("singlemode", "Оптика SM (до 10 км)"))


def set_location(topology, dev, text: str) -> None:
    dev.location = text.strip()
    for link in topology.links:
        if dev in (link.a.device, link.b.device):
            link.set_physical(link.media, link.length_m)
    topology.engine.emit("device_changed", dev)


class PhysicalPanel(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        place = QPushButton(tr("Разместить выбранные…"))
        place.clicked.connect(self.place_selected)
        refresh = QPushButton(tr("Обновить"))
        refresh.clicked.connect(self.refresh)
        row.addWidget(place)
        row.addWidget(refresh)
        row.addStretch(1)
        lay.addLayout(row)
        split = QSplitter(Qt.Vertical)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([tr("Размещение"), tr("Устройства")])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        split.addWidget(self.tree)
        self.cables = QTableWidget(0, 4)
        self.cables.setHorizontalHeaderLabels([tr("Кабель"), tr("Тип"), tr("Длина, м (0 — авто)"), tr("Состояние")])
        hh = self.cables.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in (1, 2, 3):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.cables.setWordWrap(True)
        self.cables.verticalHeader().setVisible(False)
        split.addWidget(self.cables)
        lay.addWidget(split, 1)
        hint = QLabel(tr("Размещение задаётся как «Здание / Этаж / Стойка». Длина кабеля по умолчанию оценивается по "
                         "размещению: стойка 2 м, этаж 30 м, другой этаж 60 м, другое здание 300 м. Медный кабель "
                         "длиннее 100 м не поднимает линк — нужна оптика."))
        hint.setWordWrap(True)
        lay.addWidget(hint)
        eng = window.ctl.engine
        self._subs = [("device_changed", self._later), ("device_added", self._later), ("device_removed", self._later),
                      ("link_added", self._later), ("link_removed", self._later)]
        for name, fn in self._subs:
            eng.subscribe(name, fn)
        self._pending = False
        self.refresh()

    def shutdown(self) -> None:
        for name, fn in self._subs:
            self.window.ctl.engine.unsubscribe(name, fn)

    def _later(self, *a) -> None:
        if not self._pending:
            self._pending = True
            from PySide6.QtCore import QTimer
            QTimer.singleShot(200, self._do_refresh)

    def _do_refresh(self) -> None:
        self._pending = False
        try:
            self.refresh()
        except RuntimeError:
            pass

    def refresh(self) -> None:
        topo = self.window.topology
        self.tree.clear()
        nodes: dict = {}
        for d in sorted(topo.devices.values(), key=lambda x: x.name.lower()):
            b, f, r = parse_location(getattr(d, "location", ""))
            path = [x for x in (b or tr("Не размещено"), f, r) if x]
            parent, key = None, ()
            for part in path:
                key = key + (part,)
                if key not in nodes:
                    it = QTreeWidgetItem([part, ""])
                    (parent.addChild(it) if parent else self.tree.addTopLevelItem(it))
                    nodes[key] = it
                parent = nodes[key]
            parent.addChild(QTreeWidgetItem(["", d.name]))
        self.tree.expandAll()
        links = [l for l in topo.links if not l.wireless]
        self.cables.setRowCount(len(links))
        for i, link in enumerate(links):
            self.cables.setItem(i, 0, QTableWidgetItem(
                f"{link.a.device.name} {link.a.short} ↔ {link.b.device.name} {link.b.short}"))
            media = QComboBox()
            for code, title in MEDIA:
                media.addItem(tr(title), code)
            media.setCurrentIndex(max(0, media.findData(link.media)))
            media.currentIndexChanged.connect(lambda _=0, l=link, m=media: l.set_physical(m.currentData(), l.length_m))
            self.cables.setCellWidget(i, 1, media)
            length = QDoubleSpinBox()
            length.setRange(0, 20000)
            length.setDecimals(0)
            length.setValue(link.length_m or 0)
            length.setSpecialValueText(tr("авто ({m:.0f})", m=link.length))
            length.editingFinished.connect(lambda l=link, s=length: (l.set_physical(l.media, s.value() or None),
                                                                     self._later()))
            self.cables.setCellWidget(i, 2, length)
            state = QTableWidgetItem(tr("слишком длинный для {media}", media=link.media) if link.too_long else
                                     tr("в норме, {m:.0f} м", m=link.length))
            if link.too_long:
                state.setForeground(Qt.red)
            self.cables.setItem(i, 3, state)

    def place_selected(self) -> None:
        from .items import DeviceItem
        sel = [it.device for it in self.window.scene.selectedItems() if isinstance(it, DeviceItem)]
        if not sel:
            self.window.statusBar().showMessage(tr("Выделите устройства на схеме."), 5000)
            return
        cur = getattr(sel[0], "location", "")
        text, ok = QInputDialog.getText(self, tr("Размещение"), tr("Здание / Этаж / Стойка:"), text=cur)
        if ok:
            for d in sel:
                set_location(self.window.topology, d, text)
            self.refresh()
