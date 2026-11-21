"""Graphics items: devices, cables and animated PDUs."""
from __future__ import annotations

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPainterPathStroker, QPen
from PySide6.QtWidgets import QGraphicsItem

from .icons import ICON_H, ICON_W, device_pixmap

from . import theme


def _scale() -> float:
    return theme.STATE["scale"]


PORT_UP = QColor("#00e640")        # bright green: link is up
PORT_UP_GLOW = QColor(0, 230, 64, 70)
PORT_DOWN = QColor("#d32f2f")
PROTO_COLORS = {"ARP": QColor("#fbc02d"), "ICMP": QColor("#ec407a"), "OSPF": QColor("#7e57c2"),
                "BGP": QColor("#26a69a"), "DHCP": QColor("#8d6e63"), "DNS": QColor("#29b6f6"),
                "HTTP": QColor("#66bb6a"), "TCP": QColor("#bdbdbd"), "RIP": QColor("#ff7043"),
                "STP": QColor("#78909c"), "ICMPv6": QColor("#ab47bc"), "NDP": QColor("#ce93d8"),
                "SSH": QColor("#26a69a"), "TELNET": QColor("#ef5350"),
                "LACP": QColor("#8d6e63"), "PAgP": QColor("#a1887f"),
                "HSRP": QColor("#5c6bc0"), "VRRP": QColor("#7986cb"),
                "OSPFv3": QColor("#00897b"), "RIPng": QColor("#ff8a65"), "DHCPv6": QColor("#9ccc65")}


class DeviceItem(QGraphicsItem):
    # Plain QGraphicsItem (not QGraphicsObject): Qt signals carrying graphics
    # objects proved fragile in PySide6, so items use plain callbacks instead.

    def __init__(self, device):
        super().__init__()
        self.device = device
        self.links: list["LinkItem"] = []
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable
                      | QGraphicsItem.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.setZValue(10)
        self.setPos(device.x, device.y)
        self._hover = False
        self._font = QFont()
        self._font.setPointSizeF(8.5 * _scale())
        self.refresh()

    def refresh(self) -> None:
        d = self.device
        tip = f"<b>{d.name}</b><br>{d.model}"
        if d.hostname and d.hostname != d.name:
            tip += f"<br>hostname: {d.hostname}"
        for p in d.ports.values():
            if p.ip is not None:
                tip += f"<br>{p.short}: {p.ip.with_prefixlen}"
        self.setToolTip(tip)
        self.prepareGeometryChange()
        self.update()

    def center(self) -> QPointF:
        return self.pos()

    def _label_rect(self) -> QRectF:
        fm = QFontMetricsF(self._font)
        w = max(fm.horizontalAdvance(self.device.name) + 8, ICON_W)
        return QRectF(-w / 2, ICON_H / 2 + 1, w, fm.height() + 2)

    def boundingRect(self) -> QRectF:
        return QRectF(-ICON_W / 2 - 2, -ICON_H / 2 - 2, ICON_W + 4, ICON_H + 4).united(self._label_rect())

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addRect(QRectF(-ICON_W / 2, -ICON_H / 2, ICON_W, ICON_H))
        path.addRect(self._label_rect())
        return path

    def paint(self, p: QPainter, option, widget=None) -> None:
        if self.isSelected() or self._hover:
            p.setPen(QPen(QColor("#1e88e5"), 1.2, Qt.DashLine if self.isSelected() else Qt.SolidLine))
            p.setBrush(QColor(30, 136, 229, 30))
            p.drawRoundedRect(QRectF(-ICON_W / 2 - 2, -ICON_H / 2 - 2, ICON_W + 4, ICON_H + 4), 4, 4)
        kind = "laptop" if getattr(self.device, "model_key", "").startswith("laptop") else None
        pm = device_pixmap(self.device.category, getattr(self.device, "vendor", "generic"), kind=kind)
        p.drawPixmap(QPointF(-ICON_W / 2, -ICON_H / 2), pm)
        p.setFont(self._font)
        p.setPen(theme.color("label"))
        p.drawText(self._label_rect(), Qt.AlignHCenter | Qt.AlignTop, self.device.name)

    def apply_scale(self) -> None:
        self.prepareGeometryChange()
        self._font.setPointSizeF(8.5 * _scale())
        self.update()

    def hoverEnterEvent(self, e):
        self._hover = True
        self.update()

    def hoverLeaveEvent(self, e):
        self._hover = False
        self.update()

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.device.x, self.device.y = self.pos().x(), self.pos().y()
            for l in self.links:
                l.adjust()
            sc = self.scene()
            if sc is not None and hasattr(sc, "grow_to"):
                sc.grow_to(self.sceneBoundingRect())
        return super().itemChange(change, value)


