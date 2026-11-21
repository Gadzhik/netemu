"""Lab assignment panel (task text + automatic checks), teacher mode and the lab editor."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                               QPlainTextEdit, QProgressBar, QPushButton, QTabWidget, QTextBrowser, QVBoxLayout,
                               QWidget)

from ..core.lab import Lab, evaluate, parse_checks, record_attempt, report_html, script_to_text, text_to_script
from ..i18n import language, tr

PASS, FAIL, TODO = "✔", "✘", "•"
LABS_DIR = Path(__file__).resolve().parents[1] / "resources" / "labs"


def builtin_labs() -> list[tuple[str, Path, str]]:
    """[(title in the interface language, path, kind)] of the ready-made labs shipped with NetEmu."""
    out = []
    for p in sorted(LABS_DIR.glob("*.netemu")):
        try:
            lab = json.loads(p.read_text(encoding="utf-8")).get("lab") or {}
        except (OSError, ValueError):
            continue
        title = ((lab.get("translations") or {}).get(language()) or {}).get("title") or lab.get("title") or p.stem
        out.append((title, p, lab.get("kind", "lab")))
    return out


class LabPanel(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        lay = QVBoxLayout(self)
        self.title = QLabel()
        f = self.title.font()
        f.setPointSizeF(f.pointSizeF() * 1.25)
        f.setBold(True)
        self.title.setFont(f)
        self.title.setWordWrap(True)
        self.text = QTextBrowser()
        self.text.setOpenExternalLinks(True)
        self.solution = QTextBrowser()
        self.tabs = QTabWidget()
        self.tabs.addTab(self.text, tr("Условие"))
        self.checks = QListWidget()
        self.progress = QProgressBar()
        self.progress.setFormat(tr("Выполнено %v из %m"))
        self.check_btn = QPushButton(tr("Проверить"))
        self.check_btn.clicked.connect(self.run_checks)
        self.report_btn = QPushButton(tr("Сохранить отчёт…"))
        self.report_btn.clicked.connect(self.save_report)
        lay.addWidget(self.title)
        lay.addWidget(self.tabs, 3)
        lay.addWidget(QLabel(tr("Проверки:")))
        lay.addWidget(self.checks, 2)
        lay.addWidget(self.progress)
        row = QHBoxLayout()
        row.addWidget(self.check_btn, 1)
        row.addWidget(self.report_btn)
        lay.addLayout(row)
        self.load()

    @property
    def lab(self) -> Lab | None:
        return self.window.topology.lab

    def load(self) -> None:
        lab = self.lab
        self.checks.clear()
        teacher = getattr(self.window, "teacher", False)
        idx = self.tabs.indexOf(self.solution)
        if teacher and lab is not None and idx < 0:
            self.tabs.addTab(self.solution, tr("Решение"))
        elif (not teacher or lab is None) and idx >= 0:
            self.tabs.removeTab(idx)
        if lab is None:
            self.title.setText(tr("Задания нет"))
            self.text.setMarkdown(tr("Откройте готовую лабораторную (**Задание → Готовые лабораторные**) или "
                                     "создайте своё: **Задание → Редактировать задание…**"))
            self.progress.setMaximum(1)
            self.progress.setValue(0)
            self.check_btn.setEnabled(False)
            self.report_btn.setEnabled(False)
            return
        title, text, check_texts = lab.localized(language())
        prefix = tr("Поиск неисправностей: ") if lab.kind == "troubleshoot" and not title.startswith(
            tr("Поиск неисправностей")) else ""
        self.title.setText(prefix + (title or tr("Задание")))
        self.text.setMarkdown(text)
        if teacher:
            self.solution.setMarkdown(lab.solution_markdown() or tr("_Решение не записано._"))
        for t in check_texts:
            self.checks.addItem(QListWidgetItem(f"{TODO}  {t}"))
        self.progress.setMaximum(max(1, len(lab.checks)))
        self.progress.setValue(0)
        self.check_btn.setEnabled(bool(lab.checks))
        self.report_btn.setEnabled(bool(lab.checks))

    def run_checks(self) -> list:
        lab = self.lab
        if lab is None:
            return []
        results = evaluate(self.window.topology, lab)
        self.checks.clear()
        passed = 0
        texts = lab.localized(language())[2]
        for (c, ok, detail), text in zip(results, texts):
            it = QListWidgetItem(f"{PASS if ok else FAIL}  {text}")
            it.setToolTip(detail)
            it.setForeground(QColor("#2e7d32") if ok else QColor("#c62828"))
            self.checks.addItem(it)
            passed += ok
        self.progress.setValue(passed)
        sc = getattr(self.window, "student_class", None)
        if sc is not None:
            sc.report(results)                       # classroom mode: the teacher sees the progress
        pct = round(100 * passed / max(1, len(results)))
        self.window.statusBar().showMessage(tr("Проверка задания: {p} из {n} ({pct}%)", p=passed, n=len(results), pct=pct),
                                            10000)
        return results

    def save_report(self, student: str | None = None, path: str | None = None) -> str | None:
        lab = self.lab
        if lab is None:
            return None
        if student is None:
            student, ok = QInputDialog.getText(self, tr("Отчёт"), tr("Фамилия и имя:"))
            if not ok or not student.strip():
                return None
        results = self.run_checks()
        attempt = record_attempt(lab, student.strip(), results, language())
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, tr("Сохранить отчёт"),
                                                  f"{lab.localized(language())[0]} - {student.strip()}.html",
                                                  "HTML (*.html)")
            if not path:
                return None
        Path(path).write_text(report_html(lab, attempt, language()), encoding="utf-8")
        self.window.statusBar().showMessage(tr("Отчёт сохранён: {p}", p=path), 8000)
        return path


class LabEditor(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle(tr("Редактор задания"))
        self.resize(820, 720)
        lab = window.topology.lab or Lab()
        self.lab = lab
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.title = QLineEdit(lab.title)
        form.addRow(tr("Название:"), self.title)
        self.troubleshoot = QCheckBox(tr("Задача на поиск неисправностей"))
        self.troubleshoot.setChecked(lab.kind == "troubleshoot")
        form.addRow("", self.troubleshoot)
        lay.addLayout(form)
        tabs = QTabWidget()
        lay.addWidget(tabs, 1)

        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel(tr("Условие (Markdown):")))
        self.text = QPlainTextEdit(lab.text)
        v.addWidget(self.text, 3)
        hint = QLabel(tr("Проверки, по одной в строке: тип аргументы | описание. Типы: ping и noping <устройство> <адрес>, "
                         "route <устройство> <сеть>, ospf <устройство> <соседей>, hostname <устройство> <имя>, "
                         "vlan <устройство> <id>, access <коммутатор> <порт> <vlan>, ip <устройство> <адрес>, dhcp <ПК>, "
                         "nat <маршрутизатор>, ssh <устройство>, stproot <коммутатор>, fhrp <устройство> active|standby, "
                         "wifi <устройство> [SSID], bgp <устройство> <сессий>, "
                         "channel <коммутатор> <Po> [портов], psec <коммутатор> <порт>, phone <телефон>, "
                         "ospf6 <устройство> <соседей>, http <устройство> <URL>."))
        hint.setWordWrap(True)
        v.addWidget(hint)
        self.checks = QPlainTextEdit("\n".join(c.line() for c in lab.checks))
        self.checks.setPlaceholderText("ping PC0 192.168.2.10 | PC0 pings the server")
        v.addWidget(self.checks, 2)
        tabs.addTab(w, tr("Задание"))

        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel(tr("Решение для преподавателя (Markdown), ученику не показывается:")))
        self.solution = QPlainTextEdit(lab.solution)
        v.addWidget(self.solution, 1)
        sh = QLabel(tr("Сценарий решения: заголовок [имя устройства], под ним команды CLI. Для ПК и точек доступа: "
                       "@ip 192.168.1.10/24 gw 192.168.1.1, @dhcp, @ip6 2001:db8::10/64 gw 2001:db8::1, "
                       "@wifi SSID ключ, @ssid SSID key ключ. «Задание → Применить решение» выполняет сценарий."))
        sh.setWordWrap(True)
        v.addWidget(sh)
        self.script = QPlainTextEdit(script_to_text(lab.script))
        self.script.setPlaceholderText("[R1]\nenable\nconfigure terminal\n...")
        v.addWidget(self.script, 2)
        v.addWidget(QLabel(tr("Внесённые неисправности (по одной в строке):")))
        self.faults = QPlainTextEdit("\n".join(lab.faults))
        self.faults.setMaximumHeight(80)
        v.addWidget(self.faults)
        tabs.addTab(w, tr("Решение"))

        w = QWidget()
        f2 = QFormLayout(w)
        self.password = QLineEdit(lab._key or "")
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText(tr("пусто: без пароля"))
        f2.addRow(tr("Пароль преподавателя:"), self.password)
        note = QLabel(tr("С паролем решение, сценарий и список неисправностей хранятся в файле в зашифрованном виде, "
                         "а редактировать задание и смотреть решение можно только в режиме преподавателя."))
        note.setWordWrap(True)
        f2.addRow(note)
        self.attempts = QListWidget()
        for a in lab.attempts:
            self.attempts.addItem(f"{a['time']}  {a['student']}: {a['passed']}/{a['total']}")
        f2.addRow(tr("Попытки учеников:"), self.attempts)
        tabs.addTab(w, tr("Доступ и результаты"))

        self.errors = QLabel()
        self.errors.setStyleSheet("color: #c62828")
        lay.addWidget(self.errors)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.remove_btn = bb.addButton(tr("Удалить задание"), QDialogButtonBox.DestructiveRole)
        self.remove_btn.clicked.connect(self.remove)
        bb.accepted.connect(self.accept_lab)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def accept_lab(self) -> bool:
        checks, errors = parse_checks(self.checks.toPlainText())
        if errors:
            self.errors.setText("\n".join(errors))
            return False
        lab = self.lab
        lab.title, lab.text, lab.checks = self.title.text().strip(), self.text.toPlainText(), checks
        lab.kind = "troubleshoot" if self.troubleshoot.isChecked() else "lab"
        lab.solution = self.solution.toPlainText()
        lab.script = text_to_script(self.script.toPlainText())
        lab.faults = [x.strip() for x in self.faults.toPlainText().splitlines() if x.strip()]
        pw = self.password.text()
        if (pw or None) != lab._key:
            lab.set_password(pw or None)
        self.window.topology.lab = lab
        self.accept()
        return True

    def remove(self) -> None:
        if QMessageBox.question(self, tr("Удалить задание"), tr("Удалить задание из проекта?")) != QMessageBox.Yes:
            return
        self.window.topology.lab = None
        self.accept()


__all__ = ["LabPanel", "LabEditor", "builtin_labs", "Qt"]
