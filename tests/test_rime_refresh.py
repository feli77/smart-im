from __future__ import annotations

import ctypes
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from smart_im import rime_refresh
from smart_im.rime_refresh import WindowsCandidateRefresh


class FakeWindows:
    def __init__(self):
        self.foreground = 0x123456789 if ctypes.sizeof(ctypes.c_void_p) == 8 else 123
        self.process = 20
        self.thread = 30
        self.active = self.foreground
        self.focus = 40
        self.caret = 50
        self.rect = (1, 2, 3, 4)
        self.layout = 0x876543210 if ctypes.sizeof(ctypes.c_void_p) == 8 else 456
        self.tick = 100
        self.flags = 0
        self.capture_window = 0
        self.down = set()
        self.pressed_before = set()
        self.fail = None
        self.sent = []
        self.send_count = 2

    def GetForegroundWindow(self):
        return self.foreground

    def GetWindowThreadProcessId(self, hwnd, output):
        assert hwnd == self.foreground
        output._obj.value = self.process
        return self.thread

    def GetGUIThreadInfo(self, thread, output):
        assert thread == self.thread
        gui = output._obj
        assert gui.cbSize == ctypes.sizeof(rime_refresh._GUITHREADINFO)
        gui.flags = self.flags
        gui.hwndActive = self.active
        gui.hwndFocus = self.focus
        gui.hwndCaret = self.caret
        gui.hwndCapture = self.capture_window
        gui.rcCaret = rime_refresh._RECT(*self.rect)
        return self.fail != "gui"

    def GetKeyboardLayout(self, thread):
        assert thread == self.thread
        return self.layout

    def GetLastInputInfo(self, output):
        assert output._obj.cbSize == ctypes.sizeof(rime_refresh._LASTINPUTINFO)
        output._obj.dwTime = self.tick
        return self.fail != "input"

    def GetAsyncKeyState(self, key):
        return (0x8000 if key in self.down else 0) | (1 if key in self.pressed_before else 0)

    def SendInput(self, count, inputs, size):
        self.sent.append((count, bytes(inputs), size))
        return self.send_count


def test_unchanged_composition_gets_one_select_down_up_batch():
    api = FakeWindows()
    refresh = WindowsCandidateRefresh(api)
    target = refresh.capture()
    assert target is not None
    assert refresh.notify(target)
    assert len(api.sent) == 1
    count, data, size = api.sent[0]
    assert count == 2
    assert size == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
    events = (rime_refresh._INPUT * 2).from_buffer_copy(data)
    assert [(event.type, event.ki.wVk, event.ki.dwFlags) for event in events] == [
        (1, 0x29, 0),
        (1, 0x29, 2),
    ]
    assert all(event.ki.time == event.ki.dwExtraInfo == event.ki.wScan == 0 for event in events)
    with pytest.raises(FrozenInstanceError):
        target.last_input_tick = 200


@pytest.mark.parametrize(
    ("attribute", "value"),
    [
        ("foreground", 99),
        ("process", 99),
        ("thread", 99),
        ("active", 99),
        ("focus", 99),
        ("caret", 99),
        ("rect", (2, 2, 3, 4)),
        ("layout", 99),
        ("tick", 99),
    ],
)
def test_focus_input_or_caret_changes_block_notification(attribute, value):
    api = FakeWindows()
    refresh = WindowsCandidateRefresh(api)
    target = refresh.capture()
    setattr(api, attribute, value)
    assert not refresh.notify(target)
    assert not api.sent
    if attribute not in {"caret", "rect", "tick"}:
        assert refresh.ready(target) is None


@pytest.mark.parametrize("key", [1, 2, 4, 5, 6, 0x10, 0x11, 0x12, 0x20, 0x41, 0x5B])
def test_held_mouse_buttons_modifiers_and_typing_block_notification(key):
    api = FakeWindows()
    refresh = WindowsCandidateRefresh(api)
    target = refresh.capture()
    api.down.add(key)
    assert refresh.ready(target) is None
    assert not refresh.notify(target)
    assert not api.sent


def test_ready_accepts_last_character_keyup_before_inference():
    api = FakeWindows()
    refresh = WindowsCandidateRefresh(api)
    api.down.add(0x41)
    target = refresh.capture()
    assert target is not None
    assert refresh.ready(target) is None
    api.down.clear()
    api.pressed_before.add(0x41)
    api.tick += 1
    ready = refresh.ready(target)
    assert ready is not None
    assert ready.last_input_tick == 101
    assert not refresh.notify(target)
    assert refresh.notify(ready)


