"""Real Lua → filesystem → Python engine → filesystem → Lua round trips."""

import json
import os
import time

import pytest
from test_ollama_model import ollama_server as ollama_server
from test_rime_lua import LuaHarness, identities

from smart_im.engine import Engine
from smart_im.ollama_model import OllamaReranker
from smart_im.rime_protocol import parse_rank_request
from smart_im.rime_service import MailboxService


class BatchModel:
    name = "integration-batch"

    def __init__(self, best=None):
        self.best = best
        self.calls = []

    def rerank(self, context, texts, pinyin="", context_after=""):
        self.calls.append((context, texts, pinyin))
        if self.best is None:
            return list(range(len(texts)))
        return [self.best, *(index for index in range(len(texts)) if index != self.best)]


class LuaRefresh:
    """Deliver the service's system notification, without injecting real keys."""

    def __init__(self, bridge):
        self.bridge = bridge
        self.notifications = 0

    def capture(self):
        return self.bridge.session

    def ready(self, target):
        return target

    def notify(self, target):
        assert target == self.bridge.session
        self.notifications += 1
        assert self.bridge.press(0xFF60) == 1
        assert self.bridge.press(0xFF60, release=True) == 1
        return True


def mailbox_service(bridge, engine, **kwargs):
    return MailboxService(bridge.directory, engine, refresh=LuaRefresh(bridge), **kwargs)


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    # Keep heartbeat age deterministic without making real file mtimes stale.
    now = int(time.time())
    monkeypatch.setattr("smart_im.rime_service.time.time", lambda: float(now))
    harness = LuaHarness(tmp_path / "rime", heartbeat=False, now=now)
    harness.enable_refresh()
    yield harness
    harness.close()


def composition_after(bridge, text):
    # Text can predate this IME session; a TSF read supplies it independently.
    bridge.tsf_context(text)
    bridge.context.input = "shishi"
    bridge.context.caret_pos = 6


def test_ollama_response_updates_and_highlights_without_user_keypress(
    tmp_path, bridge, ollama_server
):
    engine = Engine(tmp_path / "personal", OllamaReranker(endpoint=ollama_server.endpoint))
    with mailbox_service(bridge, engine, debounce_interval=0) as service:
        service.poll_once()
        composition_after(bridge, "系统支持")
        originals = bridge.candidates
        assert identities(bridge.filter()) == [1, 2, 3]
        request = parse_rank_request(bridge.request_path.read_bytes())
        assert request.context == "系统支持"
        assert request.candidates == ("事实", "实时", "实施")
        service.poll_once()

        # The service notification must rebuild the displayed menu by itself.
        output = bridge.displayed
        assert bridge.context.segment.selected_index == 0
        assert bridge.context.segment.prompt == "★ AI 推荐"
        assert bridge.press(0x20) == 2
        assert bridge.press(ord("2")) == 2
        assert bridge.press(0xFF09) == 2
        assert output[0].text == "实时"
        assert sorted(identities(output)) == [1, 2, 3]
        assert bridge.lua.eval("rawequal")(output[0], originals[2])
        assert originals[2].comment == "comment2"
        assert len(ollama_server.requests) == 1
        path, _, payload = ollama_server.requests[0]
        assert path == "/api/chat"
        assert payload["model"] == "qwen3:1.7b"
        assert json.loads(payload["messages"][1]["content"]) == {
            "context": "系统支持",
            "context_after": "",
            "pinyin": "shishi",
            "options": [
                {"index": index, "phrase": "系统支持" + text}
                for index, text in enumerate(["事实", "实时", "实施"])
            ],
        }
        assert not (engine.data_dir / "learning.sqlite3").exists()
        assert service.refresh.notifications == 1
        service.poll_once()
        assert service.refresh.notifications == 1


def test_mixed_whole_word_and_partial_candidates_complete_round_trip(tmp_path, bridge):
    engine = Engine(tmp_path / "personal", BatchModel(best=1))
    with mailbox_service(bridge, engine, debounce_interval=0) as service:
        service.poll_once()
        composition_after(bridge, "系统支持")
        originals = bridge.make_candidates(["事实", "实", "实时", "实施", "事"])
        originals[2]._end = 3
        originals[5]._end = 3
        bridge.filter(originals)
        bridge.enable_refresh()
        request = parse_rank_request(bridge.request_path.read_bytes())
        assert request.candidates == ("事实", "实时", "实施")
        assert "计算中" in bridge.context.segment.prompt
        service.poll_once()
        output = bridge.displayed
        assert "AI 推荐" in bridge.context.segment.prompt
        assert identities(output) == [3, 2, 1, 4, 5]
        for candidate, index in zip(output, [3, 2, 1, 4, 5], strict=True):
            assert bridge.lua.eval("rawequal")(candidate, originals[index])
        assert output[1]._end == output[4]._end == 3


