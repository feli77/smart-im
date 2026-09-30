"""Offscreen tests exercise real Qt cursor handling and the local engine worker."""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent, Qt  # noqa: E402
from PySide6.QtGui import QTextCursor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from smart_im.desktop import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    window = MainWindow(data_dir=tmp_path)
    window.show()
    yield window
    window.close()
    window.worker._thread.join(timeout=3)
    assert not window.worker._thread.is_alive(), "Inference worker did not stop"
    window._timer.stop()
    # close() only hides a Qt window. Destroy it and its deferred child widgets
    # while Python wrappers still live, before the next test creates a window.
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def wait_for(check, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if check():
            return
        QTest.qWait(10)
    assert check(), "Qt condition did not become true"


def test_pinyin_selection_inserts_chinese(window):
    QTest.keyClicks(window.editor, "nihao")
    wait_for(lambda: bool(window.session.candidates))
    assert window.session.candidates[0].text == "你好"
    QTest.keyClick(window.editor, Qt.Key.Key_Space)
    assert window.editor.toPlainText() == "你好"
    assert window.session.pinyin == ""


def test_selection_replacement_after_emoji(window):
    window.editor.setPlainText("🙂旧词后文")
    cursor = window.editor.textCursor()
    cursor.setPosition(2)
    cursor.setPosition(4, QTextCursor.MoveMode.KeepAnchor)
    window.editor.setTextCursor(cursor)
    QTest.keyClicks(window.editor, "nihao")
    wait_for(lambda: bool(window.session.candidates))
    QTest.keyClick(window.editor, Qt.Key.Key_Space)
    assert window.editor.toPlainText() == "🙂你好后文"


def test_correction_applies_to_right_location_in_long_document(window):
    prefix = "这是前文。" * 120 + "🙂"
    window.editor.setPlainText(prefix + "我以经完成")
    cursor = window.editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    window.editor.setTextCursor(cursor)
    wait_for(lambda: bool(window._last_result.corrections))
    correction = window._last_result.corrections[0]
    window._apply_correction(correction, window._request_context)
    assert window.editor.toPlainText() == prefix + "我已经完成"


def test_cursor_move_cancels_pending_composition(window):
    window.editor.setPlainText("已有文字")
    QTest.keyClicks(window.editor, "ni")
    assert window.session.pinyin == "ni"
    cursor = window.editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    window.editor.setTextCursor(cursor)
    assert window.session.pinyin == ""


def test_fast_space_and_next_word_wait_for_matching_candidates(window):
    # No event-loop wait between typing and selecting: exercise the real race.
    QTest.keyClicks(window.editor, "nihao shijie ")
    wait_for(lambda: window.editor.toPlainText() == "你好世界")
    assert not window.session.pinyin
