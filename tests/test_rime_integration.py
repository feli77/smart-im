"""Real Lua → filesystem → Python engine → filesystem → Lua round trips."""

import os
import time

import pytest
from test_rime_lua import LuaHarness, identities

from smart_im.engine import Engine
from smart_im.rime_protocol import parse_rank_request
from smart_im.rime_service import MailboxService


class FlatModel:
    name = "integration-flat"

    def score(self, context, text):
        return -3.0


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    # Keep heartbeat age deterministic without making real file mtimes stale.
    now = int(time.time())
    monkeypatch.setattr("smart_im.rime_service.time.time", lambda: float(now))
    harness = LuaHarness(tmp_path / "rime", heartbeat=False, now=now)
    yield harness
    harness.close()


def composition_after(bridge, text):
    bridge.commit(text, pinyin="xitongzhichi")
    bridge.context.input = "shishi"
    bridge.context.caret_pos = 6


def test_bundled_model_round_trip_only_changes_display_after_tab(tmp_path, bridge):
    engine = Engine(tmp_path / "personal")
    with MailboxService(bridge.directory, engine) as service:
        service.poll_once()
        composition_after(bridge, "系统支持")
        originals = bridge.candidates
        assert identities(bridge.filter()) == [1, 2, 3]
        request = parse_rank_request(bridge.request_path.read_bytes())
        assert request.context == "系统支持"
        assert request.candidates == ("事实", "实时", "实施")
        service.poll_once()

        # A finished result cannot change the display or hijack ordinary selection.
        assert bridge.press(0x20) == 2
        assert bridge.press(ord("2")) == 2
        assert identities(bridge.filter()) == [1, 2, 3]
        assert bridge.press() == 1
        output = bridge.filter()
        assert output[0].text == "实时"
        assert sorted(identities(output)) == [1, 2, 3]
        assert bridge.lua.eval("rawequal")(output[0], originals[2])
        assert originals[2].comment == "comment2"
        assert not (engine.data_dir / "learning.sqlite3").exists()


def test_mixed_whole_word_and_partial_candidates_complete_round_trip(tmp_path, bridge):
    engine = Engine(tmp_path / "personal")
    with MailboxService(bridge.directory, engine) as service:
        service.poll_once()
        composition_after(bridge, "系统支持")
        originals = bridge.make_candidates(["事实", "实", "实时", "实施", "事"])
        originals[2]._end = 3
        originals[5]._end = 3
        bridge.filter(originals)
        bridge.enable_refresh()
        request = parse_rank_request(bridge.request_path.read_bytes())
        assert request.candidates == ("事实", "实时", "实施")
        bridge.press()
        assert "计算中" in bridge.context.segment.prompt
        service.poll_once()
        bridge.press()
        output = bridge.filter()
        assert "AI排序" in bridge.context.segment.prompt
        assert identities(output) == [3, 2, 1, 4, 5]
        for candidate, index in zip(output, [3, 2, 1, 4, 5], strict=True):
            assert bridge.lua.eval("rawequal")(candidate, originals[index])
        assert output[1]._end == output[4]._end == 3


@pytest.mark.parametrize("service_learns", [False, True])
@pytest.mark.parametrize("client_learns", [False, True])
def test_commit_learning_requires_both_opt_ins_end_to_end(
    tmp_path, bridge, service_learns, client_learns
):
    engine = Engine(tmp_path / "personal", FlatModel(), learning=service_learns)
    with MailboxService(bridge.directory, engine) as service:
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

        # Navigation clears transient history, leaving only learned word evidence.
        bridge.press(0xFF51)
        assert identities(bridge.filter()) == [1, 2, 3]
        service.poll_once()
        bridge.press()
        assert identities(bridge.filter()) == ([3, 1, 2] if allowed else [1, 2, 3])
        assert engine.stats()["selections"] == int(allowed)


def test_turning_learning_off_discards_ready_personalized_result(tmp_path, bridge):
    engine = Engine(tmp_path / "personal", FlatModel(), learning=True)
    with MailboxService(bridge.directory, engine) as service:
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
        bridge.press()
        assert identities(bridge.filter()) == [1, 2, 3]
        current = parse_rank_request(bridge.request_path.read_bytes())
        assert current.revision > previous.revision
        assert not current.learning
        service.poll_once()
        bridge.press()
        assert identities(bridge.filter()) == [1, 2, 3]
        bridge.commit("实施")
        service.poll_once()
        assert engine.stats()["selections"] == 1


def test_new_candidate_objects_cannot_apply_old_snapshot_response(tmp_path, bridge):
    engine = Engine(tmp_path / "personal")
    with MailboxService(bridge.directory, engine) as service:
        service.poll_once()
        composition_after(bridge, "系统支持")
        original = bridge.candidates
        bridge.filter()
        service.poll_once()
        replacement = bridge.make_candidates(["事实", "实时", "实施"])
        replacement[2].comment = "new translation metadata"
        assert identities(bridge.filter(replacement)) == [1, 2, 3]
        bridge.press()
        assert identities(bridge.filter()) == [1, 2, 3]

        service.poll_once()
        bridge.press()
        output = bridge.filter()
        assert output[0].text == "实时"
        assert bridge.lua.eval("rawequal")(output[0], replacement[2])
        assert not bridge.lua.eval("rawequal")(output[0], original[2])
        assert original[2].comment == "comment2"
        assert output[0].comment == "new translation metadata"


def test_service_stop_discards_a_previously_ready_order(tmp_path, bridge):
    engine = Engine(tmp_path / "personal")
    with MailboxService(bridge.directory, engine) as service:
        service.poll_once()
        composition_after(bridge, "系统支持")
        bridge.filter()
        service.poll_once()
        assert bridge.response_path.exists()
    assert bridge.press() == 1
    assert identities(bridge.filter()) == [1, 2, 3]
    assert "服务未运行" in bridge.context.segment.prompt
    assert not bridge.request_path.exists()


def test_tab_recovers_after_service_expires_idle_mailbox_files(tmp_path, bridge):
    engine = Engine(tmp_path / "personal", FlatModel(), learning=True)
    with MailboxService(bridge.directory, engine) as service:
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
        bridge.press()
        assert identities(bridge.filter()) == [1, 2, 3]
        assert bridge.request_path.exists()
        service.poll_once()
        bridge.press()
        assert identities(bridge.filter()) == [3, 1, 2]


def test_real_engine_model_failure_keeps_originals_through_tab(tmp_path, bridge):
    class BrokenModel(FlatModel):
        def score(self, context, text):
            if text == "实施":
                raise RuntimeError("model unavailable after some candidates were scored")
            return -1.0 if context and text == "实时" else -5.0

    engine = Engine(tmp_path / "personal", BrokenModel())
    with MailboxService(bridge.directory, engine) as service:
        service.poll_once()
        composition_after(bridge, "系统支持")
        bridge.filter()
        service.poll_once()
        assert engine.stats()["model_error"] == "RuntimeError"
        assert bridge.response_path.read_text().endswith("fallback\n1,2,3\n")
        assert bridge.press() == 1
        assert identities(bridge.filter()) == [1, 2, 3]
        assert "AI暂不可用" in bridge.context.segment.prompt
