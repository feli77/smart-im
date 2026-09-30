import ctypes
import sys
from unittest.mock import Mock

import pytest

from smart_im.session import InputSession
from smart_im.windows import (
    INPUT,
    KBDLLHOOKSTRUCT,
    KEYBDINPUT,
    NativeHooks,
    key_name,
    unicode_inputs,
)


def test_input_abi_uses_windows_widths_on_all_hosts():
    assert ctypes.sizeof(INPUT) == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
    assert ctypes.sizeof(KEYBDINPUT) == (24 if ctypes.sizeof(ctypes.c_void_p) == 8 else 16)
    assert ctypes.sizeof(KBDLLHOOKSTRUCT) == (24 if ctypes.sizeof(ctypes.c_void_p) == 8 else 20)


def test_unicode_sendinput_includes_surrogate_pairs_and_keyup():
    events = unicode_inputs("中🌱")
    assert len(events) == 6
    assert [event.ki.wScan for event in events[::2]] == [0x4E2D, 0xD83C, 0xDF31]
    assert all(event.type == 1 and event.ki.wVk == 0 for event in events)
    assert [event.ki.dwFlags for event in events] == [4, 6, 4, 6, 4, 6]


def mocked_hooks():
    hook = object.__new__(NativeHooks)
    hook.session = InputSession()
    hook.user32 = Mock()
    hook.user32.CallNextHookEx.return_value = 42
    hook.user32.GetAsyncKeyState.return_value = 0
    hook.user32.GetKeyState.return_value = 0
    hook.user32.GetForegroundWindow.return_value = 100
    hook.target = 100
    hook._suppressed = set()
    hook.on_action = Mock()
    hook.on_reset = Mock()
    return hook


def test_hook_passes_injected_events_and_suppresses_own_keyup():
    hook = mocked_hooks()
    key = KBDLLHOOKSTRUCT(0x41, 0, 0x10, 0, 0)
    assert hook._keyboard(0, 0x100, ctypes.addressof(key)) == 42
    assert not hook.session.pinyin
    key.flags = 0
    assert hook._keyboard(0, 0x100, ctypes.addressof(key)) == 1
    assert hook.session.pinyin == "a"
    assert hook._keyboard(0, 0x101, ctypes.addressof(key)) == 1
    assert hook._suppressed == set()


def test_hook_disabled_mode_does_not_capture_keys():
    hook = mocked_hooks()
    hook.session.enabled = False
    key = KBDLLHOOKSTRUCT(0x41, 0, 0, 0, 0)
    assert hook._keyboard(0, 0x100, ctypes.addressof(key)) == 42
    assert not hook.session.pinyin
    hook.on_action.assert_not_called()


def test_focus_change_discards_previous_context():
    hook = mocked_hooks()
    hook.session.context = "前一个应用的内容"
    hook.user32.GetForegroundWindow.return_value = 200
    hook.check_target()
    assert hook.session.context == ""
    assert hook.target == 200


def test_send_refuses_wrong_foreground_window():
    hook = mocked_hooks()
    with pytest.raises(RuntimeError, match="目标已改变"):
        hook.send("你好", 200)
    hook.user32.SendInput.assert_not_called()


def test_native_key_mapping():
    assert key_name(0x41) == "a"
    assert key_name(0x31) == "1"
    assert key_name(0x31, shift=True) == "!"
    assert key_name(0xBF, shift=True) == "?"
    assert key_name(0x25) == "Left"


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows error path")
def test_unsupported_platform_has_actionable_error():
    from smart_im.windows import main

    with pytest.raises(RuntimeError, match="Windows 10/11"):
        main()


def test_queued_commit_is_canceled_after_mouse_navigation_in_same_window():
    hook = mocked_hooks()
    hook._target_generation = 4
    original_target = (100, 4)
    hook.reset()
    with pytest.raises(RuntimeError, match="位置已改变"):
        hook.send("你好", original_target)
    hook.user32.SendInput.assert_not_called()
