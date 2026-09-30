"""Native PySide6 writing surface with a bounded, serial inference worker."""

from __future__ import annotations

import sys
import threading
from collections import deque
from pathlib import Path

try:
    from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
    from PySide6.QtGui import QTextCursor
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QFrame,
        QHBoxLayout,
        QLabel,
        QMainWindow,
        QPushButton,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:
    raise ImportError('桌面界面需要 PySide6，请运行 pip install -e ".[desktop]"') from exc

from .session import InputSession, KeyAction, prefix_at_utf16
from .types import SuggestionResult


class EngineWorker(QObject):
    """One inference request in flight + one replaceable pending request.

    Confirmed user edits are immediately applied by the UI. Only optional learning
    records are queued; that queue is capped so slow models cannot consume memory.
    Engine construction, learning, inference and close run on the same thread.
    """

    result = Signal(int, object)
    status = Signal(object)
    failed = Signal(str)

    def __init__(self, data_dir=None, model=None, learning=False, factory=None):
        super().__init__()
        self._data_dir = data_dir
        self._model = model
        self._learning = learning
        self._factory = factory
        self._condition = threading.Condition()
        self._pending = None
        self._commands = deque()
        self._stopping = False
        self._thread = threading.Thread(target=self._run, name="smart-im-engine", daemon=True)
        self._thread.start()

    def request(self, revision: int, pinyin: str, context: str):
        with self._condition:
            if self._stopping:
                return
            self._pending = (revision, pinyin, context)
            self._condition.notify()

    def command(self, name: str, *args):
        with self._condition:
            if self._stopping:
                return
            if name == "learning":
                self._learning = bool(args[0])
            if name == "commit" and len(self._commands) >= 256:
                # Losing an optional learning observation is preferable to dropping
                # a privacy control command or accumulating an unbounded queue.
                return
            if name == "clear":
                # A privacy reset must not replay old queued learning afterwards.
                self._commands.clear()
                self._commands.append(("learning", (self._learning,)))
                self._pending = None
            self._commands.append((name, args))
            self._condition.notify()

    def stop(self):
        with self._condition:
            self._stopping = True
            self._pending = None
            self._condition.notify()

    def wait(self, timeout=5.0):
        self._thread.join(timeout)

    def _run(self):
        engine = None
        try:
            if self._factory is None:
                from .engine import Engine

                self._factory = Engine
            engine = self._factory(
                data_dir=self._data_dir, model=self._model, learning=self._learning
            )
            self.status.emit(engine.stats())
            while True:
                with self._condition:
                    self._condition.wait_for(
                        lambda: self._stopping or self._commands or self._pending
                    )
                    if self._stopping and not self._commands:
                        break
                    if self._commands:
                        name, args = self._commands.popleft()
                        request = None
                    else:
                        request, self._pending = self._pending, None
                        name, args = "", ()
                try:
                    if name == "commit":
                        engine.commit(*args)
                    elif name == "learning":
                        engine.learning = bool(args[0])
                    elif name == "clear":
                        engine.clear_learning()
                    elif request is not None:
                        revision, pinyin, context = request
                        result = engine.suggest(pinyin, context=context, limit=9)
                        if not self._stopping:
                            self.result.emit(revision, result)
                    if name:
                        self.status.emit(engine.stats())
                except Exception as exc:
                    self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            if engine is not None:
                engine.close()


STYLE = """
QMainWindow, QWidget#root { background: #F7F6F0; color: #213D38; }
QWidget { font-family: 'Microsoft YaHei UI', 'Noto Sans CJK SC', 'Arial'; font-size: 14px; }
QFrame#sidebar { background: #123F37; border-radius: 18px; }
QFrame#sidebar QLabel { color: #D6E8DC; background: transparent; }
QLabel#brand { color: #FFFFFF; font-size: 27px; font-weight: 700; }
QLabel#overline { color: #809E79; font-size: 11px; font-weight: 700; letter-spacing: 2px; }
QLabel#title { font-size: 30px; font-weight: 700; color: #183E35; }
QLabel#subtle { color: #73867B; font-size: 12px; }
QFrame#card { background: #FFFFFF; border: 1px solid #E2E6DC; border-radius: 14px; }
QTextEdit { background: white; border: none; border-radius: 12px; color: #243F37;
            font-size: 21px; padding: 14px; selection-background-color: #CBE3D0; }
QPushButton { background: #EDF2E7; color: #234A3B; border: 1px solid #D9E3D1;
              padding: 10px 14px; border-radius: 8px; text-align: left; }
QPushButton:hover { background: #DCEAD4; border-color: #90AD82; }
QPushButton:pressed { background: #C4DABB; }
QPushButton#first { background: #1D5947; color: #FFFFFF; border: 1px solid #1D5947; }
QPushButton#first:hover { background: #2A715A; }
QPushButton#quiet { background: transparent; color: #CCDACE; border-color: #42685C; font-size: 12px; }
QLabel#composition { font-size: 19px; color: #2E6C4F; font-weight: 600; }
QCheckBox { color: #D6E8DC; spacing: 9px; padding: 7px 0; }
QCheckBox::indicator { width: 18px; height: 18px; border: 1px solid #769883; border-radius: 5px; background: #234D41; }
QCheckBox::indicator:checked { background: #BCE08B; border-color: #BCE08B; }
QLabel#pill { background: #E5EEDA; border-radius: 11px; padding: 5px 11px; color: #48673F; font-size: 11px; }
"""