def test_empty_context_keeps_native_order_without_ollama_request(tmp_path, bridge, ollama_server):
    engine = Engine(tmp_path / "personal", OllamaReranker(endpoint=ollama_server.endpoint))
    with mailbox_service(bridge, engine, debounce_interval=0) as service:
        service.poll_once()
        bridge.filter()
        assert parse_rank_request(bridge.request_path.read_bytes()).context == ""
        service.poll_once()
        assert identities(bridge.displayed) == [1, 2, 3]
        assert bridge.context.segment.prompt == "缺少上下文，保留原候选"
        assert service.refresh.notifications == 1
        assert ollama_server.requests == []
        assert not (engine.data_dir / "learning.sqlite3").exists()


@pytest.mark.parametrize("in_document", [False, True])
@pytest.mark.parametrize(
    "prefix,spelling,pinyin,texts",
    [
        ("铁血", "tiexue", "zhanshi", ["展示", "战士", "战事"]),
        ("解决", "jiejue", "fangan", ["反感", "方案", "翻案"]),
    ],
)
def test_context_reaches_batch_ranker_and_response_updates_display(
    tmp_path, bridge, in_document, prefix, spelling, pinyin, texts
):
    model = BatchModel(best=1)
    engine = Engine(tmp_path / "personal", model)
    with mailbox_service(bridge, engine, debounce_interval=0) as service:
        service.poll_once()
        if in_document:
            bridge.tsf_context(prefix)
            bridge.context.input = pinyin
            start = 0
        else:
            bridge.context.input = spelling + pinyin
            start = len(spelling)
            bridge.set_segments((prefix, 0, start, "kSelected"))
        bridge.context.caret_pos = len(bridge.context.input)
        originals = bridge.make_candidates(texts, start=start, finish=bridge.context.caret_pos)
        assert identities(bridge.filter(originals)) == [1, 2, 3]
        bridge.enable_refresh()
        service.poll_once()
        assert model.calls == [(prefix, texts, pinyin)]
        output = bridge.displayed
        assert output[0].text == texts[1]
        assert identities(output) == [2, 1, 3]
        assert bridge.lua.eval("rawequal")(output[0], originals[2])
        assert engine.stats()["selections"] == 0


@pytest.mark.parametrize("service_learns", [False, True])
@pytest.mark.parametrize("client_learns", [False, True])
def test_commit_learning_requires_both_opt_ins_end_to_end(
    tmp_path, bridge, service_learns, client_learns
):
    engine = Engine(tmp_path / "personal", BatchModel(), learning=service_learns)
    with mailbox_service(bridge, engine, debounce_interval=0) as service:
        service.poll_once()
        bridge.option("smart_im_learning", client_learns)
        bridge.filter()
        service.poll_once()
        assert engine.stats()["selections"] == 0
        bridge.commit("实施")
        service.poll_once()
        allowed = service_learns and client_learns
        assert engine.stats()["selections"] == int(allowed)
        assert not list(bridge.directory.glob("*.commit"))

        # A new TSF generation keeps the empty document snapshot independent of learning.
        bridge.tsf_context(token="next-position")
        bridge.press(0xFF51)
        assert identities(bridge.filter()) == [1, 2, 3]
        service.poll_once()
        assert identities(bridge.displayed) == ([3, 1, 2] if allowed else [1, 2, 3])
        assert engine.stats()["selections"] == int(allowed)


