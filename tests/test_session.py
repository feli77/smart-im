from smart_im.session import InputSession, prefix_at_utf16
from smart_im.types import Candidate, SuggestionResult


def type_keys(session, text):
    for key in text:
        assert session.handle(key).consumed


def test_compose_candidate_and_context():
    session = InputSession()
    session.set_context("向你说")
    type_keys(session, "nihao")
    session.apply_result(session.revision, SuggestionResult(candidates=[Candidate("你好")]))
    action = session.handle("Space")
    assert (action.text, action.pinyin, action.context) == ("你好", "nihao", "向你说")
    assert session.context == "向你说你好"
    assert session.pinyin == ""


def test_stale_result_cannot_replace_current_candidates():
    session = InputSession()
    session.handle("n")
    revision = session.revision
    session.handle("i")
    assert not session.apply_result(revision, SuggestionResult(candidates=[Candidate("嗯")]))
    assert session.candidates == []


def test_fast_space_waits_for_matching_result():
    session = InputSession()
    type_keys(session, "nihao")
    revision = session.revision
    action = session.handle("Space")
    assert action.consumed and not action.text
    assert session.pinyin == "nihao"
    session.apply_result(revision, SuggestionResult(candidates=[Candidate("你好")]))
    actions = session.drain_actions()
    assert [action.text for action in actions] == ["你好"]


def test_fast_typing_second_word_preserves_order():
    session = InputSession()
    type_keys(session, "nihao")
    first_revision = session.revision
    session.handle("Space")
    type_keys(session, "shijie")
    session.handle("Space")
    session.apply_result(first_revision, SuggestionResult(candidates=[Candidate("你好")]))
    assert [a.text for a in session.drain_actions() if a.text] == ["你好"]
    assert session.pinyin == "shijie"
    session.apply_result(session.revision, SuggestionResult(candidates=[Candidate("世界")]))
    assert [a.text for a in session.drain_actions() if a.text] == ["世界"]
    assert session.context == "你好世界"


def test_fast_punctuation_waits_and_preserves_candidate():
    session = InputSession()
    type_keys(session, "nihao")
    revision = session.revision
    assert not session.handle(",").text
    session.apply_result(revision, SuggestionResult(candidates=[Candidate("你好")]))
    assert "".join(a.text for a in session.drain_actions()) == "你好，"


def test_navigation_and_shortcuts_forget_external_context():
    session = InputSession()
    session.set_context("不应跨窗口使用")
    type_keys(session, "ni")
    action = session.handle("Left")
    assert not action.consumed
    assert not session.context and not session.pinyin
    session.set_context("刚才的文本")
    assert not session.handle("a", modified=True).consumed
    assert session.context == ""


def test_enter_literal_escape_backspace_and_prediction():
    session = InputSession()
    type_keys(session, "hellp")
    session.handle("Backspace")
    assert session.pinyin == "hell"
    assert session.handle("Enter").text == "hell"
    type_keys(session, "ni")
    assert session.handle("Escape").consumed
    assert session.pinyin == ""
    session.apply_result(session.revision, SuggestionResult(predictions=[Candidate("今天")]))
    assert session.handle("Tab").text == "今天"


def test_reset_cancels_pending_commit_and_buffered_keys():
    session = InputSession()
    type_keys(session, "nihao")
    revision = session.revision
    session.handle("Space")
    type_keys(session, "shi")
    session.reset_context()
    assert not session.apply_result(revision, SuggestionResult(candidates=[Candidate("你好")]))
    assert session.drain_actions() == []
    assert not session.pinyin


def test_utf16_cursor_prefix_handles_emoji():
    assert prefix_at_utf16("你好🌱世界", 4) == "你好🌱"
    assert prefix_at_utf16("你好🌱世界", 5) == "你好🌱世"


def test_disabled_mode_and_limits():
    session = InputSession()
    session.enabled = False
    assert not session.handle("a").consumed
    session.enabled = True
    for _ in range(200):
        session.handle("a")
    assert len(session.pinyin) == 64


def test_fast_enter_does_not_discard_buffered_second_word():
    session = InputSession()
    type_keys(session, "nihao")
    revision = session.revision
    session.handle("Space")
    type_keys(session, "hello")
    session.handle("Enter")
    session.apply_result(revision, SuggestionResult(candidates=[Candidate("你好")]))
    assert "".join(a.text for a in session.drain_actions()) == "你好hello"


def test_backspace_removes_buffered_letter_before_results():
    session = InputSession()
    type_keys(session, "nihao")
    revision = session.revision
    session.handle("Space")
    type_keys(session, "hellp")
    session.handle("Backspace")
    session.handle("o")
    session.handle("Enter")
    session.apply_result(revision, SuggestionResult(candidates=[Candidate("你好")]))
    assert "".join(a.text for a in session.drain_actions()) == "你好hello"