def label(text: str, name: str = "", wrap: bool = False) -> QLabel:
    item = QLabel(text)
    if name:
        item.setObjectName(name)
    item.setWordWrap(wrap)
    return item


def clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()


class MainWindow(QMainWindow):
    def __init__(
        self, data_dir: Path | str | None = None, model=None, learning=False, factory=None
    ):
        super().__init__()
        self.setWindowTitle("Smart IM · 本地 AI 中文输入法")
        self.resize(1120, 790)
        self.setMinimumSize(850, 660)
        self.session = InputSession()
        self._inserting = False
        self._closed = False
        self._last_result = SuggestionResult()
        self._request_context = ""
        self._build_ui(learning)
        self.worker = EngineWorker(data_dir, model, learning, factory)
        self.worker.result.connect(self._on_result, Qt.ConnectionType.QueuedConnection)
        self.worker.failed.connect(self._on_error, Qt.ConnectionType.QueuedConnection)
        self.learning.toggled.connect(self._learning_changed)
        self.clear_button.clicked.connect(self._clear_learning)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(25)
        self._timer.timeout.connect(self._request)
        self.editor.textChanged.connect(self._editor_changed)
        self.editor.cursorPositionChanged.connect(self._cursor_changed)
        self.editor.installEventFilter(self)
        self.editor.setFocus()
        self._schedule()

    def _build_ui(self, learning):
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(27)
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(215)
        sidebar = QVBoxLayout(side)
        sidebar.setContentsMargins(23, 27, 23, 22)
        sidebar.setSpacing(12)
        sidebar.addWidget(label("Smart IM", "brand"))
        sidebar.addWidget(label("让输入，跟上思考", wrap=True))
        sidebar.addSpacing(29)
        sidebar.addWidget(label("输入工作台", "overline"))
        sidebar.addWidget(label("●  本地中文输入"))
        sidebar.addSpacing(19)
        self.enabled = QCheckBox("中文模式")
        self.enabled.setChecked(True)
        self.enabled.toggled.connect(self._toggle_mode)
        sidebar.addWidget(self.enabled)
        self.learning = QCheckBox("学习我的表达")
        self.learning.setChecked(learning)
        sidebar.addWidget(self.learning)
        sidebar.addWidget(label("默认关闭学习。开启后，确认上屏的词句仅保存在本机。", wrap=True))
        sidebar.addSpacing(12)
        self.clear_button = QPushButton("清除个人学习数据")
        self.clear_button.setObjectName("quiet")
        sidebar.addWidget(self.clear_button)
        sidebar.addStretch()
        sidebar.addWidget(label("快捷键", "overline"))
        sidebar.addWidget(
            label(
                "空格  选首个候选\n1–9    选择候选\nTab    接受首个续写\nEnter  上屏原始拼音\nEsc     取消拼音\nCtrl + Space  中英切换",
                wrap=True,
            )
        )
        sidebar.addSpacing(18)
        sidebar.addWidget(label("LOCAL FIRST  /  MVP", "overline"))
        layout.addWidget(side)
        main = QVBoxLayout()
        main.setSpacing(15)
        heading = QHBoxLayout()
        heading.addWidget(label("把想法写下来", "title"))
        heading.addStretch()
        heading.addWidget(label("●  离线运行", "pill"))
        main.addLayout(heading)
        main.addWidget(label("输入拼音，选择候选；继续你的表达。", "subtle"))
        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(13, 13, 13, 8)
        bar = QHBoxLayout()
        bar.addWidget(label("  自由书写", "subtle"))
        bar.addStretch()
        self.count_label = label("0 字", "subtle")
        bar.addWidget(self.count_label)
        card_layout.addLayout(bar)
        self.editor = QTextEdit()
        self.editor.setAcceptRichText(False)
        self.editor.setPlaceholderText(
            "试试输入 nihao，然后按空格。\n\n从一封邮件、一段想法，或今天的计划开始……"
        )
        card_layout.addWidget(self.editor, 1)
        self.composition = label("拼音就绪", "composition")
        card_layout.addWidget(self.composition)
        self.candidate_layout = QHBoxLayout()
        self.candidate_layout.setSpacing(7)
        card_layout.addLayout(self.candidate_layout)
        self.more_layout = QHBoxLayout()
        self.more_layout.setSpacing(7)
        card_layout.addLayout(self.more_layout)
        main.addWidget(card, 1)
        main.addWidget(label("接着写  ·  Tab 接受第一项", "overline"))
        self.prediction_layout = QHBoxLayout()
        self.prediction_layout.setSpacing(8)
        main.addLayout(self.prediction_layout)
        self.correction_label = label(
            "纠错建议会显示在这里，点击后应用到光标前的文本。", "subtle", True
        )
        main.addWidget(self.correction_label)
        self.correction_layout = QHBoxLayout()
        main.addLayout(self.correction_layout)
        self.status_label = label("正在启动本地引擎…", "subtle", True)
        main.addWidget(self.status_label)
        layout.addLayout(main, 1)
        self.setStyleSheet(STYLE)

    def _context(self) -> str:
        cursor = self.editor.textCursor()
        # Selection replacement uses its left edge, never text after the cursor.
        return prefix_at_utf16(self.editor.toPlainText(), cursor.selectionStart())[-512:]

    def _schedule(self):
        if not self._closed:
            self._timer.start()

    def _request(self):
        if self._closed:
            return
        self.session.set_context(self._context())
        self._request_context = self.session.context
        self.worker.request(self.session.revision, self.session.pinyin, self.session.context)

    def _editor_changed(self):
        self.count_label.setText(f"{len(self.editor.toPlainText())} 字")
        if not self._inserting:
            self.session.reset_context(self._context())
            self._render_candidates()
            self._schedule()

    def _cursor_changed(self):
        if not self._inserting:
            self.session.reset_context(self._context())
            self._render_candidates()
            self._schedule()

    def _on_result(self, revision, result):
        if self._closed:
            return
        if not self.session.apply_result(revision, result):
            return
        self._last_result = result
        ready = self.session.drain_actions()
        for action in ready:
            self._apply_action(action)
        if ready:
            return
        self._render_candidates()
        self.status_label.setText(
            f"{result.model_name or '本地模型'}   ·   引擎 {result.elapsed_ms:.1f} ms   ·   {'学习已开启' if self.learning.isChecked() else '学习已关闭'}"
        )
        clear_layout(self.correction_layout)
        for correction in result.corrections[:3]:
            button = QPushButton(f"{correction.original} → {correction.replacement}")
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setToolTip(correction.reason)
            button.clicked.connect(
                lambda checked=False, c=correction, ctx=self._request_context: (
                    self._apply_correction(c, ctx)
                )
            )
            self.correction_layout.addWidget(button)
        self.correction_layout.addStretch()
        self.correction_label.setText(
            "发现可能的笔误 · 点击建议修正"
            if result.corrections
            else "暂无纠错建议 · 编辑器可自由修改和选择文本"
        )

    def _render_candidates(self):
        self.composition.setText(
            self.session.pinyin or ("拼音就绪" if self.session.enabled else "英文模式 · 直接输入")
        )
        clear_layout(self.candidate_layout)
        clear_layout(self.more_layout)
        for index, candidate in enumerate(self.session.candidates[:9]):
            button = QPushButton(f"{index + 1}  {candidate.text}")
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setToolTip(candidate.annotation or candidate.source)
            if index == 0:
                button.setObjectName("first")
            button.clicked.connect(
                lambda checked=False, i=index: self._apply_action(self.session.select(i))
            )
            (self.candidate_layout if index < 5 else self.more_layout).addWidget(button)
        if not self.session.candidates:
            self.candidate_layout.addWidget(
                label("正在匹配…" if self.session.pinyin else "候选词将显示在这里", "subtle")
            )
        self.candidate_layout.addStretch()
        self.more_layout.addStretch()
        clear_layout(self.prediction_layout)
        for index, candidate in enumerate(self.session.predictions[:4]):
            button = QPushButton(candidate.text)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(
                lambda checked=False, i=index: self._apply_action(
                    self.session.select(i, prediction=True)
                )
            )
            self.prediction_layout.addWidget(button)
        if not self.session.predictions:
            self.prediction_layout.addWidget(label("写下一句话，查看上下文续写建议", "subtle"))
        self.prediction_layout.addStretch()

    def _apply_action(self, action: KeyAction):
        if action.text:
            self._inserting = True
            try:
                cursor = self.editor.textCursor()
                cursor.insertText(action.text)
                self.editor.setTextCursor(cursor)
            finally:
                self._inserting = False
            self.worker.command("commit", action.pinyin, action.text, action.context)
            self.session.set_context(self._context())
        if action.changed:
            self._render_candidates()
            clear_layout(self.correction_layout)
            self._schedule()

    def _apply_correction(self, correction, context):
        if self.session.pinyin or self._context() != context:
            return
        before = prefix_at_utf16(
            self.editor.toPlainText(), self.editor.textCursor().selectionStart()
        )
        offset = len(before) - len(context)
        start = offset + correction.start
        end = offset + correction.end
        if before[start:end] != correction.original:
            return
        cursor = self.editor.textCursor()
        cursor.setPosition(len(before[:start].encode("utf-16-le")) // 2)
        cursor.setPosition(
            len(before[:end].encode("utf-16-le")) // 2, QTextCursor.MoveMode.KeepAnchor
        )
        cursor.insertText(correction.replacement)
        self.editor.setTextCursor(cursor)

    def _toggle_mode(self, enabled):
        self.session.enabled = enabled
        self.session.reset_context(self._context())
        if hasattr(self, "_timer"):
            self._render_candidates()
            self._schedule()

    def _learning_changed(self, enabled):
        self.worker.command("learning", enabled)
        self.session.reset_context(self._context())
        self._render_candidates()
        self._schedule()
        self.status_label.setText(
            "学习已开启 · 仅保存之后确认上屏的内容"
            if enabled
            else "学习已关闭 · 后续输入不会写入个人词库"
        )

    def _clear_learning(self):
        self.session.reset_context(self._context())
        self.worker.command("clear")
        self.status_label.setText("个人词频和表达习惯已安排清除")
        self._schedule()

    def _on_error(self, message):
        self.status_label.setText(f"本地引擎提示：{message}")

    def eventFilter(self, watched, event):
        if watched is self.editor and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            modifiers = event.modifiers()
            if key == Qt.Key.Key_Space and modifiers & Qt.KeyboardModifier.ControlModifier:
                self.enabled.setChecked(not self.enabled.isChecked())
                return True
            if not self.session.enabled:
                return False
            names = {
                Qt.Key.Key_Space: "Space",
                Qt.Key.Key_Return: "Return",
                Qt.Key.Key_Enter: "Enter",
                Qt.Key.Key_Escape: "Escape",
                Qt.Key.Key_Backspace: "Backspace",
                Qt.Key.Key_Tab: "Tab",
                Qt.Key.Key_Left: "Left",
                Qt.Key.Key_Right: "Right",
                Qt.Key.Key_Up: "Up",
                Qt.Key.Key_Down: "Down",
                Qt.Key.Key_Home: "Home",
                Qt.Key.Key_End: "End",
                Qt.Key.Key_Delete: "Delete",
                Qt.Key.Key_PageUp: "PageUp",
                Qt.Key.Key_PageDown: "PageDown",
            }
            modified = bool(
                modifiers
                & (
                    Qt.KeyboardModifier.ControlModifier
                    | Qt.KeyboardModifier.AltModifier
                    | Qt.KeyboardModifier.MetaModifier
                )
            )
            self.session.set_context(self._context())
            action = self.session.handle(names.get(key, event.text()), modified=modified)
            self._apply_action(action)
            return action.consumed
        return super().eventFilter(watched, event)

    def closeEvent(self, event):
        self._closed = True
        self._timer.stop()
        self.worker.stop()
        super().closeEvent(event)


def main(data_dir=None, model=None, learning=False) -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("Smart IM")
    window = MainWindow(data_dir, model, learning)
    window.show()
    try:
        return app.exec()
    finally:
        window.worker.stop()
        window.worker.wait(2.0)


if __name__ == "__main__":
    raise SystemExit(main())