def test_turning_learning_off_discards_ready_personalized_result(tmp_path, bridge):
    engine = Engine(tmp_path / "personal", BatchModel(), learning=True)
    with mailbox_service(bridge, engine, debounce_interval=0) as service:
        service.poll_once()
        bridge.option("smart_im_learning", True)
        bridge.commit("实施")
        service.poll_once()
        bridge.press(0xFF51)
        bridge.filter()
        service.poll_once()
        previous = parse_rank_request(bridge.request_path.read_bytes())
        assert bridge.response_path.read_text().endswith("3,1,2\n")

        bridge.option("smart_im_learning", False)
        assert identities(bridge.displayed) == [1, 2, 3]
        current = parse_rank_request(bridge.request_path.read_bytes())
        assert current.revision > previous.revision
        assert not current.learning
        service.poll_once()
        assert identities(bridge.displayed) == [1, 2, 3]
        bridge.commit("实施")
        service.poll_once()
        assert engine.stats()["selections"] == 1


def test_new_candidate_objects_cannot_apply_old_snapshot_response(tmp_path, bridge):
    engine = Engine(tmp_path / "personal", BatchModel(best=1))
    with mailbox_service(bridge, engine, debounce_interval=0) as service:
        service.poll_once()
        composition_after(bridge, "系统支持")
        original = bridge.candidates
        bridge.filter()
        service.poll_once()
        replacement = bridge.make_candidates(["事实", "实时", "实施"])
        replacement[2].comment = "new translation metadata"
        assert identities(bridge.filter(replacement)) == [1, 2, 3]
        assert bridge.press(0xFF60) == 1  # A late duplicate notification.
        assert identities(bridge.displayed) == [1, 2, 3]

        service.poll_once()
        output = bridge.displayed
        assert output[0].text == "实时"
        assert bridge.lua.eval("rawequal")(output[0], replacement[2])
        assert not bridge.lua.eval("rawequal")(output[0], original[2])
        assert original[2].comment == "comment2"
        assert output[0].comment == "new translation metadata"


def test_service_stop_discards_a_previously_ready_order(tmp_path, bridge):
    engine = Engine(tmp_path / "personal", BatchModel(best=1))
    with mailbox_service(bridge, engine, debounce_interval=0) as service:
        service.poll_once()
        composition_after(bridge, "系统支持")
        bridge.filter()
        service.poll_once()
        assert bridge.response_path.exists()
    assert bridge.press(0xFF09) == 2
    assert identities(bridge.filter()) == [1, 2, 3]
    assert "AI 推荐" not in bridge.context.segment.prompt
    assert not bridge.request_path.exists()


@pytest.mark.parametrize("action", ["commit", "escape", "ai_off"])
@pytest.mark.parametrize("inflight", [False, True])
def test_invalidated_composition_cancels_pending_or_discards_inflight_result(
    tmp_path, bridge, monkeypatch, action, inflight
):
    engine = Engine(tmp_path / "personal", BatchModel())
    clock = [100.0]
    calls = []
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    def invalidate():
        if action == "commit":
            bridge.commit("事实")
        elif action == "escape":
            bridge.press(0xFF1B)
        else:
            bridge.option("smart_im_ai", False)

    def rank(*args, **kwargs):
        calls.append(args)
        if inflight:
            invalidate()
        return [2, 0, 1]

    monkeypatch.setattr(engine, "rerank", rank)
    with mailbox_service(bridge, engine) as service:
        service.poll_once()
        composition_after(bridge, "系统支持")
        bridge.filter()
        service.poll_once()
        assert not calls
        if not inflight:
            invalidate()
        clock[0] += 0.1
        service.poll_once()
        assert len(calls) == int(inflight)
        assert not bridge.request_path.exists()
        assert not bridge.response_path.exists()
        assert service.refresh.notifications == 0


def test_rebuilding_candidates_recovers_after_service_expires_idle_files(tmp_path, bridge):
    engine = Engine(tmp_path / "personal", BatchModel(), learning=True)
    with mailbox_service(bridge, engine, debounce_interval=0) as service:
        service.poll_once()
        bridge.option("smart_im_learning", True)
        bridge.commit("实施")
        service.poll_once()
        bridge.press(0xFF51)
        bridge.filter()
        service.poll_once()
        for path in [bridge.request_path, bridge.response_path]:
            os.utime(path, (1, 1))
        service.poll_once()
        assert not bridge.request_path.exists()
        assert not bridge.response_path.exists()

        bridge.lua.globals().now += 31
        bridge.heartbeat()
        assert identities(bridge.filter()) == [1, 2, 3]
        assert bridge.request_path.exists()
        service.poll_once()
        assert identities(bridge.displayed) == [3, 1, 2]


