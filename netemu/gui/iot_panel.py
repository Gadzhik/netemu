"""IoT device tab: current value (editable for sensors, commanded for actuators) and the IoT server address."""
from __future__ import annotations

from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFormLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
                               QWidget)

from ..core.addresses import is_valid_ip
from ..i18n import tr

CHOICES = {"motion": ("no", "yes"), "smoke": ("no", "yes"), "lamp": ("off", "on"), "fan": ("off", "on"),
           "door": ("closed", "open")}


class IotPanel(QWidget):
    def __init__(self, dialog):
        super().__init__()
        self.dialog = dialog
        self.dev = dialog.device
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.kind = QLabel()
        form.addRow(tr("Тип:"), self.kind)
        if self.dev.dtype == "temperature":
            self.edit = QDoubleSpinBox()
            self.edit.setRange(-40, 120)
            self.edit.setSuffix(" °C")
            self.edit.valueChanged.connect(lambda v: self.dev.set_value(round(v, 1)))
        else:
            self.edit = QComboBox()
            self.edit.addItems(CHOICES.get(self.dev.dtype, ("off", "on")))
            self.edit.currentTextChanged.connect(self.dev.set_value)
        form.addRow(tr("Значение:"), self.edit)
        self.server = QLineEdit()
        self.server.setPlaceholderText("192.168.1.100")
        form.addRow(tr("IoT-сервер:"), self.server)
        lay.addLayout(form)
        save = QPushButton(tr("Применить"))
        save.clicked.connect(self.apply)
        lay.addWidget(save)
        hint = QLabel(tr("Подключите устройство к Wi-Fi (вкладка «Настройка»), укажите IoT-сервер. Датчики шлют "
                         "показания раз в 5 секунд, исполнители выполняют команды правил сервера."))
        hint.setWordWrap(True)
        lay.addWidget(hint)
        lay.addStretch(1)
        eng, fn = self.dev.engine, self._changed
        eng.subscribe("device_changed", fn)
        self.destroyed.connect(lambda *_: eng.unsubscribe("device_changed", fn))
        self.load()

    def _changed(self, dev, *rest):
        if dev is self.dev:
            try:
                self.load()
            except RuntimeError:
                pass

    def load(self) -> None:
        d = self.dev
        self.kind.setText(d.model)
        self.edit.blockSignals(True)
        if isinstance(self.edit, QDoubleSpinBox):
            self.edit.setValue(float(d.value))
        else:
            self.edit.setCurrentText(str(d.value))
        self.edit.setEnabled(d.is_sensor)
        self.edit.blockSignals(False)
        if not self.server.hasFocus():
            self.server.setText(d.server or "")

    def apply(self) -> bool:
        s = self.server.text().strip()
        if s and not is_valid_ip(s):
            return self.dialog.error(tr("Неверный адрес IoT-сервера."))
        self.dev.server = s or None
        self.dev.report()
        return True
