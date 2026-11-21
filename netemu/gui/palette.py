"""Device palette (drag devices onto the canvas)."""
from __future__ import annotations

from PySide6.QtCore import QMimeData, QSize, Qt, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QToolBox, QVBoxLayout, QWidget

from ..core.devices import models_by_category
from ..i18n import tr
from .canvas import MIME_MODEL
from .icons import device_icon, device_pixmap

CATEGORIES = [("router", "Маршрутизаторы"), ("switch", "Коммутаторы"), ("hub", "Концентраторы"),
              ("end", "Конечные устройства"), ("wireless", "Беспроводные"), ("cloud", "Интернет")]


class ModelList(QListWidget):
    activated_model = Signal(str)

    def __init__(self, models):
        super().__init__()
        self.setViewMode(QListWidget.IconMode)
        self.setIconSize(QSize(64, 48))
        self.setGridSize(QSize(118, 92))
        self.setResizeMode(QListWidget.Adjust)
        self.setMovement(QListWidget.Static)
        self.setWordWrap(True)
        self.setDragEnabled(True)
        self.setSpacing(2)
        for m in models:
            it = QListWidgetItem(device_icon(m.category, m.vendor, "laptop" if m.key.startswith("laptop") else None), m.title)
            it.setData(Qt.UserRole, m.key)
            it.setToolTip(tr("{title}\nПеретащите на рабочую область или дважды щёлкните", title=m.title))
            self.addItem(it)
        self.itemDoubleClicked.connect(lambda it: self.activated_model.emit(it.data(Qt.UserRole)))

    def startDrag(self, actions):
        it = self.currentItem()
        if it is None:
            return
        key = it.data(Qt.UserRole)
        mime = QMimeData()
        mime.setData(MIME_MODEL, key.encode())
        drag = QDrag(self)
        drag.setMimeData(mime)
        from ..core.devices import MODELS
        m = MODELS[key]
        drag.setPixmap(device_pixmap(m.category, m.vendor))
        drag.exec(Qt.CopyAction)


class DevicePalette(QWidget):
    modelActivated = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        self.box = QToolBox()
        lay.addWidget(self.box)
        groups = models_by_category()
        pcs = groups.pop("pc", [])
        groups["end"] = [m for m in pcs if not m.key.startswith("laptop")] + groups.pop("server", []) + groups.pop("phone", [])
        groups["wireless"] = groups.get("wireless", []) + [m for m in pcs if m.key.startswith("laptop")]
        self.lists: dict[str, ModelList] = {}
        for key, title in CATEGORIES:
            lst = ModelList(groups.get(key, []))
            lst.activated_model.connect(self.modelActivated.emit)
            self.lists[key] = lst
            self.box.addItem(lst, tr(title))
