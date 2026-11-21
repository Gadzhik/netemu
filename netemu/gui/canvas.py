"""Topology canvas: scene with devices/cables and the view with editing tools."""
from __future__ import annotations

from PySide6.QtCore import QLineF, QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QGraphicsLineItem, QGraphicsScene, QGraphicsView, QMenu

from ..core.devices import MODELS
from .controller import SIMULATION, SimController
from ..i18n import tr
from .items import DeviceItem, LinkItem, PacketItem

MIME_MODEL = "application/x-netemu-model"

TOOL_SELECT = "select"
TOOL_CABLE = "cable"
TOOL_DELETE = "delete"
TOOL_PDU = "pdu"
TOOL_NOTE = "note"
TOOL_FRAME = "frame"


class TopologyScene(QGraphicsScene):
    deviceActivated = Signal(object)   # Device (double click)
    packetClicked = Signal(object)     # engine Event of the frame in flight
    changed_ = Signal()
    MARGIN = 600

    def __init__(self, ctl: SimController, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.topology = ctl.topology
        self.setSceneRect(-2000, -1500, 4000, 3000)   # grows as devices move out (grow_to)
        self.device_items: dict[str, DeviceItem] = {}
        self.link_items: dict[int, LinkItem] = {}
        self.packet_items: dict[int, PacketItem] = {}
        self.visible_protocols: set[str] | None = None  # None -> all
        ctl.deviceAdded.connect(self.add_device_item)
        ctl.deviceRemoved.connect(self.remove_device_item)
        ctl.linkAdded.connect(self.add_link_item)
        ctl.linkRemoved.connect(self.remove_link_item)
        ctl.linkStateChanged.connect(lambda port: self.refresh_links())
        ctl.deviceChanged.connect(self._device_changed)
        ctl.deviceMoved.connect(self._device_moved)
        ctl.tick.connect(self.update_packets)
        for d in self.topology.devices.values():
            self.add_device_item(d)
        for l in self.topology.links:
            self.add_link_item(l)
        self.annotation_items: list = []
        self.load_annotations()

    # -- notes and frames -------------------------------------------------------------
    def load_annotations(self) -> None:
        from .annotations import AnnotationItem
        for it in getattr(self, "annotation_items", []):
            if it.scene() is self:
                self.removeItem(it)
        self.annotation_items = []
        for data in self.topology.annotations:
            it = AnnotationItem(data)
            it.setAcceptHoverEvents(True)
            self.addItem(it)
            self.annotation_items.append(it)

    def add_annotation(self, kind: str, pos) -> object:
        from .annotations import AnnotationItem, new_annotation
        data = new_annotation(kind, pos, len(self.topology.annotations))
        self.topology.annotations.append(data)
        it = AnnotationItem(data)
        it.setAcceptHoverEvents(True)
        self.addItem(it)
        self.annotation_items.append(it)
        return it

    # -- devices / links ------------------------------------------------------------
    def grow_to(self, rect) -> None:
        """Enlarge the scrollable area so *rect* (plus a margin) is reachable."""
        want = rect.adjusted(-self.MARGIN, -self.MARGIN, self.MARGIN, self.MARGIN)
        cur = self.sceneRect()
        if not cur.contains(want):
            self.setSceneRect(cur.united(want))

    def add_device_item(self, dev) -> DeviceItem:
        item = DeviceItem(dev)
        self.addItem(item)
        self.grow_to(item.sceneBoundingRect())
        self.device_items[dev.id] = item
        self.changed_.emit()
        return item

    def remove_device_item(self, dev) -> None:
        item = self.device_items.pop(dev.id, None)
        if item is not None:
            self.removeItem(item)
        self.changed_.emit()

    def add_link_item(self, link) -> None:
        a = self.device_items.get(link.a.device.id)
        b = self.device_items.get(link.b.device.id)
        if a is None or b is None:
            return
        item = LinkItem(link, a, b)
        self.addItem(item)
        self.link_items[id(link)] = item
        self.changed_.emit()

    def remove_link_item(self, link) -> None:
        item = self.link_items.pop(id(link), None)
        if item is not None:
            item.detach()
            self.removeItem(item)
        self.changed_.emit()

    def refresh_links(self) -> None:
        for item in self.link_items.values():
            item.update()

    def _device_moved(self, dev) -> None:
        item = self.device_items.get(dev.id)
        if item is not None:
            item.setPos(dev.x, dev.y)

    def _device_changed(self, dev) -> None:
        item = self.device_items.get(dev.id)
        if item:
            item.refresh()
        self.refresh_links()

    def delete_items(self, items) -> None:
        from .annotations import AnnotationItem
        for it in [i for i in items if isinstance(i, AnnotationItem)]:
            if it.data_ in self.topology.annotations:
                self.topology.annotations.remove(it.data_)
            if it in self.annotation_items:
                self.annotation_items.remove(it)
            self.removeItem(it)
        devices = [i.device for i in items if isinstance(i, DeviceItem)]
        links = [i.link for i in items if isinstance(i, LinkItem)]
        for l in links:
            if l in self.topology.links:
                self.topology.disconnect(l)
        for d in devices:
            self.topology.remove_device(d)

    # -- packet animation ------------------------------------------------------------
    def update_packets(self) -> None:
        engine = self.ctl.engine
        alive = set()
        if self.ctl.mode == SIMULATION:
            for ev in engine.frames_in_flight():
                frame = ev.info["frame"]
                if self.visible_protocols is not None and frame.protocol not in self.visible_protocols:
                    continue
                src = self.device_items.get(ev.info["src"].device.id)
                dst = self.device_items.get(ev.info["dst"].device.id)
                if src is None or dst is None:
                    continue
                key = ev.seq
                alive.add(key)
                item = self.packet_items.get(key)
                if item is None:
                    item = PacketItem(ev, self.packetClicked.emit)
                    self.addItem(item)
                    self.packet_items[key] = item
                span = ev.time - ev.created
                t = 1.0 if span <= 0 else min(max((engine.now - ev.created) / span, 0.0), 1.0)
                # keep the envelope a bit away from the icons
                t = 0.15 + 0.7 * t
                item.setPos(QLineF(src.center(), dst.center()).pointAt(t))
        for key in [k for k in self.packet_items if k not in alive]:
            self.removeItem(self.packet_items.pop(key))

    def dispose(self) -> None:
        """Detach from the controller and drop every item explicitly.

        QGraphicsItem wrappers must not outlive their C++ objects; removing them
        here returns ownership to Python before the scene is destroyed.
        """
        ctl = self.ctl
        for sig, slot in ((ctl.deviceAdded, self.add_device_item), (ctl.deviceRemoved, self.remove_device_item),
                          (ctl.linkAdded, self.add_link_item), (ctl.linkRemoved, self.remove_link_item),
                          (ctl.deviceChanged, self._device_changed), (ctl.tick, self.update_packets)):
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        for item in list(self.packet_items.values()):
            self.removeItem(item)
        for item in list(self.link_items.values()):
            item.detach()
            self.removeItem(item)
        for item in list(self.device_items.values()):
            self.removeItem(item)
        for item in list(getattr(self, "annotation_items", [])):
            if item.scene() is self:
                self.removeItem(item)
        self.annotation_items = []
        self.packet_items.clear()
        self.link_items.clear()
        self.device_items.clear()

    def item_for_device(self, dev) -> DeviceItem | None:
        return self.device_items.get(dev.id)


class TopologyView(QGraphicsView):
    toolChanged = Signal(str)
    status = Signal(str)
    pduRequested = Signal(object, object)  # source device, destination device

    def __init__(self, scene: TopologyScene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setAcceptDrops(True)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
        from . import theme
        self.setBackgroundBrush(theme.color("canvas"))
        self.tool = TOOL_SELECT
        self._cable_from = None          # Port chosen as the first cable end
        self._cable_line: QGraphicsLineItem | None = None
        self._pdu_from = None
        self._pan_start = None
        self.port_menu_hook = None       # tests can replace the port chooser

    @property
    def tscene(self) -> TopologyScene:
        return self.scene()

    # -- tools -----------------------------------------------------------------------
    def set_tool(self, tool: str) -> None:
        self._cancel_pending()
        self.tool = tool
        self.setDragMode(QGraphicsView.RubberBandDrag if tool == TOOL_SELECT else QGraphicsView.NoDrag)
        cursors = {TOOL_SELECT: Qt.ArrowCursor, TOOL_CABLE: Qt.CrossCursor,
                   TOOL_DELETE: Qt.ForbiddenCursor, TOOL_PDU: Qt.PointingHandCursor,
                   TOOL_NOTE: Qt.IBeamCursor, TOOL_FRAME: Qt.CrossCursor}
        self.viewport().setCursor(cursors[tool])
        hints = {TOOL_SELECT: tr("Выбор и перемещение устройств. Двойной щелчок открывает устройство."),
                 TOOL_CABLE: tr("Кабель: щёлкните первое устройство и выберите порт, затем второе."),
                 TOOL_DELETE: tr("Удаление: щёлкните устройство или кабель."),
                 TOOL_PDU: tr("Простой PDU (ping): щёлкните источник, затем получателя."),
                 TOOL_NOTE: tr("Заметка: щёлкните место на схеме. Двойной щелчок по заметке меняет текст."),
                 TOOL_FRAME: tr("Рамка: щёлкните место на схеме. Размер меняется за правый нижний угол, "
                                "подпись — двойным щелчком.")}
        self.status.emit(hints[tool])
        self.toolChanged.emit(tool)

    def _cancel_pending(self) -> None:
        self._cable_from = None
        self._pdu_from = None
        if self._cable_line is not None:
            self.scene().removeItem(self._cable_line)
            self._cable_line = None

    def _device_at(self, pos) -> DeviceItem | None:
        for it in self.items(pos):
            if isinstance(it, DeviceItem):
                return it
        return None

    def choose_port(self, item: DeviceItem, global_pos):
        """Pop up a menu with the device's ports; returns the chosen Port or None."""
        if self.port_menu_hook is not None:
            return self.port_menu_hook(item.device)
        menu = QMenu(self)
        actions = {}
        for p in item.device.physical_ports():
            label = p.name + (f"  ({p.peer.device.name} {p.peer.short})" if p.link else "")
            act = menu.addAction(label)
            act.setEnabled(p.link is None)
            actions[act] = p
        chosen = menu.exec(global_pos)
        return actions.get(chosen)

    # -- mouse -------------------------------------------------------------------------
    def mousePressEvent(self, e):
        if e.button() == Qt.MiddleButton:
            self._pan_start = e.position()
            self.viewport().setCursor(Qt.ClosedHandCursor)
            e.accept()
            return
        if e.button() == Qt.RightButton and self.tool != TOOL_SELECT:
            self._cancel_pending()
            self.set_tool(TOOL_SELECT)
            return
        if e.button() == Qt.LeftButton and self.tool == TOOL_CABLE:
            self._cable_click(e)
            return
        if e.button() == Qt.LeftButton and self.tool == TOOL_DELETE:
            it = self.itemAt(e.position().toPoint())
            if it is not None and not isinstance(it, PacketItem):
                while it.parentItem() is not None:
                    it = it.parentItem()
                self.tscene.delete_items([it])
            return
        if e.button() == Qt.LeftButton and self.tool == TOOL_PDU:
            self._pdu_click(e)
            return
        if e.button() == Qt.LeftButton and self.tool in (TOOL_NOTE, TOOL_FRAME):
            it = self.tscene.add_annotation(self.tool, self.mapToScene(e.position().toPoint()))
            it.setSelected(True)
            self.set_tool(TOOL_SELECT)
            return
        super().mousePressEvent(e)

    def _cable_click(self, e) -> None:
        item = self._device_at(e.position().toPoint())
        if item is None:
            self._cancel_pending()
            return
        gp = e.globalPosition().toPoint()
        if self._cable_from is None:
            port = self.choose_port(item, gp)
            if port is None:
                return
            self._cable_from = port
            start = item.center()
            self._cable_line = QGraphicsLineItem(QLineF(start, start))
            self._cable_line.setPen(QPen(QColor("#ff8f00"), 2, Qt.DashLine))
            self._cable_line.setZValue(5)
            self.scene().addItem(self._cable_line)
            self.status.emit(tr("Порт {dev} {port} выбран. Щёлкните второе устройство.", dev=port.device.name, port=port.name))
            return
        if item.device is self._cable_from.device:
            self.status.emit(tr("Нельзя соединить устройство само с собой."))
            return
        port = self.choose_port(item, gp)
        if port is None:
            return
        a = self._cable_from
        self._cancel_pending()
        try:
            self.tscene.topology.connect(a, port)
            self.status.emit(tr("Соединено: {a} ⟷ {b}", a=f"{a.device.name} {a.name}", b=f"{port.device.name} {port.name}"))
        except ValueError as ex:
            self.status.emit(tr("Ошибка: {err}", err=ex))

    def _pdu_click(self, e) -> None:
        item = self._device_at(e.position().toPoint())
        if item is None:
            self._pdu_from = None
            return
        if self._pdu_from is None:
            self._pdu_from = item.device
            self.status.emit(tr("Источник: {name}. Щёлкните получателя.", name=item.device.name))
            return
        src, dst = self._pdu_from, item.device
        self._pdu_from = None
        self.pduRequested.emit(src, dst)

    def mouseMoveEvent(self, e):
        if self._pan_start is not None:
            delta = e.position() - self._pan_start
            self._pan_start = e.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(delta.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(delta.y()))
            return
        if self._cable_line is not None:
            line = self._cable_line.line()
            self._cable_line.setLine(QLineF(line.p1(), self.mapToScene(e.position().toPoint())))
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MiddleButton and self._pan_start is not None:
            self._pan_start = None
            self.set_tool(self.tool)
            return
        super().mouseReleaseEvent(e)
        topo = self.tscene.topology
        topo.undo.commit()                           # a drag ended: record the move as one step
        if any(d.category == "wireless" for d in topo.devices.values()):
            topo.rescan_wireless()                   # a device moved: Wi-Fi range may have changed

    def mouseDoubleClickEvent(self, e):
        item = self._device_at(e.position().toPoint())
        if item is not None and self.tool == TOOL_SELECT:
            self.tscene.deviceActivated.emit(item.device)
            return
        super().mouseDoubleClickEvent(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self._cancel_pending()
            self.set_tool(TOOL_SELECT)
            return
        if e.key() == Qt.Key_Delete:
            self.tscene.delete_items(self.scene().selectedItems())
            return
        super().keyPressEvent(e)

    def wheelEvent(self, e):
        factor = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
        self.zoom(factor)

    def zoom(self, factor: float) -> None:
        current = self.transform().m11()
        if 0.2 <= current * factor <= 4:
            self.scale(factor, factor)

    def reset_zoom(self) -> None:
        self.resetTransform()

    def fit_all(self) -> None:
        """Center on the topology; zoom out (never in past 100%) until it all fits."""
        items = self.tscene.device_items
        if not items:
            return
        rect = self.scene().itemsBoundingRect().adjusted(-40, -40, 40, 40)
        vp = self.viewport().rect()
        factor = min(vp.width() / max(rect.width(), 1), vp.height() / max(rect.height(), 1), 1.0)
        self.resetTransform()
        factor = max(factor, 0.2)
        self.scale(factor, factor)
        self.centerOn(rect.center())

    # -- drag & drop from the palette -------------------------------------------------
    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(MIME_MODEL):
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    dragMoveEvent = dragEnterEvent

    def dropEvent(self, e):
        if e.mimeData().hasFormat(MIME_MODEL):
            key = bytes(e.mimeData().data(MIME_MODEL)).decode()
            pos = self.mapToScene(e.position().toPoint())
            self.add_device(key, pos)
            e.acceptProposedAction()
        else:
            super().dropEvent(e)

    def add_device(self, model_key: str, pos: QPointF | None = None):
        if model_key not in MODELS:
            return None
        if pos is None:
            pos = self.mapToScene(self.viewport().rect().center())
        dev = self.tscene.topology.add_device(model_key, x=pos.x(), y=pos.y())
        self.status.emit(tr("Добавлено: {name} ({model})", name=dev.name, model=dev.model))
        return dev
