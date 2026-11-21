"""Main application window."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QSize, QTimer, Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (QDockWidget, QFileDialog, QInputDialog, QLabel, QLineEdit, QMainWindow, QMessageBox,
                               QToolBar)

from .. import __version__
from ..i18n import LANGUAGES, language, tr
from ..core import persistence
from ..core.addresses import IPv4Address
from ..core.samples import build_demo
from ..core.stack.icmp import PingListener, PingSession
from ..core.topology import Topology
from .canvas import TOOL_CABLE, TOOL_DELETE, TOOL_PDU, TOOL_SELECT, TopologyScene, TopologyView, TOOL_NOTE, TOOL_FRAME
from .controller import REALTIME, SIMULATION, SimController
from .device_dialog import DeviceDialog
from .icons import tool_icon
from .items import LinkItem
from .palette import DevicePalette
from .simulation_panel import PduDialog, SimulationPanel

FILE_FILTER = "Проекты NetEmu (*.netemu);;Все файлы (*)"  # translated at use


class _PduListener(PingListener):
    def __init__(self, window, src, dst):
        self.window, self.src, self.dst = window, src, dst

    def on_reply(self, seq, src, rtt, ttl, size):
        self.window.report_pdu(self.src, self.dst, True, tr("ответ за {ms} мс", ms=f"{rtt:.0f}"))

    def on_timeout(self, seq):
        self.window.report_pdu(self.src, self.dst, False, tr("таймаут"))

    def on_send_fail(self, seq):
        self.window.report_pdu(self.src, self.dst, False, tr("нет маршрута или IP-адреса у источника"))

    def on_error(self, seq, kind, src):
        what = tr("узел недоступен") if kind == "unreachable" else tr("истёк TTL")
        self.window.report_pdu(self.src, self.dst, False, tr("{what} (сообщил {src})", what=what, src=src))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.topology = Topology()
        self._help = None
        self.language_pending = None
        self.ctl = SimController(self.topology, self)
        self.scene = TopologyScene(self.ctl, self)
        self.view = TopologyView(self.scene, self)
        self.setCentralWidget(self.view)
        self.dialogs: dict[str, DeviceDialog] = {}
        self.pdu_results: list[tuple[str, str, bool, str]] = []
        self.file_path: Path | None = None
        self._saved_snapshot = self._snapshot()

        self.palette_dock = QDockWidget(tr("Устройства"), self)
        self.palette = DevicePalette()
        self.palette.modelActivated.connect(lambda key: self.view.add_device(key))
        self.palette_dock.setWidget(self.palette)
        self.palette_dock.setObjectName("palette")
        self.addDockWidget(Qt.LeftDockWidgetArea, self.palette_dock)

        self.sim_dock = QDockWidget(tr("Режим симуляции"), self)
        self.sim_panel = SimulationPanel(self.ctl)
        self.sim_panel.filterChanged.connect(self._filter_changed)
        self.sim_dock.setWidget(self.sim_panel)
        self.sim_dock.setObjectName("simulation")
        self.addDockWidget(Qt.RightDockWidgetArea, self.sim_dock)
        self.sim_dock.hide()

        from .lab_panel import LabPanel
        self.lab_dock = QDockWidget(tr("Задание"), self)
        self.lab_panel = LabPanel(self)
        self.lab_dock.setWidget(self.lab_panel)
        self.lab_dock.setObjectName("lab")
        self.addDockWidget(Qt.RightDockWidgetArea, self.lab_dock)
        self.lab_dock.hide()

        self.time_label = QLabel()
        self.statusBar().addPermanentWidget(self.time_label)
        self._build_actions()
        self.view.status.connect(lambda s: self.statusBar().showMessage(s, 8000))
        self.view.toolChanged.connect(self._tool_changed)
        self.view.pduRequested.connect(self.send_pdu)
        self.scene.deviceActivated.connect(self.open_device)
        self.scene.packetClicked.connect(self._packet_clicked)
        self.ctl.deviceRemoved.connect(self._device_removed)
        self.ctl.deviceChanged.connect(self._device_changed)
        self.ctl.tick.connect(self._update_time)
        self.ctl.modeChanged.connect(self._mode_changed)
        self.resize(1280, 800)
        self._update_title()
        self.topology.undo.on_pending = self._schedule_commit
        self.statusBar().showMessage(tr("Перетащите устройства из палитры слева, соедините их инструментом "
                                        "«Кабель» и дважды щёлкните устройство для настройки. F1 — справка."))

    # -- actions ---------------------------------------------------------------------
    def _act(self, text, slot=None, shortcut=None, icon=None, checkable=False) -> QAction:
        text = tr(text)
        a = QAction(text, self)
        if icon:
            a.setIcon(tool_icon(icon))
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        a.setCheckable(checkable)
        if slot:
            a.triggered.connect(slot)
        return a

    def _build_actions(self) -> None:
        mb = self.menuBar()
        m = mb.addMenu(tr("&Файл"))
        self.act_new = self._act("&Новый", self.new_project, QKeySequence.New)
        self.act_open = self._act("&Открыть…", self.open_dialog, QKeySequence.Open)
        self.act_save = self._act("&Сохранить", self.save, QKeySequence.Save)
        self.act_save_as = self._act("Сохранить &как…", self.save_as, QKeySequence.SaveAs)
        self.act_demo = self._act("Открыть &демо-топологию", self.load_demo)
        self.act_export = self._act("&Экспорт схемы (PNG, SVG)…", self.export_dialog, "Ctrl+E")
        self.act_quit = self._act("В&ыход", self.close, QKeySequence.Quit)
        for a in (self.act_new, self.act_open, self.act_save, self.act_save_as):
            m.addAction(a)
        m.addSeparator()
        m.addAction(self.act_export)
        m.addSeparator()
        m.addAction(self.act_demo)
        m.addSeparator()
        m.addAction(self.act_quit)

        m = mb.addMenu(tr("&Правка"))
        self.act_undo = self._act("&Отменить", self.undo, QKeySequence.Undo)
        self.act_redo = self._act("&Повторить", self.redo, QKeySequence.Redo)
        self.act_redo.setShortcuts([QKeySequence.Redo, QKeySequence("Ctrl+Y")])
        m.addAction(self.act_undo)
        m.addAction(self.act_redo)
        m.addSeparator()
        self.act_delete = self._act("&Удалить выделенное",
                                    lambda: self.scene.delete_items(self.scene.selectedItems()))
        self.act_select_all = self._act("Выделить &всё", self._select_all, QKeySequence.SelectAll)
        m.addAction(self.act_delete)
        m.addAction(self.act_select_all)

        tools = QActionGroup(self)
        self.act_tool_select = self._act("Выбор", lambda: self.view.set_tool(TOOL_SELECT), "S", "select", True)
        self.act_tool_cable = self._act("Кабель", lambda: self.view.set_tool(TOOL_CABLE), "C", "cable", True)
        self.act_tool_delete = self._act("Удалить", lambda: self.view.set_tool(TOOL_DELETE), "D", "delete", True)
        self.act_tool_pdu = self._act("Простой PDU", lambda: self.view.set_tool(TOOL_PDU), "P", "pdu", True)
        self.act_tool_note = self._act("Заметка", lambda: self.view.set_tool(TOOL_NOTE), "N", "note", True)
        self.act_tool_frame = self._act("Рамка", lambda: self.view.set_tool(TOOL_FRAME), "F", "frame", True)
        for a in (self.act_tool_select, self.act_tool_cable, self.act_tool_delete, self.act_tool_pdu,
                  self.act_tool_note, self.act_tool_frame):
            tools.addAction(a)
        self.act_tool_select.setChecked(True)

        m = mb.addMenu(tr("&Инструменты"))
        for a in tools.actions():
            m.addAction(a)

        m = mb.addMenu(tr("&Вид"))
        self.act_labels = self._act("Подписи портов", self._toggle_labels, checkable=True)
        self.act_labels.setChecked(True)
        m.addAction(self.act_labels)
        m.addAction(self._act("Увеличить", lambda: self.view.zoom(1.2), QKeySequence.ZoomIn))
        m.addAction(self._act("Уменьшить", lambda: self.view.zoom(1 / 1.2), QKeySequence.ZoomOut))
        m.addAction(self._act("Масштаб 100%", self.view.reset_zoom, "Ctrl+0"))
        self.act_fit = self._act("Показать всю схему", self.center_view, "Ctrl+9")
        m.addAction(self.act_fit)
        from . import theme
        tm = m.addMenu(tr("Тема"))
        self.theme_group = QActionGroup(self)
        for code, title in theme.THEMES.items():
            a = QAction(tr(title), self, checkable=True)
            a.setChecked(code == theme.STATE["theme"])
            a.triggered.connect(lambda _=False, c=code: self.choose_theme(c))
            self.theme_group.addAction(a)
            tm.addAction(a)
        sm = m.addMenu(tr("Масштаб интерфейса"))
        self.scale_group = QActionGroup(self)
        for f in theme.SCALES:
            a = QAction(f"{round(f * 100)}%", self, checkable=True)
            a.setChecked(abs(f - theme.STATE["scale"]) < 0.01)
            a.triggered.connect(lambda _=False, v=f: self.choose_scale(v))
            self.scale_group.addAction(a)
            sm.addAction(a)
        lang_menu = m.addMenu(tr("Язык / Language"))
        self.lang_group = QActionGroup(self)
        for code, title in LANGUAGES.items():
            a = QAction(title, self, checkable=True)
            a.setData(code)
            a.setChecked(code == language())
            a.triggered.connect(lambda _=False, c=code: self.choose_language(c))
            self.lang_group.addAction(a)
            lang_menu.addAction(a)
        m.addSeparator()
        m.addAction(self.palette_dock.toggleViewAction())

        modes = QActionGroup(self)
        self.act_realtime = self._act("Реальное время", lambda: self.ctl.set_mode(REALTIME),
                                      "Ctrl+R", "realtime", True)
        self.act_simulation = self._act("Симуляция", lambda: self.ctl.set_mode(SIMULATION),
                                        "Ctrl+M", "simulation", True)
        modes.addAction(self.act_realtime)
        modes.addAction(self.act_simulation)
        self.act_realtime.setChecked(True)
        m = mb.addMenu(tr("&Симуляция"))
        m.addAction(self.act_realtime)
        m.addAction(self.act_simulation)
        m.addSeparator()
        self.act_step = self._act("Шаг", self.sim_panel.step, "F10")
        self.act_play = self._act("Воспроизвести / пауза",
                                  lambda: self.ctl.set_playing(not self.ctl.playing), "F5")
        self.act_sim_reset = self._act("Сброс симуляции", self.ctl.reset_simulation)
        for a in (self.act_step, self.act_play, self.act_sim_reset):
            m.addAction(a)

        m = mb.addMenu(tr("&Задание"))
        from .lab_panel import builtin_labs
        ready = m.addMenu(tr("&Готовые лабораторные"))
        for title, path, kind in builtin_labs():
            ready.addAction(("🔧 " if kind == "troubleshoot" else "") + title,
                            lambda p=path: self.open_builtin_lab(p))
        m.addSeparator()
        self.act_lab_show = self._act("Показать &задание", self.show_lab)
        self.act_lab_check = self._act("&Проверить задание", self.check_lab, "F6")
        self.act_lab_report = self._act("Сохранить &отчёт…", lambda: self.lab_panel.save_report())
        for a in (self.act_lab_show, self.act_lab_check, self.act_lab_report):
            m.addAction(a)
        m.addSeparator()
        self.act_teacher = self._act("Режим &преподавателя", self.toggle_teacher, checkable=True)
        self.act_lab_edit = self._act("&Редактировать задание…", self.edit_lab)
        self.act_lab_solve = self._act("Применить решение", self.apply_solution)
        self.act_lab_fault = self._act("Внести случайную &неисправность", self.plant_fault)
        for a in (self.act_teacher, self.act_lab_edit, self.act_lab_solve, self.act_lab_fault):
            m.addAction(a)
        self.teacher = False
        self._sync_teacher_actions()

        m = mb.addMenu(tr("&Класс"))
        self.act_class_open = self._act("Открыть класс и раздать задание…", self.class_open)
        self.act_class_resend = self._act("Раздать задание заново", self.class_resend)
        self.act_class_report = self._act("Сохранить отчёт класса…", self.class_report)
        self.act_class_join = self._act("Подключиться к классу…", self.class_join)
        self.act_class_leave = self._act("Закрыть / покинуть класс", self.class_leave)
        for a in (self.act_class_open, self.act_class_resend, self.act_class_report):
            m.addAction(a)
        m.addSeparator()
        for a in (self.act_class_join, self.act_class_leave):
            m.addAction(a)
        self.teacher_class = self.student_class = None
        self.roster_dock = None

        m = mb.addMenu(tr("&Совместная работа"))
        self.act_collab_host = self._act("Начать сессию…", self.collab_host)
        self.act_collab_join = self._act("Подключиться…", self.collab_join)
        self.act_collab_stop = self._act("Завершить", self.collab_stop)
        for a in (self.act_collab_host, self.act_collab_join, self.act_collab_stop):
            m.addAction(a)
        self.act_collab_stop.setEnabled(False)
        self.collab = None

        m = mb.addMenu(tr("&Справка"))
        self.act_help = self._act("Руководство и команды", lambda: self.show_help(), QKeySequence.HelpContents)
        m.addAction(self.act_help)
        m.addSeparator()
        from .updater import UpdateChecker
        m.addAction(self._act("Проверить обновления…", self.check_updates))
        self.act_auto_update = self._act("Проверять обновления при запуске", UpdateChecker.set_enabled, checkable=True)
        self.act_auto_update.setChecked(UpdateChecker.enabled())
        m.addAction(self.act_auto_update)
        m.addSeparator()
        m.addAction(self._act("О программе", self.about))
        self.updater = None

        tb = QToolBar(tr("Инструменты"))
        tb.setObjectName("tools")
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        for a in tools.actions():
            tb.addAction(a)
        tb.addSeparator()
        tb.addAction(self.act_realtime)
        tb.addAction(self.act_simulation)
        self.addToolBar(tb)

    # -- tools / modes ---------------------------------------------------------------
    def _tool_changed(self, tool: str) -> None:
        {TOOL_SELECT: self.act_tool_select, TOOL_CABLE: self.act_tool_cable,
         TOOL_DELETE: self.act_tool_delete, TOOL_PDU: self.act_tool_pdu, TOOL_NOTE: self.act_tool_note,
         TOOL_FRAME: self.act_tool_frame}[tool].setChecked(True)

    def _mode_changed(self, mode: str) -> None:
        sim = mode == SIMULATION
        self.sim_dock.setVisible(sim)
        (self.act_simulation if sim else self.act_realtime).setChecked(True)
        self.statusBar().showMessage(
            tr("Режим симуляции: время остановлено, используйте «Шаг» или «Воспроизвести».") if sim
            else tr("Режим реального времени."), 8000)
        self.scene.update_packets()

    def _filter_changed(self, protocols) -> None:
        self.scene.visible_protocols = set(protocols)
        self.scene.update_packets()

    def _toggle_labels(self, on: bool) -> None:
        LinkItem.show_port_labels = on
        self.scene.refresh_links()

    def _select_all(self) -> None:
        for it in self.scene.items():
            it.setSelected(True)

    def _update_time(self) -> None:
        e = self.ctl.engine
        mode = tr("Симуляция") if self.ctl.mode == SIMULATION else tr("Реальное время")
        self.time_label.setText(tr("{mode} · {sec} с", mode=mode, sec=f"{e.now / 1000:,.1f}".replace(",", " ")))

    # -- devices -----------------------------------------------------------------------
    def open_device(self, dev) -> DeviceDialog:
        dlg = self.dialogs.get(dev.id)
        if dlg is None:
            dlg = DeviceDialog(dev, self.topology, self)
            self.dialogs[dev.id] = dlg
            # F1 in a device window opens the help page of that vendor
            from .help_window import topic_for_device
            dlg.help_hook = lambda d=dev: self.show_help(topic_for_device(d))
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        return dlg

    def _device_removed(self, dev) -> None:
        dlg = self.dialogs.pop(dev.id, None)
        if dlg is not None:
            dlg.close()
            dlg.deleteLater()

    def _device_changed(self, dev) -> None:
        dlg = self.dialogs.get(dev.id)
        if dlg is not None:
            dlg.update_title()
            dlg.refresh()

    def close_dialogs(self) -> None:
        for dlg in self.dialogs.values():
            dlg.close()
            dlg.deleteLater()
        self.dialogs.clear()

    def _packet_clicked(self, ev) -> None:
        info = ev.info
        PduDialog(info["frame"], tr("В пути: {a} → {b}", a=f"{info['src'].device.name} {info['src'].name}", b=
                     f"{info['dst'].device.name} {info['dst'].name}"), self).show()

    # -- simple PDU ----------------------------------------------------------------------
    def send_pdu(self, src, dst) -> PingSession | None:
        if not hasattr(src, "originate") or (src.category == "switch" and not any(p.ip for p in src.ports.values())):
            self.statusBar().showMessage(tr("{name} не умеет отправлять IP-пакеты (L2-устройство).", name=src.name), 8000)
            return None
        targets = [p.ip.ip for p in dst.ports.values() if p.ip is not None]
        if not targets:
            self.statusBar().showMessage(tr("У {name} нет IP-адреса.", name=dst.name), 8000)
            return None
        target = targets[0]
        for p in dst.ports.values():
            if p.ip is not None and p.is_up:
                target = p.ip.ip
                break
        self.statusBar().showMessage(f"PDU: ICMP echo {src.name} → {dst.name} ({target})", 8000)
        return PingSession(src, IPv4Address(target), _PduListener(self, src.name, dst.name),
                           count=1, size=32).start()

    def report_pdu(self, src: str, dst: str, ok: bool, detail: str) -> None:
        self.pdu_results.append((src, dst, ok, detail))
        verdict = tr("Успешно") if ok else tr("Неудачно")
        self.statusBar().showMessage(f"PDU {src} → {dst}: {verdict} ({detail})", 15000)

    # -- files ---------------------------------------------------------------------------
    def _snapshot(self) -> str:
        return json.dumps(persistence.topology_to_dict(self.topology), sort_keys=True)

    @property
    def dirty(self) -> bool:
        return self._snapshot() != self._saved_snapshot

    def _update_title(self) -> None:
        name = self.file_path.name if self.file_path else tr("Без имени")
        self.setWindowTitle(f"{name} — NetEmu")

    def maybe_save(self) -> bool:
        """Ask to save unsaved changes. Returns False if the user cancelled."""
        if not self.dirty:
            return True
        r = QMessageBox.question(self, "NetEmu", tr("Сохранить изменения в проекте?"),
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Cancel:
            return False
        if r == QMessageBox.Save:
            return self.save()
        return True

    def _reset(self) -> None:
        self.close_dialogs()
        self.ctl.set_mode(REALTIME)
        self.topology.clear()
        self.ctl.engine.clear()
        self.ctl.clear_records()

    def new_project(self) -> None:
        if not self.maybe_save():
            return
        self._reset()
        self.topology.undo.reset()
        self.scene.load_annotations()
        self.teacher = False
        self._sync_teacher_actions()
        self.lab_panel.load()
        self.lab_dock.hide()
        self.file_path = None
        self._saved_snapshot = self._snapshot()
        self._update_title()

    def open_dialog(self) -> None:
        if not self.maybe_save():
            return
        path, _ = QFileDialog.getOpenFileName(self, tr("Открыть проект"), "", tr(FILE_FILTER))
        if path:
            self.open_file(path)

    def open_file(self, path) -> bool:
        path = Path(path)
        self._reset()
        try:
            persistence.load(path, self.topology)
        except (OSError, ValueError, KeyError) as ex:
            QMessageBox.critical(self, tr("Ошибка"), tr("Не удалось открыть {name}:\n{err}", name=path.name, err=ex))
            self.topology.clear()
            return False
        self.file_path = path
        self._saved_snapshot = self._snapshot()
        self._update_title()
        self.center_view()
        self.topology.undo.reset()
        self.scene.load_annotations()
        self.teacher = False
        self._sync_teacher_actions()
        self.lab_panel.load()
        if self.topology.lab is not None:
            self.lab_dock.show()
        self.statusBar().showMessage(tr("Открыт {path}", path=path), 5000)
        return True

    def load_demo(self) -> None:
        if not self.maybe_save():
            return
        self._reset()
        build_demo(self.topology)
        self.topology.undo.reset()
        self.center_view()
        self.file_path = None
        self._saved_snapshot = self._snapshot()
        self._update_title()
        self.statusBar().showMessage(tr("Демо: PC0 (192.168.1.10) ⟷ PC1 (192.168.2.10) через Cisco, MikroTik, "
                                        "Juniper и Huawei. Откройте PC0 → «Командная строка» → ping 192.168.2.10"),
                                     20000)

    def center_view(self) -> None:
        self.view.fit_all()

    def save(self) -> bool:
        if self.file_path is None:
            return self.save_as()
        return self.save_to(self.file_path)

    def save_as(self) -> bool:
        path, _ = QFileDialog.getSaveFileName(self, tr("Сохранить проект"), "topology.netemu", tr(FILE_FILTER))
        if not path:
            return False
        if not path.lower().endswith(".netemu"):
            path += ".netemu"
        return self.save_to(Path(path))

    def save_to(self, path: Path) -> bool:
        try:
            persistence.save(self.topology, path)
        except OSError as ex:
            QMessageBox.critical(self, tr("Ошибка"), tr("Не удалось сохранить:\n{err}", err=ex))
            return False
        self.file_path = Path(path)
        self._saved_snapshot = self._snapshot()
        self._update_title()
        self.statusBar().showMessage(tr("Сохранено в {path}", path=path), 5000)
        return True

    # -- export ----------------------------------------------------------------------------
    def export_dialog(self) -> None:
        base = (self.file_path.stem if self.file_path else "topology")
        path, flt = QFileDialog.getSaveFileName(self, tr("Экспорт схемы"), f"{base}.png",
                                                "PNG (*.png);;SVG (*.svg)")
        if path:
            self.export_image(path)

    def export_image(self, path) -> bool:
        """Render the canvas (devices, cables, notes; no packets or selection) to PNG or SVG."""
        from PySide6.QtCore import QRectF, QSize
        from PySide6.QtGui import QColor, QImage, QPainter
        path = str(path)
        scene = self.scene
        selected = scene.selectedItems()
        for it in selected:
            it.setSelected(False)
        packets = list(scene.packet_items.values())
        for it in packets:
            it.setVisible(False)
        rect = scene.itemsBoundingRect().adjusted(-40, -40, 40, 40)
        if rect.isEmpty():
            rect = QRectF(0, 0, 400, 300)
        try:
            if path.lower().endswith(".svg"):
                from PySide6.QtSvg import QSvgGenerator
                gen = QSvgGenerator()
                gen.setFileName(path)
                gen.setSize(QSize(int(rect.width()), int(rect.height())))
                gen.setViewBox(QRectF(0, 0, rect.width(), rect.height()))
                gen.setTitle(self.file_path.stem if self.file_path else "NetEmu topology")
                p = QPainter(gen)
                scene.render(p, QRectF(0, 0, rect.width(), rect.height()), rect)
                p.end()
                ok = Path(path).exists()
            else:
                if not path.lower().endswith(".png"):
                    path += ".png"
                img = QImage(int(rect.width() * 2), int(rect.height() * 2), QImage.Format_ARGB32)
                img.fill(QColor("white"))
                p = QPainter(img)
                p.setRenderHint(QPainter.Antialiasing)
                p.setRenderHint(QPainter.TextAntialiasing)
                scene.render(p, QRectF(0, 0, img.width(), img.height()), rect)
                p.end()
                ok = img.save(path)
        finally:
            for it in packets:
                it.setVisible(True)
            for it in selected:
                it.setSelected(True)
        if ok:
            self.statusBar().showMessage(tr("Схема сохранена в {path}", path=path), 6000)
        else:
            QMessageBox.critical(self, tr("Ошибка"), tr("Не удалось сохранить:\n{err}", err=path))
        return ok

    # -- lab assignments -------------------------------------------------------------------
    def show_lab(self) -> None:
        self.lab_panel.load()
        self.lab_dock.show()
        self.lab_dock.raise_()

    def check_lab(self):
        self.show_lab()
        return self.lab_panel.run_checks()

    def edit_lab(self) -> None:
        from .lab_panel import LabEditor
        lab = self.topology.lab
        if lab is not None and lab.password is not None and not self.teacher:
            if not self.toggle_teacher(True):
                return
        LabEditor(self).exec()
        self._sync_teacher_actions()
        self.show_lab()

    def open_builtin_lab(self, path) -> None:
        """Open a ready-made lab as a new, unsaved project (the shipped file stays untouched)."""
        if not self.maybe_save():
            return
        if self.open_file(path):
            self.file_path = None
            self._saved_snapshot = None
            self._update_title()
            self.show_lab()

    def toggle_teacher(self, on: bool) -> bool:
        lab = self.topology.lab
        if on and lab is not None and lab.password is not None and lab.locked:
            pw, ok = QInputDialog.getText(self, tr("Режим преподавателя"), tr("Пароль преподавателя:"),
                                          QLineEdit.Password)
            if not ok or not lab.unlock(pw):
                if ok:
                    QMessageBox.warning(self, tr("Режим преподавателя"), tr("Неверный пароль."))
                on = False
        if not on and lab is not None and self.teacher:
            lab.lock()
        self.teacher = on
        self._sync_teacher_actions()
        self.lab_panel.load()
        return on

    def _sync_teacher_actions(self) -> None:
        self.act_teacher.setChecked(self.teacher)
        lab = self.topology.lab
        protected = lab is not None and lab.password is not None
        self.act_lab_edit.setEnabled(self.teacher or not protected)
        self.act_lab_solve.setEnabled(self.teacher and lab is not None and bool(lab.script))
        self.act_lab_fault.setEnabled(self.teacher or lab is None)

    def apply_solution(self) -> None:
        from ..core.lab import apply_script
        lab = self.topology.lab
        if lab is None or not lab.script:
            return
        apply_script(self.topology, lab.script)
        self.scene.refresh_all() if hasattr(self.scene, "refresh_all") else None
        self.check_lab()

    def plant_fault(self) -> None:
        import random
        from ..core.lab import Lab, plant_fault
        desc = plant_fault(self.topology, random.Random())
        if desc is None:
            QMessageBox.information(self, tr("Неисправность"), tr("В схеме нечего сломать: нужны настроенные "
                                                                   "устройства и кабели."))
            return
        lab = self.topology.lab
        if lab is None:
            lab = self.topology.lab = Lab(tr("Поиск неисправностей"), tr("В сети есть неисправность. Найдите и "
                                                                          "устраните её."), kind="troubleshoot")
        lab.faults.append(desc)
        if not self.teacher:
            self.teacher = True
        self._sync_teacher_actions()
        self.lab_panel.load()
        self.statusBar().showMessage(tr("Внесена неисправность (видна в решении)"), 6000)

    # -- undo / redo -------------------------------------------------------------------------
    def _schedule_commit(self) -> None:
        if not getattr(self, "_commit_pending", False):
            self._commit_pending = True
            QTimer.singleShot(0, self._commit_undo)

    def _commit_undo(self) -> None:
        self._commit_pending = False
        self.topology.undo.commit()

    def undo(self) -> None:
        if self.topology.undo.undo():
            self.statusBar().showMessage(tr("Отменено"), 3000)

    def redo(self) -> None:
        if self.topology.undo.redo():
            self.statusBar().showMessage(tr("Повторено"), 3000)

    def check_updates(self) -> None:
        from .updater import install
        if self.updater is None:
            self.updater = install(self, auto=False)
        self.updater.check(manual=True)

    def about(self) -> None:
        QMessageBox.about(self, tr("О программе"),
                          f"<h3>NetEmu {__version__}</h3>"
                          + tr("<p>Учебный симулятор компьютерных сетей.</p><p>Устройства Cisco, MikroTik, Juniper, "
                               "Huawei и Extreme с CLI соответствующих производителей, ПК с командной строкой Windows и "
                               "Linux, пошаговая симуляция пакетов, лабораторные работы с автопроверкой.</p>")
                          + tr("<p>Свободное ПО: GNU General Public License версии 3 или более поздней. "
                               "Программа распространяется без каких-либо гарантий.</p>")
                          + '<p><a href="https://github.com/Gadzhik/netemu">github.com/Gadzhik/netemu</a></p>')

    # -- help / language ------------------------------------------------------------------
    def show_help(self, topic: str | None = None):
        from PySide6.QtWidgets import QApplication

        from .help_window import HelpWindow, topic_for_device
        active = QApplication.activeWindow()
        if topic is None and isinstance(active, DeviceDialog):   # F1 over a device window
            topic = topic_for_device(active.device)
        if self._help is None:
            self._help = HelpWindow(self)
        self._help.show_topic(topic)
        self._help.show()
        self._help.raise_()
        self._help.activateWindow()
        return self._help

    # -- classroom mode ---------------------------------------------------------------------------
    def class_open(self, port: int | None = None) -> bool:
        from .classroom import CLASS_PORT, RosterPanel, TeacherClass
        from .collab import local_addresses
        lab = self.topology.lab
        if lab is None or not lab.checks:
            QMessageBox.information(self, tr("Класс"), tr("Сначала откройте или создайте задание с проверками."))
            return False
        if port is None:
            port, ok = QInputDialog.getInt(self, tr("Класс"), tr("TCP-порт класса:"), CLASS_PORT, 1024, 65535)
            if not ok:
                return False
        self.class_leave()
        data = self._assignment()
        t = TeacherClass(data, lab.title)
        if not t.start(port):
            QMessageBox.warning(self, tr("Класс"), tr("Не удалось открыть порт {port}.", port=port))
            return False
        self.teacher_class = t
        self.roster_dock = QDockWidget(tr("Класс"), self)
        self.roster_dock.setObjectName("classroom")
        self.roster_dock.setWidget(RosterPanel(t))
        self.addDockWidget(Qt.RightDockWidgetArea, self.roster_dock)
        if self.isVisible():
            QMessageBox.information(self, tr("Класс"), tr(
                "Класс открыт. Ученики выбирают «Класс → Подключиться к классу…» и вводят:\n\nадрес: {addr}\n"
                "порт: {port}\nPIN: {pin}", addr=", ".join(local_addresses()), port=port, pin=t.pin))
        return True

    def _assignment(self) -> dict:
        """The project as handed out: the teacher's solution, script and attempts stay here."""
        data = persistence.topology_to_dict(self.topology)
        lab = dict(data.get("lab") or {})
        for k in ("solution", "script", "faults", "attempts", "secret", "password"):
            lab.pop(k, None)
        data["lab"] = lab
        return data

    def class_resend(self) -> None:
        if self.teacher_class is not None and self.topology.lab is not None:
            self.teacher_class.redistribute(self._assignment(), self.topology.lab.title)

    def class_report(self, path: str | None = None) -> str | None:
        if self.teacher_class is None:
            return None
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, tr("Сохранить отчёт класса"), "class-report.html", "HTML (*.html)")
            if not path:
                return None
        Path(path).write_text(self.teacher_class.roster.report_html(), encoding="utf-8")
        return path

    def class_join(self, address: str | None = None, port: int | None = None, pin: str | None = None,
                   name: str | None = None) -> None:
        from .classroom import CLASS_PORT, StudentClass
        if address is None:
            text, ok = QInputDialog.getText(self, tr("Класс"), tr("Адрес преподавателя (IP или IP:порт):"))
            if not ok or not text.strip():
                return
            host, _, p = text.strip().partition(":")
            address, port = host, int(p) if p.isdigit() else CLASS_PORT
            pin, ok = QInputDialog.getText(self, tr("Класс"), tr("PIN класса:"))
            if not ok:
                return
            name, ok = QInputDialog.getText(self, tr("Класс"), tr("Фамилия и имя:"))
            if not ok:
                return
        if not self.maybe_save():
            return
        self.class_leave()
        s = StudentClass((name or "").strip() or "Student")
        s.assigned.connect(self._class_assigned)
        s.status.connect(lambda st: self.statusBar().showMessage(
            {"joined": tr("Вы в классе: {name}", name=s.name), "lost": tr("Связь с классом потеряна"),
             "error": tr("Не удалось подключиться к классу")}.get(st, st), 8000))
        s.join(address, port or CLASS_PORT, (pin or "").strip())
        self.student_class = s

    def _class_assigned(self, data: dict) -> None:
        self._reset()
        persistence.topology_from_dict(data, self.topology)
        self.topology.undo.reset()
        self.scene.load_annotations()
        self.file_path = None
        self._saved_snapshot = self._snapshot()
        self._update_title()
        self.center_view()
        self.teacher = False
        self._sync_teacher_actions()
        self.show_lab()

    def class_leave(self) -> None:
        if self.teacher_class is not None:
            self.teacher_class.stop()
            self.teacher_class = None
        if self.roster_dock is not None:
            self.removeDockWidget(self.roster_dock)
            self.roster_dock.deleteLater()
            self.roster_dock = None
        if self.student_class is not None:
            self.student_class.stop()
            self.student_class = None

    # -- LAN collaboration ----------------------------------------------------------------------
    def _collab(self):
        if self.collab is None:
            from PySide6.QtCore import QSysInfo
            from .collab import CollabSession
            self.collab = CollabSession(self.topology, QSysInfo.machineHostName() or "NetEmu")
            self.collab.statusChanged.connect(self._collab_status)
            self.collab.remoteApplied.connect(self._collab_applied)
            self.collab_label = QLabel()
            self.statusBar().addPermanentWidget(self.collab_label)
        return self.collab

    def collab_host(self, port: int | None = None) -> bool:
        from .collab import DEFAULT_PORT, local_addresses
        if port is None:
            port, ok = QInputDialog.getInt(self, tr("Совместная работа"), tr("TCP-порт сессии:"), DEFAULT_PORT,
                                           1024, 65535)
            if not ok:
                return False
        c = self._collab()
        if not c.host(port):
            return False
        if self.isVisible():
            QMessageBox.information(self, tr("Совместная работа"), tr(
                "Сессия открыта. Участники подключаются через «Совместная работа → Подключиться…»:\n\n"
                "адрес: {addr}\nпорт: {port}\nPIN: {pin}\n\nКаждый участник видит общую схему и конфигурации, "
                "а симуляцию запускает у себя.", addr=", ".join(local_addresses()), port=port, pin=c.pin))
        return True

    def collab_join(self, address: str | None = None, port: int | None = None, pin: str | None = None) -> None:
        from .collab import DEFAULT_PORT
        if address is None:
            text, ok = QInputDialog.getText(self, tr("Совместная работа"), tr("Адрес ведущего (IP или IP:порт):"))
            if not ok or not text.strip():
                return
            host, _, p = text.strip().partition(":")
            address, port = host, int(p) if p.isdigit() else DEFAULT_PORT
            pin, ok = QInputDialog.getText(self, tr("Совместная работа"), tr("PIN сессии:"))
            if not ok:
                return
        if not self.maybe_save():
            return
        self._collab().join(address, port or DEFAULT_PORT, (pin or "").strip())

    def collab_stop(self) -> None:
        if self.collab is not None:
            self.collab.stop()

    def _collab_status(self, st: str) -> None:
        active = self.collab is not None and self.collab.active
        self.act_collab_stop.setEnabled(active)
        if st.startswith("host:"):
            _, n, names = st.split(":", 2)
            text = tr("Сессия (PIN {pin}): участников {n}", pin=self.collab.pin, n=n) + (f" · {names}" if names else "")
        elif st == "joined":
            text = tr("Подключено к сессии")
        elif st == "connecting":
            text = tr("Подключение…")
        elif st == "lost":
            text = tr("Связь с сессией потеряна")
        elif st.startswith("error"):
            text = tr("Ошибка сессии: {e}", e=st.partition(": ")[2])
        else:
            text = ""
        self.collab_label.setText(text)

    def _collab_applied(self) -> None:
        self.scene.load_annotations()
        self.lab_panel.load()
        if self.topology.lab is not None and not self.lab_dock.isVisible() and self.topology.lab.checks:
            self.lab_dock.show()

    def choose_theme(self, code: str) -> None:
        from . import theme
        theme.apply_theme(code)
        theme.save_settings()

    def choose_scale(self, factor: float) -> None:
        from . import theme
        theme.apply_scale(factor)
        theme.save_settings()
        tb = self.findChild(QToolBar, "tools")
        if tb is not None:
            s = round(24 * factor)
            tb.setIconSize(QSize(s, s))

    def choose_language(self, code: str) -> None:
        from PySide6.QtCore import QSettings
        if code == language():
            return
        QSettings().setValue("language", code)
        self.language_pending = code
        QMessageBox.information(self, "NetEmu", tr("Язык интерфейса изменится после перезапуска NetEmu.")
                                if code == "ru" else
                                "The interface language will change after NetEmu is restarted.")

    def shutdown(self) -> None:
        """Release simulation and scene resources (safe to call twice)."""
        if getattr(self, "_shut", False):
            return
        self._shut = True
        if getattr(self, "collab", None) is not None:
            self.collab.stop()
        if hasattr(self, "teacher_class"):
            self.class_leave()
        self.close_dialogs()
        self.ctl.shutdown()
        self.scene.dispose()

    def closeEvent(self, e):
        if self.maybe_save():
            self.shutdown()
            e.accept()
        else:
            e.ignore()