class LinkItem(QGraphicsItem):
    show_port_labels = True

    def __init__(self, link, a: DeviceItem, b: DeviceItem):
        super().__init__()
        self.link = link
        self.a, self.b = a, b
        a.links.append(self)
        b.links.append(self)
        self.setFlags(QGraphicsItem.ItemIsSelectable)
        self.setZValue(1)
        self._font = QFont()
        self._font.setPointSizeF(7 * _scale())
        self._link_font_base = 7
        self._line = QLineF()
        self.adjust()

    def detach(self) -> None:
        for d in (self.a, self.b):
            if self in d.links:
                d.links.remove(self)

    def adjust(self) -> None:
        self.prepareGeometryChange()
        self._line = QLineF(self.a.center(), self.b.center())
        self.setToolTip(f"{self.link.a.device.name} {self.link.a.name}  ⟷  "
                        f"{self.link.b.device.name} {self.link.b.name}")
        self.update()

    def line(self) -> QLineF:
        return self._line

    def _point_at(self, dist_from_a: float, from_b: bool = False) -> QPointF:
        line = self._line
        length = line.length() or 1
        t = dist_from_a / length
        t = min(max(t, 0.0), 0.5)
        return line.pointAt(1 - t if from_b else t)

    def boundingRect(self) -> QRectF:
        r = QRectF(self._line.p1(), self._line.p2()).normalized()
        return r.adjusted(-60, -30, 60, 30)

    def shape(self) -> QPainterPath:
        path = QPainterPath(self._line.p1())
        path.lineTo(self._line.p2())
        s = QPainterPathStroker()
        s.setWidth(10)
        return s.createStroke(path)

    def paint(self, p: QPainter, option, widget=None) -> None:
        p.setRenderHint(QPainter.Antialiasing)
        color = theme.color("cable_sel") if self.isSelected() else theme.color("cable")
        pen = QPen(color, 2.4 if self.isSelected() else 1.6)
        if getattr(self.link, "wireless", False):
            pen.setStyle(Qt.DashLine)
            pen.setColor(QColor("#1e88e5") if self.isSelected() else QColor("#5c6bc0"))
        p.setPen(pen)
        p.drawLine(self._line)
        for port, from_b in ((self.link.a, False), (self.link.b, True)):
            pt = self._point_at(42, from_b)
            if port.is_up:
                p.setPen(Qt.NoPen)
                p.setBrush(PORT_UP_GLOW)
                p.drawEllipse(pt, 9, 9)
                p.setPen(QPen(QColor("#00a32e"), 1))
                p.setBrush(PORT_UP)
                p.drawEllipse(pt, 5.5, 5.5)
            else:
                p.setPen(QPen(QColor("white"), 1))
                p.setBrush(PORT_DOWN)
                tri = [QPointF(pt.x(), pt.y() - 5), QPointF(pt.x() - 5, pt.y() + 4),
                       QPointF(pt.x() + 5, pt.y() + 4)]
                p.drawPolygon(tri)
            if self.show_port_labels:
                lp = self._point_at(62, from_b)
                p.setFont(self._font)
                p.setPen(theme.color("port_label"))
                p.drawText(QRectF(lp.x() - 40, lp.y() - 16, 80, 14), Qt.AlignCenter, port.short)


def _link_apply_scale(self) -> None:
    self.prepareGeometryChange()
    self._font.setPointSizeF(self._link_font_base * _scale())
    self.update()


LinkItem.apply_scale = _link_apply_scale


class PacketItem(QGraphicsItem):
    """Envelope drawn on a cable for a frame in flight (simulation mode)."""

    def __init__(self, event, on_click=None):
        super().__init__()
        self.on_click = on_click
        self.event = event
        self.frame = event.info["frame"]
        self.setZValue(20)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(self.frame.summary())
        self.color = PROTO_COLORS.get(self.frame.protocol, QColor("#90a4ae"))

    def boundingRect(self) -> QRectF:
        return QRectF(-11, -8, 22, 16)

    def paint(self, p: QPainter, option, widget=None) -> None:
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(-10, -7, 20, 14)
        p.setPen(QPen(self.color.darker(170), 1.2))
        p.setBrush(QBrush(self.color))
        p.drawRect(r)
        p.drawPolyline([r.topLeft(), QPointF(0, 1), r.topRight()])

    def mousePressEvent(self, e):
        e.accept()
        if self.on_click is not None:
            self.on_click(self.event)
