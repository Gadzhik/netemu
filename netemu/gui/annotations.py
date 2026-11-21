"""Canvas notes and frames (labelled zones such as "LAN 192.168.1.0/24" or "Area 0").

Each item edits a dict in ``topology.annotations`` so it is saved with the project:
``{"kind": "note"|"frame", "x", "y", "w", "h", "text", "color"}``."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QGraphicsItem, QInputDialog

from ..i18n import tr

FRAME_COLORS = ["#1e88e5", "#43a047", "#fb8c00", "#8e24aa", "#e53935", "#546e7a"]
GRIP = 12


class AnnotationItem(QGraphicsItem):
    def __init__(self, data: dict):
        super().__init__()
        self.data_ = data
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable
                      | QGraphicsItem.ItemSendsGeometryChanges)
        self.setPos(data["x"], data["y"])
        self.setZValue(-5 if data["kind"] == "frame" else 3)
        self._resizing = False
        self._font = QFont()
        self._font.setPointSizeF(9.5)

    # -- geometry ------------------------------------------------------------------------
    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self.data_["w"], self.data_["h"]).adjusted(-3, -3, 3, 3)

    def shape(self):
        from PySide6.QtGui import QPainterPath
        path = QPainterPath()
        r = QRectF(0, 0, self.data_["w"], self.data_["h"])
        if self.data_["kind"] == "frame":
            # only the border and the label are "solid": devices inside stay clickable
            inner = r.adjusted(10, 26, -10, -10)
            path.addRect(r)
            hole = QPainterPath()
            hole.addRect(inner)
            path = path.subtracted(hole)
        else:
            path.addRect(r)
        return path

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.data_["x"], self.data_["y"] = round(self.pos().x(), 1), round(self.pos().y(), 1)
        return super().itemChange(change, value)

    # -- painting ---------------------------------------------------------------------------
    def paint(self, p: QPainter, option, widget=None) -> None:
        d = self.data_
        r = QRectF(0, 0, d["w"], d["h"])
        color = QColor(d.get("color") or FRAME_COLORS[0])
        p.setRenderHint(QPainter.Antialiasing)
        p.setFont(self._font)
        if d["kind"] == "frame":
            fill = QColor(color)
            fill.setAlpha(22)
            pen = QPen(color, 2.2 if self.isSelected() else 1.6, Qt.DashLine)
            p.setPen(pen)
            p.setBrush(QBrush(fill))
            p.drawRoundedRect(r, 10, 10)
            p.setPen(color.darker(130))
            f = QFont(self._font)
            f.setBold(True)
            p.setFont(f)
            p.drawText(r.adjusted(12, 6, -12, 0), Qt.AlignLeft | Qt.AlignTop, d.get("text", ""))
        else:
            p.setPen(QPen(QColor("#c9a227") if not self.isSelected() else QColor("#1e88e5"), 1.2))
            p.setBrush(QColor("#fff7c2"))
            p.drawRoundedRect(r, 4, 4)
            p.setPen(QColor("#3b3b2f"))
            p.drawText(r.adjusted(8, 6, -8, -6), Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, d.get("text", ""))
        if self.isSelected():
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#1e88e5"))
            p.drawRect(QRectF(d["w"] - GRIP / 2, d["h"] - GRIP / 2, GRIP / 2, GRIP / 2))

    # -- interaction ------------------------------------------------------------------------
    def _in_grip(self, pos: QPointF) -> bool:
        return pos.x() > self.data_["w"] - GRIP and pos.y() > self.data_["h"] - GRIP

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self._in_grip(e.pos()):
            self._resizing = True
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._resizing:
            self.prepareGeometryChange()
            self.data_["w"] = max(80.0, round(e.pos().x(), 1))
            self.data_["h"] = max(40.0, round(e.pos().y(), 1))
            self.update()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._resizing = False
        super().mouseReleaseEvent(e)

    def hoverMoveEvent(self, e):
        self.setCursor(Qt.SizeFDiagCursor if self._in_grip(e.pos()) else Qt.ArrowCursor)

    def mouseDoubleClickEvent(self, e):
        self.edit_text()

    def edit_text(self) -> bool:
        d = self.data_
        if d["kind"] == "note":
            text, ok = QInputDialog.getMultiLineText(None, tr("Заметка"), tr("Текст заметки:"), d.get("text", ""))
        else:
            text, ok = QInputDialog.getText(None, tr("Рамка"), tr("Подпись рамки:"), text=d.get("text", ""))
        if ok:
            d["text"] = text
            self.update()
        return ok


def new_annotation(kind: str, pos: QPointF, n: int) -> dict:
    if kind == "note":
        return {"kind": "note", "x": round(pos.x(), 1), "y": round(pos.y(), 1), "w": 200.0, "h": 90.0,
                "text": tr("Заметка"), "color": None}
    return {"kind": "frame", "x": round(pos.x(), 1), "y": round(pos.y(), 1), "w": 360.0, "h": 240.0,
            "text": tr("Зона"), "color": FRAME_COLORS[n % len(FRAME_COLORS)]}