def test_real_engine_model_failure_never_highlights_a_recommendation(tmp_path, bridge):
    class BrokenModel(BatchModel):
        def rerank(self, context, texts, pinyin="", context_after=""):
            raise RuntimeError("private context must not appear in the fallback response")

    engine = Engine(tmp_path / "personal", BrokenModel())
    with mailbox_service(bridge, engine, debounce_interval=0) as service:
        service.poll_once()
        composition_after(bridge, "系统支持")
        bridge.filter()
        service.poll_once()
        assert engine.stats()["model_error"] == "RuntimeError"
        assert bridge.response_path.read_text().endswith("fallback\tfield-1\n1,2,3\n")
        assert identities(bridge.displayed) == [1, 2, 3]
        assert "AI暂不可用" in bridge.context.segment.prompt
        assert "AI 推荐" not in bridge.context.segment.prompt


def test_original_document_and_selected_prefix_reach_ollama_exactly_once(
    tmp_path, bridge, ollama_server
):
    engine = Engine(tmp_path / "personal", OllamaReranker(endpoint=ollama_server.endpoint))
    with mailbox_service(bridge, engine, debounce_interval=0) as service:
        service.poll_once()
        bridge.tsf_context("原文：我们", "这项方案。", token="document-7")
        bridge.context.input, bridge.context.caret_pos = "jihuashishi", 11
        bridge.set_segments(("计划", 0, 5, "kSelected"))
        originals = bridge.make_candidates(["事实", "实时", "实施"], start=5, finish=11)
        bridge.filter(originals)
        request = parse_rank_request(bridge.request_path.read_bytes())
        assert request.context == "原文：我们"
        assert request.selected_prefix == "计划"
        assert request.context_after == "这项方案。"
        service.poll_once()
        _, _, payload = ollama_server.requests[0]
        sent = json.loads(payload["messages"][1]["content"])
        assert sent["context"] == "原文：我们计划"
        assert sent["context_after"] == "这项方案。"
        assert sent["pinyin"] == "shishi"
        assert [option["phrase"] for option in sent["options"]] == [
            f"原文：我们计划{text}这项方案。" for text in ["事实", "实时", "实施"]
        ]
        assert identities(bridge.displayed) == [2, 1, 3]


@pytest.mark.parametrize("change", ["cursor", "focus"])
@pytest.mark.parametrize("inflight", [False, True])
def test_tsf_cursor_and_focus_change_cancel_pending_or_inflight_rank(
    tmp_path, bridge, monkeypatch, change, inflight
):
    model = BatchModel(best=1)
    engine = Engine(tmp_path / "personal", model)
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    calls = []

    def change_context():
        if change == "cursor":
            # The text is identical; the position/focus generation still differs.
            bridge.tsf_context("系统支持", token="another-position")
        else:
            bridge.property("smart_im_context", "")

    def rank(*args, **kwargs):
        calls.append(kwargs)
        if inflight:
            change_context()
        return [1, 0, 2]

    monkeypatch.setattr(engine, "rerank", rank)
    with mailbox_service(bridge, engine) as service:
        service.poll_once()
        composition_after(bridge, "系统支持")
        bridge.filter()
        service.poll_once()
        assert not calls
        if not inflight:
            change_context()
        clock[0] += 0.1
        service.poll_once()
        assert len(calls) == int(inflight)
        assert not bridge.request_path.exists()
        assert not bridge.response_path.exists()
        assert service.refresh.notifications == 0
        assert identities(bridge.displayed) == [1, 2, 3]
        assert bridge.context.segment.prompt == ""


def test_frontend_without_tsf_never_ranks_or_reconstructs_committed_history(
    tmp_path, bridge, ollama_server
):
    engine = Engine(tmp_path / "personal", OllamaReranker(endpoint=ollama_server.endpoint))
    with mailbox_service(bridge, engine, debounce_interval=0) as service:
        service.poll_once()
        bridge.property("smart_im_context", "")
        bridge.commit("系统支持", "xitongzhichi")
        bridge.context.input, bridge.context.caret_pos = "shishi", 6
        assert identities(bridge.filter()) == [1, 2, 3]
        service.poll_once()
        assert not bridge.request_path.exists()
        assert ollama_server.requests == []
        assert service.refresh.notifications == 0
        assert "未获取到局部上下文" in bridge.context.segment.prompt
