"""Experimental Windows floating IME (not a registered TSF text service).

The keyboard callback performs no model inference and never reads application
text. Only the active composition and our own confirmed context are retained.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import c_int32, c_size_t, c_ssize_t, c_uint16, c_uint32, c_void_p

from .session import InputSession

DWORD = c_uint32
WORD = c_uint16
LONG = c_int32
ULONG_PTR = c_size_t


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", WORD),
        ("wScan", WORD),
        ("dwFlags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", LONG),
        ("dy", LONG),
        ("mouseData", DWORD),
        ("dwFlags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", DWORD), ("wParamL", WORD), ("wParamH", WORD)]


class INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("type", DWORD), ("value", INPUTUNION)]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", DWORD),
        ("scanCode", DWORD),
        ("flags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", LONG), ("y", LONG)]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", c_void_p),
        ("message", DWORD),
        ("wParam", ULONG_PTR),
        ("lParam", c_ssize_t),
        ("time", DWORD),
        ("pt", POINT),
        ("lPrivate", DWORD),
    ]


def unicode_inputs(text: str):
    """Create Unicode key-down/up pairs, preserving UTF-16 surrogate pairs."""
    encoded = text.encode("utf-16-le")
    units = [int.from_bytes(encoded[i : i + 2], "little") for i in range(0, len(encoded), 2)]
    events = (INPUT * (2 * len(units)))()
    for index, unit in enumerate(units):
        events[index * 2].type = 1  # INPUT_KEYBOARD
        events[index * 2].ki = KEYBDINPUT(0, unit, 0x0004, 0, 0)
        events[index * 2 + 1].type = 1
        events[index * 2 + 1].ki = KEYBDINPUT(0, unit, 0x0004 | 0x0002, 0, 0)
    return events


def key_name(vk: int, shift: bool = False) -> str:
    if 0x41 <= vk <= 0x5A:
        return chr(vk).lower()
    if 0x30 <= vk <= 0x39:
        return ")!@#$%^&*("[vk - 0x30] if shift else chr(vk)
    known = {
        0x08: "Backspace",
        0x09: "Tab",
        0x0D: "Enter",
        0x1B: "Escape",
        0x20: "Space",
        0x21: "PageUp",
        0x22: "PageDown",
        0x23: "End",
        0x24: "Home",
        0x25: "Left",
        0x26: "Up",
        0x27: "Right",
        0x28: "Down",
        0x2E: "Delete",
        0xBC: "<" if shift else ",",
        0xBE: ">" if shift else ".",
        0xBF: "?" if shift else "/",
        0xBA: ":" if shift else ";",
        0xDE: '"' if shift else "'",
        0xBD: "_" if shift else "-",
        0xBB: "+" if shift else "=",
    }
    return known.get(vk, "")


class NativeHooks:
    HOTKEY_ID = 0x5349

    def __init__(self, session, on_action, on_reset):
        if sys.platform != "win32":
            raise RuntimeError(
                "Windows 全局输入模式仅支持 Windows 10/11；其他系统可运行 desktop 工作台。"
            )
        self.session = session
        self.on_action = on_action
        self.on_reset = on_reset
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._callback_type = ctypes.WINFUNCTYPE(c_ssize_t, ctypes.c_int, ULONG_PTR, c_ssize_t)
        self._keyboard_callback = self._callback_type(self._keyboard)
        self._mouse_callback = self._callback_type(self._mouse)
        self._keyboard_hook = None
        self._mouse_hook = None
        self._registered = False
        self._suppressed = set()
        self.target = None
        self._target_generation = 0
        self._configure()

    def _configure(self):
        u = self.user32
        u.SetWindowsHookExW.argtypes = [ctypes.c_int, self._callback_type, c_void_p, DWORD]
        u.SetWindowsHookExW.restype = c_void_p
        u.CallNextHookEx.argtypes = [c_void_p, ctypes.c_int, ULONG_PTR, c_ssize_t]
        u.CallNextHookEx.restype = c_ssize_t
        u.UnhookWindowsHookEx.argtypes = [c_void_p]
        u.UnhookWindowsHookEx.restype = ctypes.c_int
        u.GetForegroundWindow.restype = c_void_p
        u.GetAsyncKeyState.argtypes = [ctypes.c_int]
        u.GetAsyncKeyState.restype = ctypes.c_short
        u.GetKeyState.argtypes = [ctypes.c_int]
        u.GetKeyState.restype = ctypes.c_short
        u.RegisterHotKey.argtypes = [c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
        u.RegisterHotKey.restype = ctypes.c_int
        u.UnregisterHotKey.argtypes = [c_void_p, ctypes.c_int]
        u.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]
        u.SendInput.restype = ctypes.c_uint
        self.kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
        self.kernel32.GetModuleHandleW.restype = c_void_p

    def install(self):
        try:
            # MOD_NOREPEAT | MOD_CONTROL. Failure is explicit: native IMEs can own it.
            if not self.user32.RegisterHotKey(None, self.HOTKEY_ID, 0x4002, 0x20):
                raise RuntimeError("Ctrl+Space 已被其他程序占用。请关闭冲突的热键后重试。")
            self._registered = True
            module = self.kernel32.GetModuleHandleW(None)
            self._keyboard_hook = self.user32.SetWindowsHookExW(
                13, self._keyboard_callback, module, 0
            )
            self._mouse_hook = self.user32.SetWindowsHookExW(14, self._mouse_callback, module, 0)
            if not self._keyboard_hook or not self._mouse_hook:
                raise ctypes.WinError(ctypes.get_last_error())
        except Exception:
            self.close()
            raise

    def close(self):
        for name in ("_keyboard_hook", "_mouse_hook"):
            hook = getattr(self, name)
            if hook:
                self.user32.UnhookWindowsHookEx(hook)
                setattr(self, name, None)
        if self._registered:
            self.user32.UnregisterHotKey(None, self.HOTKEY_ID)
            self._registered = False
        self._suppressed.clear()

    def reset(self):
        self._target_generation = getattr(self, "_target_generation", 0) + 1
        self.session.reset_context()
        self.on_reset()

    def check_target(self):
        target = self.user32.GetForegroundWindow()
        if target != self.target:
            self.target = target
            self.reset()
        return target

    def _keyboard(self, code, message, pointer):
        try:
            if code < 0:
                return self.user32.CallNextHookEx(None, code, message, pointer)
            info = ctypes.cast(pointer, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            if info.flags & 0x10:  # LLKHF_INJECTED: do not re-intercept SendInput.
                return self.user32.CallNextHookEx(None, code, message, pointer)
            vk = info.vkCode
            if message in (0x101, 0x105):
                if vk in self._suppressed:
                    self._suppressed.discard(vk)
                    return 1
                return self.user32.CallNextHookEx(None, code, message, pointer)
            if message not in (0x100, 0x104) or not self.session.enabled:
                return self.user32.CallNextHookEx(None, code, message, pointer)
            target = self.check_target()
            modified = any(
                self.user32.GetAsyncKeyState(k) & 0x8000 for k in (0x11, 0x12, 0x5B, 0x5C)
            )
            if vk in (0x11, 0xA2, 0xA3, 0x12, 0xA4, 0xA5, 0x5B, 0x5C) or modified:
                self.reset()
                return self.user32.CallNextHookEx(None, code, message, pointer)
            if self.user32.GetKeyState(0x14) & 1:  # Caps Lock temporarily passes English.
                self.reset()
                return self.user32.CallNextHookEx(None, code, message, pointer)
            shift = bool(self.user32.GetAsyncKeyState(0x10) & 0x8000)
            key = key_name(vk, shift)
            if key:
                action = self.session.handle(key)
                if action.changed:
                    self.on_action(action, (target, getattr(self, "_target_generation", 0)))
                if action.consumed:
                    self._suppressed.add(vk)
                    return 1
        except Exception:
            # A hook must fail open; exceptions must never trap the user's keyboard.
            self.session.reset_context()
        return self.user32.CallNextHookEx(None, code, message, pointer)

    def _mouse(self, code, message, pointer):
        try:
            # No mouse coordinates, text, or history are captured.
            if (
                code >= 0
                and self.session.enabled
                and message in (0x201, 0x204, 0x207, 0x20B, 0x20A)
            ):
                self.reset()
        except Exception:
            pass
        return self.user32.CallNextHookEx(None, code, message, pointer)

    def send(self, text, target):
        if isinstance(target, tuple):
            target, generation = target
            if generation != getattr(self, "_target_generation", 0):
                raise RuntimeError("输入位置已改变，已取消上屏。")
        if not target or self.user32.GetForegroundWindow() != target:
            self.reset()
            raise RuntimeError("输入目标已改变，已取消上屏。")
        events = unicode_inputs(text)
        if not events:
            return
        sent = self.user32.SendInput(len(events), events, ctypes.sizeof(INPUT))
        if sent != len(events):
            self.reset()
            raise RuntimeError("目标应用拒绝上屏（可能权限级别不同）；请在普通权限的编辑器中测试。")


def main(data_dir=None, model=None, learning=False) -> int:
    if sys.platform != "win32":
        raise RuntimeError(
            "Windows 全局输入模式仅支持 Windows 10/11；请运行 smart-im desktop 体验输入工作台。"
        )
    from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Qt, QTimer, Signal
    from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
    from PySide6.QtWidgets import QApplication, QLabel, QMenu, QSystemTrayIcon, QVBoxLayout, QWidget

    from .desktop import EngineWorker

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)

    class Bridge(QObject):
        action = Signal(object, object)
        reset = Signal()
        toggle = Signal()

    bridge = Bridge()
    session = InputSession()
    session.enabled = False
    worker = EngineWorker(data_dir, model, learning)
    panel = QWidget(
        None,
        Qt.WindowType.Tool
        | Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.WindowStaysOnTopHint
        | Qt.WindowType.WindowDoesNotAcceptFocus,
    )
    panel.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    panel.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    panel.setWindowTitle("Smart IM 候选浮窗")
    panel.setStyleSheet(
        "QWidget {background:#153F36;color:#EEF5DE;font:15px 'Microsoft YaHei UI';border-radius:10px;} QLabel {padding:3px 10px;}"
    )
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(12, 8, 12, 8)
    composition = QLabel("Smart IM 已启动 · Ctrl+Space 开启中文")
    candidates = QLabel("实验性浮窗输入 · 请先切换系统键盘为英文")
    candidates.setWordWrap(True)
    candidates.setMaximumWidth(860)
    details = QLabel("Tab 续写 · Enter 原拼音 · Esc 取消 · 右键托盘退出")
    details.setStyleSheet("color:#ACBEA8;font-size:11px;")
    layout.addWidget(composition)
    layout.addWidget(candidates)
    layout.addWidget(details)
    panel.setMinimumWidth(570)
    hooks = NativeHooks(session, bridge.action.emit, bridge.reset.emit)

    def position_panel():
        panel.adjustSize()
        screen = app.primaryScreen().availableGeometry()
        panel.move(screen.center().x() - panel.width() // 2, screen.bottom() - panel.height() - 24)

    def render():
        composition.setText(
            f"中  {session.pinyin or 'Smart IM · 准备输入'}"
            if session.enabled
            else "Smart IM 英文模式 · Ctrl+Space 开启中文"
        )
        if session.pinyin:
            candidates.setText(
                "   ".join(f"{i + 1} {c.text}" for i, c in enumerate(session.candidates[:9]))
                or "正在匹配拼音…"
            )
        else:
            candidates.setText(
                "Tab  " + "   ·   ".join(c.text for c in session.predictions[:4])
                if session.predictions
                else "空格选首项 · 1–9 选词 · 仅使用本工具确认过的上下文"
            )
        position_panel()

    def request():
        render()
        if session.enabled:
            worker.request(session.revision, session.pinyin, session.context)

    def toggle():
        session.enabled = not session.enabled
        session.reset_context()
        hooks.target = hooks.user32.GetForegroundWindow()
        enabled_action.setChecked(session.enabled)
        render()
        request()

    def on_action(action, target):
        if action.text:
            try:
                hooks.send(action.text, target)
                worker.command("commit", action.pinyin, action.text, action.context)
            except Exception as exc:
                details.setText(str(exc))
        request()

    def on_result(revision, result):
        if session.enabled and session.apply_result(revision, result):
            ready = session.drain_actions()
            for action in ready:
                on_action(action, hooks.target)
            render()
            details.setText(
                f"{result.model_name} · {result.elapsed_ms:.1f} ms · Ctrl+Space 切换 · {'学习开启' if learn_action.isChecked() else '学习关闭'}"
            )

    class HotkeyFilter(QAbstractNativeEventFilter):
        def nativeEventFilter(self, event_type, message):
            native = MSG.from_address(int(message))
            if native.message == 0x312 and native.wParam == hooks.HOTKEY_ID:
                bridge.toggle.emit()
                return True, 0
            return False, 0

    native_filter = HotkeyFilter()
    app.installNativeEventFilter(native_filter)
    bridge.action.connect(on_action, Qt.ConnectionType.QueuedConnection)
    bridge.reset.connect(request, Qt.ConnectionType.QueuedConnection)
    bridge.toggle.connect(toggle, Qt.ConnectionType.QueuedConnection)
    worker.result.connect(on_result)
    worker.failed.connect(details.setText)
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor("#1D5947"))
    painter = QPainter(pixmap)
    painter.setPen(QColor("#EAF5D9"))
    painter.setFont(QFont("Microsoft YaHei UI", 32))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "中")
    painter.end()
    tray = QSystemTrayIcon(QIcon(pixmap), app)
    tray.setToolTip("Smart IM · Ctrl+Space 切换中文")
    menu = QMenu()
    enabled_action = QAction("中文模式  Ctrl+Space", menu)
    enabled_action.setCheckable(True)
    enabled_action.triggered.connect(lambda _: toggle())
    menu.addAction(enabled_action)
    learn_action = QAction("本地学习（默认关闭）", menu)
    learn_action.setCheckable(True)
    learn_action.setChecked(learning)

    def set_learning(checked):
        session.reset_context()
        worker.command("learning", checked)
        request()

    def clear_learning():
        session.reset_context()
        worker.command("clear")
        request()

    learn_action.triggered.connect(set_learning)
    menu.addAction(learn_action)
    menu.addAction("清除个人学习数据", clear_learning)
    menu.addSeparator()
    menu.addAction("退出 Smart IM", app.quit)
    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda reason: toggle() if reason == QSystemTrayIcon.ActivationReason.Trigger else None
    )
    timer = QTimer()
    timer.setInterval(200)
    timer.timeout.connect(lambda: hooks.check_target() if session.enabled else None)

    def cleanup():
        timer.stop()
        hooks.close()
        worker.stop()
        tray.hide()

    app.aboutToQuit.connect(cleanup)
    try:
        hooks.install()
        tray.show()
        panel.show()
        position_panel()
        timer.start()
        return app.exec()
    finally:
        cleanup()
        app.removeNativeEventFilter(native_filter)


if __name__ == "__main__":
    raise SystemExit(main())