@pytest.mark.parametrize(
    ("caret", "rect"),
    [(99, (1, 2, 3, 4)), (50, (10, 20, 30, 40)), (99, (10, 20, 30, 40))],
)
def test_ready_accepts_tsf_finishing_preedit_caret_layout(caret, rect):
    api = FakeWindows()
    refresh = WindowsCandidateRefresh(api)
    target = refresh.capture()
    api.caret, api.rect = caret, rect
    ready = refresh.ready(target)
    assert ready is not None
    assert ready != target
    assert not refresh.notify(target)
    assert refresh.notify(ready)
    # Once inference begins, a further caret move invalidates the notification.
    api.rect = (100, 200, 300, 400)
    assert not refresh.notify(ready)
    assert len(api.sent) == 1


@pytest.mark.parametrize(
    ("attribute", "value"),
    [
        ("foreground", 0),
        ("process", 0),
        ("thread", 0),
        ("focus", 0),
        ("layout", 0),
        ("flags", 2),
        ("flags", 4),
        ("flags", 8),
        ("flags", 16),
        ("capture_window", 99),
        ("fail", "gui"),
        ("fail", "input"),
    ],
)
def test_capture_fails_closed_when_input_target_is_not_available(attribute, value):
    api = FakeWindows()
    setattr(api, attribute, value)
    refresh = WindowsCandidateRefresh(api)
    assert refresh.capture() is None
    assert refresh.ready(None) is None
    assert not refresh.notify(None)
    assert not api.sent


def test_caret_blink_and_tsf_without_native_caret_do_not_block_capture():
    api = FakeWindows()
    refresh = WindowsCandidateRefresh(api)
    api.caret = 0
    target = refresh.capture()
    assert target is not None
    api.flags = 1  # GUI_CARETBLINKING is not a focus or selection change.
    assert refresh.notify(target)


def test_foreground_change_during_capture_is_rejected():
    api = FakeWindows()
    calls = iter([api.foreground, 99])
    api.GetForegroundWindow = lambda: next(calls)
    assert WindowsCandidateRefresh(api).capture() is None


def test_change_while_checking_key_state_is_rejected_before_send():
    api = FakeWindows()
    refresh = WindowsCandidateRefresh(api)
    target = refresh.capture()

    def change_focus(key):
        api.focus = 99
        return 0

    api.GetAsyncKeyState = change_focus
    assert not refresh.notify(target)
    assert not api.sent


@pytest.mark.parametrize("sent", [0, 1])
def test_blocked_or_partial_send_is_not_retried(sent):
    api = FakeWindows()
    api.send_count = sent
    refresh = WindowsCandidateRefresh(api)
    assert not refresh.notify(refresh.capture())
    assert len(api.sent) == 1


def test_native_errors_are_best_effort():
    api = FakeWindows()
    refresh = WindowsCandidateRefresh(api)
    target = refresh.capture()

    def unavailable(*args):
        raise OSError("input desktop is unavailable")

    api.SendInput = unavailable
    assert not refresh.notify(target)
    api.GetAsyncKeyState = unavailable
    assert refresh.ready(target) is None
    assert not refresh.notify(target)
    api.GetForegroundWindow = unavailable
    assert refresh.capture() is None


def test_real_dll_signatures_preserve_handles_and_input_size(monkeypatch):
    class Function:
        pass

    class DLL:
        def __init__(self):
            for name in (
                "GetForegroundWindow",
                "GetWindowThreadProcessId",
                "GetGUIThreadInfo",
                "GetKeyboardLayout",
                "GetLastInputInfo",
                "GetAsyncKeyState",
                "SendInput",
            ):
                setattr(self, name, Function())

    dll = DLL()
    monkeypatch.setattr(rime_refresh, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(rime_refresh.ctypes, "WinDLL", lambda *args, **kwargs: dll, raising=False)
    assert rime_refresh._load_user32() is dll
    assert dll.GetForegroundWindow.restype is ctypes.c_void_p
    assert dll.GetKeyboardLayout.restype is ctypes.c_void_p
    assert dll.GetWindowThreadProcessId.argtypes[0] is ctypes.c_void_p
    assert dll.GetAsyncKeyState.restype is ctypes.c_int16
    assert dll.SendInput.argtypes == [
        ctypes.c_uint32,
        ctypes.POINTER(rime_refresh._INPUT),
        ctypes.c_int,
    ]


def test_non_windows_constructor_does_not_load_user32(monkeypatch):
    monkeypatch.setattr(rime_refresh, "os", SimpleNamespace(name="posix"))
    with pytest.raises(OSError, match="only supported on Windows"):
        WindowsCandidateRefresh()
