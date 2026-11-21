"""Console widget that hosts a vendor CLI session."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit


class TerminalWidget(QPlainTextEdit):
    commandExecuted = Signal(str)

    MAX_BLOCKS = 5000

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.history: list[str] = []
        self._hist_pos = 0
        self._input_start = 0
        self._job = None
        self._pending_lines: list[str] = []
        self._secret = ""
        self.apply_scale()
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.setUndoRedoEnabled(False)
        self.setMaximumBlockCount(self.MAX_BLOCKS)
        pal = self.palette()
        pal.setColor(QPalette.Base, QColor("#111417"))
        pal.setColor(QPalette.Text, QColor("#e0e0e0"))
        self.setPalette(pal)
        self.setCursorWidth(8)
        banner = session.banner()
        if banner:
            self._append(banner)
        self._show_prompt()

    def apply_scale(self) -> None:
        from . import theme
        fixed = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        font = QFont()
        font.setFamilies(["Consolas", "Menlo", "DejaVu Sans Mono", "Liberation Mono", fixed.family()])
        font.setStyleHint(QFont.Monospace)
        font.setPointSizeF(10 * theme.STATE["scale"])
        self.setFont(font)

    # -- output -------------------------------------------------------------------
    def _append(self, text: str) -> None:
        if not text:
            return
        c = self.textCursor()
        c.movePosition(QTextCursor.End)
        c.insertText(text.replace("\r", ""))
        self.setTextCursor(c)
        self._input_start = c.position()
        self.ensureCursorVisible()

    def _show_prompt(self) -> None:
        text = self.toPlainText()
        if text and not text.endswith("\n"):
            self._append("\n")
        self._append(self.cur.display_prompt())

    def current_input(self) -> str:
        return self.toPlainText()[self._input_start:]

    def _set_input(self, text: str) -> None:
        c = self.textCursor()
        c.setPosition(self._input_start)
        c.movePosition(QTextCursor.End, QTextCursor.KeepAnchor)
        c.insertText(text)
        self.setTextCursor(c)

    def console_message(self, text: str) -> None:
        """Asynchronous router message (e.g. %OSPF-5-ADJCHG): printed above the current input line."""
        if self.busy:
            self._append("\n" + text + "\n")
            return
        typed = self.current_input()
        c = self.textCursor()
        c.movePosition(QTextCursor.End)
        c.movePosition(QTextCursor.StartOfBlock, QTextCursor.KeepAnchor)
        c.removeSelectedText()
        self.setTextCursor(c)
        self._input_start = c.position()
        self._append(text + "\n\n")
        self._show_prompt()
        self._set_input(typed)

    @property
    def cur(self):
        """Session receiving input: the local one or the remote (telnet/ssh) session attached to it."""
        return self.session.active()

    @property
    def busy(self) -> bool:
        return self._job is not None and not self._job.done

    # -- executing ----------------------------------------------------------------------
    def run_command(self, line: str) -> None:
        """Execute *line* as if the user typed it and pressed Enter."""
        if self.busy:
            self._pending_lines.append(line)
            return
        self._set_input(line)
        self._submit()

    def _submit(self) -> None:
        cur = self.cur
        secret = cur.secret_input()
        if secret:
            line, self._secret = self._secret, ""
        else:
            line = self.current_input()
        self._append("\n")
        if line.strip() and not secret:
            self.history.append(line)
        self._hist_pos = len(self.history)
        job = cur.execute(line, self._append)
        if not secret:
            self.commandExecuted.emit(line)
        if getattr(cur, "wants_clear", False):
            cur.wants_clear = False
            self.clear()
            self._input_start = 0
        if job is not None and not job.done:
            self._job = job
            job.on_done(self._job_finished)
        else:
            self._after_command()

    def _job_finished(self) -> None:
        self._job = None
        self._after_command()

    def _after_command(self) -> None:
        self._show_prompt()
        if self._pending_lines:
            self.run_command(self._pending_lines.pop(0))

    # -- keyboard -----------------------------------------------------------------------
    def keyPressEvent(self, e):
        key, mods = e.key(), e.modifiers()
        if key == Qt.Key_F1:          # leave F1 (help) to the device window
            e.ignore()
            return
        ctrl = bool(mods & Qt.ControlModifier)
        if ctrl and key == Qt.Key_C and self.textCursor().hasSelection():
            self.copy()
            return
        if self.busy:
            if ctrl and key in (Qt.Key_C, Qt.Key_6, Qt.Key_X):
                self._pending_lines.clear()
                self.cur.interrupt()
            return
        c = self.textCursor()
        if c.position() < self._input_start:
            c.movePosition(QTextCursor.End)
            self.setTextCursor(c)
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self._submit()
            return
        if self.cur.secret_input():          # password: nothing is echoed
            if key == Qt.Key_Backspace:
                self._secret = self._secret[:-1]
            elif e.text() and e.text().isprintable():
                self._secret += e.text()
            return
        if ctrl and key == Qt.Key_Z:
            text = self.cur.ctrl_z()
            self._append("^Z" + text if text else "^Z\n")
            self._show_prompt()
            return
        if ctrl and key == Qt.Key_C:
            self._append("^C\n")
            self._show_prompt()
            return
        if ctrl and key == Qt.Key_V:
            self.paste()
            return
        if key == Qt.Key_Tab:
            done = self.cur.complete(self.current_input())
            if done is not None:
                self._set_input(done)
            return
        if e.text() == "?":
            line = self.current_input()
            help_text = self.cur.help(line)
            self._append("?\n" + help_text)
            self._show_prompt()
            self._set_input(line)
            return
        if key == Qt.Key_Up:
            if self.history and self._hist_pos > 0:
                self._hist_pos -= 1
                self._set_input(self.history[self._hist_pos])
            return
        if key == Qt.Key_Down:
            if self._hist_pos < len(self.history) - 1:
                self._hist_pos += 1
                self._set_input(self.history[self._hist_pos])
            else:
                self._hist_pos = len(self.history)
                self._set_input("")
            return
        if key in (Qt.Key_Backspace, Qt.Key_Left) and c.position() <= self._input_start \
                and not c.hasSelection():
            return
        if key == Qt.Key_Home:
            c.setPosition(self._input_start)
            self.setTextCursor(c)
            return
        if key in (Qt.Key_PageUp, Qt.Key_PageDown):
            super().keyPressEvent(e)
            return
        if c.hasSelection() and c.selectionStart() < self._input_start:
            c.clearSelection()
            c.movePosition(QTextCursor.End)
            self.setTextCursor(c)
        super().keyPressEvent(e)

    def insertFromMimeData(self, source):
        text = source.text().replace("\r\n", "\n").replace("\r", "\n")
        if not text:
            return
        c = self.textCursor()
        if c.position() < self._input_start:
            c.movePosition(QTextCursor.End)
            self.setTextCursor(c)
        lines = text.split("\n")
        first, rest = lines[0], lines[1:]
        self.insertPlainText(first)
        if rest:
            *full, last = rest
            self._submit()
            for l in full:
                self.run_command(l)
            if last:
                if self.busy:
                    self._pending_lines.append(last)
                else:
                    self._set_input(last)

    def canInsertFromMimeData(self, source):
        return source.hasText()

    def contextMenuEvent(self, e):
        menu = self.createStandardContextMenu()
        menu.exec(e.globalPos())
