"""Wake an unchanged Windows composition after a mailbox response is ready."""

from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass

# Use Windows widths explicitly, including when tests run on another platform.
_DWORD = ctypes.c_uint32
_LONG = ctypes.c_int32
_WORD = ctypes.c_uint16
_HANDLE = ctypes.c_void_p
_ULONG_PTR = ctypes.c_size_t


class _RECT(ctypes.Structure):
    _fields_ = [(name, _LONG) for name in ("left", "top", "right", "bottom")]


class _GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", _DWORD),
        ("flags", _DWORD),
        ("hwndActive", _HANDLE),
        ("hwndFocus", _HANDLE),
        ("hwndCapture", _HANDLE),
        ("hwndMenuOwner", _HANDLE),
        ("hwndMoveSize", _HANDLE),
        ("hwndCaret", _HANDLE),
        ("rcCaret", _RECT),
    ]


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", _DWORD), ("dwTime", _DWORD)]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", _WORD),
        ("wScan", _WORD),
        ("dwFlags", _DWORD),
        ("time", _DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", _LONG),
        ("dy", _LONG),
        ("mouseData", _DWORD),
        ("dwFlags", _DWORD),
        ("time", _DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", _DWORD), ("wParamL", _WORD), ("wParamH", _WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("event",)
    _fields_ = [("type", _DWORD), ("event", _INPUTUNION)]


def _load_user32():
    if os.name != "nt":
        raise OSError("Automatic candidate refresh is only supported on Windows")
    api = ctypes.WinDLL("user32", use_last_error=True)
    signatures = {
        "GetForegroundWindow": ([], _HANDLE),
        "GetWindowThreadProcessId": ([_HANDLE, ctypes.POINTER(_DWORD)], _DWORD),
        "GetGUIThreadInfo": ([_DWORD, ctypes.POINTER(_GUITHREADINFO)], _LONG),
        "GetKeyboardLayout": ([_DWORD], _HANDLE),
        "GetLastInputInfo": ([ctypes.POINTER(_LASTINPUTINFO)], _LONG),
        "GetAsyncKeyState": ([ctypes.c_int], ctypes.c_int16),
        "SendInput": ([_DWORD, ctypes.POINTER(_INPUT), ctypes.c_int], _DWORD),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(api, name)
        function.argtypes, function.restype = arguments, result
    return api


@dataclass(frozen=True)
class RefreshTarget:
    identity: tuple[int, ...]
    last_input_tick: int


class WindowsCandidateRefresh:
    """Best-effort refresh; never redirect focus or retry an injected event.

    A focus check cannot make SendInput target a particular window atomically.
    The Lua processor must consume Select and validate its session/revision.
    """

    def __init__(self, api=None) -> None:
        self._api = api if api is not None else _load_user32()

    def capture(self) -> RefreshTarget | None:
        try:
            foreground = self._api.GetForegroundWindow()
            if not foreground:
                return None
            process = _DWORD()
            thread = self._api.GetWindowThreadProcessId(foreground, ctypes.byref(process))
            if not thread or not process.value:
                return None
            gui = _GUITHREADINFO(cbSize=ctypes.sizeof(_GUITHREADINFO))
            if not self._api.GetGUIThreadInfo(thread, ctypes.byref(gui)):
                return None
            # Exclude menus, window moves and mouse capture, but not caret blink.
            if not gui.hwndFocus or gui.flags & 0x1E or gui.hwndCapture:
                return None
            layout = self._api.GetKeyboardLayout(thread)
            recent = _LASTINPUTINFO(cbSize=ctypes.sizeof(_LASTINPUTINFO))
            if not layout or not self._api.GetLastInputInfo(ctypes.byref(recent)):
                return None
            if self._api.GetForegroundWindow() != foreground:
                return None
            rect = gui.rcCaret
            return RefreshTarget(
                identity=(
                    foreground,
                    process.value,
                    thread,
                    gui.hwndActive or 0,
                    gui.hwndFocus,
                    gui.hwndCaret or 0,
                    rect.left,
                    rect.top,
                    rect.right,
                    rect.bottom,
                    layout,
                ),
                last_input_tick=recent.dwTime,
            )
        except OSError:
            return None

    def _keys_released(self) -> bool:
        # This includes mouse buttons and modifiers. Read only the current-state
        # high bit: the low "pressed since last call" bit is shared with others.
        return not any(self._api.GetAsyncKeyState(key) & 0x8000 for key in range(1, 256))

    def ready(self, target: RefreshTarget | None) -> RefreshTarget | None:
        if target is None:
            return None
        current = self.capture()
        if current is None:
            return None
        # The Lua request can precede TSF applying this keystroke's inline
        # preedit. Its caret may finish moving during debounce, so bind only
        # window/process/thread/active/focus and keyboard layout at this stage.
        # notify() compares the complete settled caret and input tick afterward.
        if (
            current.identity[:5] != target.identity[:5]
            or current.identity[-1] != target.identity[-1]
        ):
            return None
        try:
            return current if self._keys_released() else None
        except OSError:
            return None

    def notify(self, target: RefreshTarget | None) -> bool:
        if target is None or self.capture() != target:
            return False
        try:
            if not self._keys_released() or self.capture() != target:
                return False
            # Weasel itself uses VK_SELECT to obtain fresh data and run a TSF
            # edit session (WeaselTSF/CandidateList.cpp). It maps to Rime 0xff60.
            # Include the full INPUT union: KEYBDINPUT alone is too small.
            inputs = (_INPUT * 2)()
            for event in inputs:
                event.type = 1  # INPUT_KEYBOARD
                event.ki.wVk = 0x29  # VK_SELECT
            inputs[1].ki.dwFlags = 0x0002  # KEYEVENTF_KEYUP
            return self._api.SendInput(2, inputs, ctypes.sizeof(_INPUT)) == 2
        except OSError:
            # UIPI or a disappearing input desktop must not stop model service.
            return False
