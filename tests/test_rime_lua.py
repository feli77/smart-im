"""Execute the shipped Lua bridge, with only the native Rime objects simulated."""

from pathlib import Path

import pytest
from lupa.lua54 import LuaRuntime

from smart_im.rime_protocol import parse_commit_event, parse_rank_request

ASSETS = Path(__file__).parents[1] / "src" / "smart_im" / "rime_assets"


class LuaHarness:
    """A real Lua runtime and real files; reusable by mailbox integration tests."""

    def __init__(self, user_dir, *, heartbeat=True, now=1_900_000_000):
        self.user_dir = Path(user_dir)
        self.directory = self.user_dir / "smart_im_runtime"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().user_dir = str(self.user_dir)
        self.lua.globals().now = now
        self.lua.execute(
            """
            os.time = function() return now end
            rime_api = { get_user_data_dir = function() return user_dir end }
            function notifier()
              local n = { connections = {} }
              function n:connect(callback)
                local c = { callback = callback, active = true }
                function c:disconnect() self.active = false end
                self.connections[#self.connections + 1] = c
                return c
              end
              function n:emit(...)
                for _, c in ipairs(self.connections) do
                  if c.active then c.callback(...) end
                end
              end
              return n
            end
            function new_context()
              local ctx = {
                input = 'shishi', caret_pos = 6, refreshes = 0, commit_text = '',
                options = { smart_im_ai = true, smart_im_learning = false, ascii_mode = false },
                properties = {}, commit_notifier = notifier(), option_update_notifier = notifier(),
                update_notifier = notifier(), unhandled_key_notifier = notifier(),
                segment = { prompt = '', selected_index = 0 }, segments = {},
              }
              function ctx:get_option(name) return self.options[name] or false end
              function ctx:set_option(name, value)
                self.options[name] = value
                self.option_update_notifier:emit(self, name)
              end
              function ctx:get_property(name) return self.properties[name] or '' end
              function ctx:set_property(name, value) self.properties[name] = value end
              function ctx:get_commit_text() return self.commit_text end
              function ctx:has_menu() return self.input ~= '' end
              function ctx:refresh_non_confirmed_composition()
                self.refreshes = self.refreshes + 1
                self.segment.selected_index = 0
                self.segment.prompt = ''
                if self.refresh_callback then self.refresh_callback() end
                self.update_notifier:emit(self)
              end
              ctx.composition = {}
              function ctx.composition:empty() return ctx.input == '' end
              function ctx.composition:back() return ctx.segment end
              function ctx.composition:toSegmentation()
                return { get_segments = function() return ctx.segments end }
              end
              return ctx
            end
            function make_key(code, flags)
              local key = { keycode = code }
              for _, name in ipairs({'ctrl', 'alt', 'super', 'release', 'shift'}) do
                key[name] = function() return flags and flags[name] or false end
              end
              return key
            end
            function candidate(text, index, start, finish)
              return { text = text, start = start or 0, _end = finish or 6,
                type = 'phrase', comment = 'comment' .. index, preedit = 'shi shi',
                quality = 10 - index, identity = index, genuine = {} }
            end
            function selected_segment(text, start, finish, status)
              local seg = { start = start, _end = finish, status = status,
                candidate = candidate(text, 1, start, finish) }
              function seg:get_selected_candidate() return self.candidate end
              return seg
            end
            function collect(module, env, candidates)
              local input = { index = 0, reads = 0 }
              function input:iter()
                return function(source)
                  source.index = source.index + 1
                  if candidates[source.index] then source.reads = source.reads + 1 end
                  return candidates[source.index]
                end, self, nil
              end
              local out, first_yield_reads = {}, nil
              yield = function(cand)
                first_yield_reads = first_yield_reads or input.reads
                out[#out + 1] = cand
              end
              module.filter.func(input, env)
              return out, first_yield_reads
            end
            """
        )
        self.module = self.lua.execute((ASSETS / "lua" / "smart_im.lua").read_text())
        self.context = self.lua.globals().new_context()
        engine = self.lua.table_from({"context": self.context})
        self.processor_env = self.lua.table_from({"engine": engine})
        self.filter_env = self.lua.table_from({"engine": engine})
        self.module.processor.init(self.processor_env)
        self.module.filter.init(self.filter_env)
        self.candidates = self.make_candidates(["事实", "实时", "实施"])
        self.displayed = []
        self.enable_refresh()
        if heartbeat:
            self.heartbeat()

    @property
    def session(self):
        return self.context.properties.smart_im_session

    @property
    def request_path(self):
        return self.directory / f"{self.session}.request"

    @property
    def response_path(self):
        return self.directory / f"{self.session}.response"

    def heartbeat(self, age=0):
        (self.directory / "heartbeat").write_text(str(self.lua.globals().now - age))

    def make_candidates(self, texts, *, start=0, finish=6):
        return self.lua.table_from(
            [
                self.lua.globals().candidate(text, i, start, finish)
                for i, text in enumerate(texts, 1)
            ]
        )

    def set_segments(self, *segments):
        self.context.segments = self.lua.table_from(
            [self.lua.globals().selected_segment(*segment) for segment in segments]
        )

    def filter(self, candidates=None):
        if candidates is not None:
            self.candidates = candidates
        output, self.first_yield_reads = self.lua.globals().collect(
            self.module, self.filter_env, self.candidates
        )
        self.displayed = list(output.values())
        return self.displayed

    def press(self, code=0xFF60, **flags):
        key = self.lua.globals().make_key(code, self.lua.table_from(flags))
        return self.module.processor.func(key, self.processor_env)

    def enable_refresh(self):
        """Simulate Rime rebuilding its translation synchronously on refresh."""
        self.context.refresh_callback = self.filter

    def response(self, order="2,1,3", *, session=None, revision=None, status="ok"):
        request = parse_rank_request(self.request_path.read_bytes())
        self.response_path.write_text(
            f"SMARTIM1\tRESULT\t{session or self.session}\t"
            f"{request.revision if revision is None else revision}\t{status}\n{order}\n"
        )

    def option(self, name, value):
        self.context.set_option(self.context, name, value)

    def update(self, **fields):
        for name, value in fields.items():
            setattr(self.context, name, value)
        self.context.update_notifier.emit(self.context.update_notifier, self.context)

    def commit(self, text, pinyin="shishi"):
        self.context.input = pinyin
        self.context.caret_pos = len(pinyin)
        self.context.commit_text = text
        self.context.commit_notifier.emit(self.context.commit_notifier, self.context)

    def close(self):
        self.module.processor.fini(self.processor_env)
        self.module.filter.fini(self.filter_env)


@pytest.fixture
def bridge(tmp_path):
    value = LuaHarness(tmp_path)
    yield value
    value.close()


def identities(candidates):
    return [candidate.identity for candidate in candidates]


@pytest.mark.parametrize("age", [None, 4, -10])
def test_no_live_service_preserves_candidates_and_creates_no_requests(tmp_path, age):
    bridge = LuaHarness(tmp_path, heartbeat=False)
    if age is not None:
        bridge.heartbeat(age)
    assert identities(bridge.filter()) == [1, 2, 3]
    assert not bridge.request_path.exists()
    assert bridge.press() == 1
    assert "服务未运行" in bridge.context.segment.prompt
    bridge.option("smart_im_learning", True)
    bridge.commit("事实")
    assert not list(bridge.directory.glob("*.commit"))
    bridge.close()


def test_selection_keys_do_not_change_the_display_before_select_notification(bridge):
    assert identities(bridge.filter()) == [1, 2, 3]
    bridge.response()
    assert bridge.press(0x20) == 2
    assert bridge.press(ord("2")) == 2
    assert identities(bridge.displayed) == [1, 2, 3]
    assert bridge.context.refreshes == 0
    assert bridge.press() == 1
    assert identities(bridge.displayed) == [2, 1, 3]
    assert bridge.context.segment.selected_index == 0
    assert bridge.context.segment.prompt == "★ AI 推荐"
    assert bridge.press(release=True) == 1
    assert bridge.context.refreshes == 1
    assert not list(bridge.directory.glob("*.commit"))


def test_natural_filter_automatically_applies_ready_result(bridge):
    bridge.filter()
    bridge.response()
    assert identities(bridge.filter()) == [2, 1, 3]
    assert bridge.context.segment.prompt == "★ AI 推荐"
    assert bridge.context.refreshes == 0


def test_ready_notification_keeps_a_menu_the_user_has_already_navigated(bridge):
    bridge.filter()
    bridge.context.segment.selected_index = 1
    bridge.update()
    request = bridge.request_path.read_bytes()
    bridge.response()
    assert bridge.press() == 1
    assert bridge.press(release=True) == 1
    assert identities(bridge.displayed) == [1, 2, 3]
    assert bridge.context.segment.selected_index == 1
    assert bridge.context.refreshes == 0
    assert bridge.processor_env.smart_im_state.approved is None
    assert bridge.request_path.read_bytes() == request
    assert not list(bridge.directory.glob("*.commit"))


def test_repeated_ready_notifications_do_not_reset_user_highlight(bridge):
    bridge.filter()
    bridge.response()
    bridge.press()
    bridge.context.segment.selected_index = 2
    request = bridge.request_path.read_bytes()
    for _ in range(3):
        assert bridge.press() == 1
        assert bridge.press(release=True) == 1
    assert bridge.context.refreshes == 1
    assert bridge.context.segment.selected_index == 2
    assert bridge.request_path.read_bytes() == request
    assert not list(bridge.directory.glob("*.commit"))


@pytest.mark.parametrize("navigation", [False, True])
def test_moving_highlight_clears_recommendation_without_rebuilding_or_learning(bridge, navigation):
    bridge.filter()
    bridge.response()
    bridge.press()
    assert bridge.context.segment.prompt == "★ AI 推荐"
    refreshes = bridge.context.refreshes
    request = bridge.request_path.read_bytes()
    if navigation:
        assert bridge.press(0xFF54) == 2
    bridge.context.segment.selected_index = 1
    bridge.update()
    assert bridge.context.segment.prompt == ""
    assert bridge.context.segment.selected_index == 1
    assert bridge.context.refreshes == refreshes
    assert identities(bridge.displayed) == [2, 1, 3]
    if navigation:
        assert not bridge.request_path.exists()
        assert bridge.processor_env.smart_im_state.approved is None
    else:
        assert bridge.request_path.read_bytes() == request
    assert not list(bridge.directory.glob("*.commit"))


def test_unchanged_update_keeps_recommendation_until_ranking_is_invalidated(bridge):
    bridge.filter()
    bridge.response()
    bridge.press()
    request = bridge.request_path.read_bytes()
    bridge.update()
    assert bridge.context.segment.prompt == "★ AI 推荐"
    assert bridge.context.segment.selected_index == 0
    assert bridge.context.refreshes == 1
    assert bridge.request_path.read_bytes() == request
    # A navigation key may invalidate ranking even if the index stays at zero.
    bridge.press(0xFF52)
    bridge.update()
    assert bridge.context.segment.prompt == ""
    assert bridge.context.segment.selected_index == 0
    assert bridge.context.refreshes == 1
    assert not bridge.request_path.exists()
    assert not list(bridge.directory.glob("*.commit"))


@pytest.mark.parametrize("change", ["candidate", "metadata", "unranked_slot"])
def test_notification_rechecks_the_actual_base_candidates_before_applying(bridge, change):
    if change == "unranked_slot":
        bridge.candidates[2]._end = 3
    bridge.filter()
    previous = parse_rank_request(bridge.request_path.read_bytes()).revision
    bridge.response("2,1" if change == "unranked_slot" else "2,1,3")
    if change == "candidate":
        bridge.candidates[1].text = "时时"
    elif change == "metadata":
        bridge.candidates[1].comment = "changed"
    else:
        bridge.candidates[2].comment = "changed partial"
    bridge.press()
    assert identities(bridge.displayed) == [1, 2, 3]
    assert bridge.context.segment.prompt == "AI计算中"
    assert parse_rank_request(bridge.request_path.read_bytes()).revision > previous
    assert not bridge.response_path.exists()


@pytest.mark.parametrize("change", ["input", "caret", "clear"])
def test_update_notifier_cancels_requests_before_any_more_keys_arrive(bridge, change):
    bridge.filter()
    bridge.response()
    response = bridge.response_path.read_bytes()
    if change == "input":
        bridge.update(input="shishia", caret_pos=7)
    elif change == "caret":
        bridge.update(caret_pos=5)
    else:
        bridge.update(input="", caret_pos=0)
    assert not bridge.request_path.exists()
    assert not bridge.response_path.exists()
    # A late worker may still publish after its last file check. It cannot
    # revive a canceled composition or cause a refresh/commit.
    bridge.response_path.write_bytes(response)
    assert bridge.press() == 1
    assert bridge.press(release=True) == 1
    assert bridge.context.refreshes == 0
    assert not bridge.request_path.exists()
    assert not list(bridge.directory.glob("*.commit"))


@pytest.mark.parametrize("flags", [{"ctrl": True}, {"alt": True}, {"super": True}])
def test_refresh_signal_with_modifiers_does_not_clear_history_or_commit(bridge, flags):
    bridge.commit("我们计划", "womenjihua")
    bridge.context.input, bridge.context.caret_pos = "shishi", 6
    bridge.filter()
    bridge.response()
    assert bridge.press(**flags) == 1
    assert bridge.press(release=True, **flags) == 1
    assert identities(bridge.displayed) == [2, 1, 3]
    assert bridge.processor_env.smart_im_state.history == "我们计划"
    assert not list(bridge.directory.glob("*.commit"))


def test_refresh_keeps_original_candidate_objects_duplicates_and_metadata(bridge):
    bridge.filter(bridge.make_candidates(["事实", "事实", "实时"]))
    bridge.response()
    bridge.press()
    output = bridge.filter()
    assert identities(output) == [2, 1, 3]
    assert output[0].comment == "comment2"
    assert output[0].preedit == "shi shi"
    assert output[0].quality == 8
    assert bridge.lua.eval("rawequal")(output[0], bridge.candidates[2])
    assert bridge.lua.eval("rawequal")(output[0].genuine, bridge.candidates[2].genuine)


@pytest.mark.parametrize("order", ["1,1,2", "0,2,3", "1,2", "1,2,3,4", "01,2,3", "1,,2,3"])
def test_invalid_permutations_fall_back(bridge, order):
    bridge.filter()
    bridge.response(order)
    assert bridge.press() == 1
    assert identities(bridge.filter()) == [1, 2, 3]


@pytest.mark.parametrize("response", [b"", b"garbage", b"a" * 1025, b"\xff", b"SMARTIM1\tRESULT\n"])
def test_malformed_response_is_ignored(bridge, response):
    bridge.filter()
    bridge.response_path.write_bytes(response)
    assert bridge.press() == 1
    assert identities(bridge.filter()) == [1, 2, 3]


@pytest.mark.parametrize("kind", ["session", "revision", "fallback"])
def test_other_session_old_revision_or_model_fallback_never_reorders(bridge, kind):
    bridge.filter()
    kwargs = {"session": "other"} if kind == "session" else {"revision": 0}
    if kind == "fallback":
        kwargs = {"status": "fallback"}
    bridge.response(**kwargs)
    bridge.press()
    assert identities(bridge.filter()) == [1, 2, 3]


def test_fallback_stays_visible_without_an_automatic_retry_loop(bridge):
    bridge.filter()
    request = bridge.request_path.read_bytes()
    bridge.response(status="fallback")
    for _ in range(3):
        bridge.press()
        assert identities(bridge.filter()) == [1, 2, 3]
        assert bridge.context.segment.prompt == "AI暂不可用，保留原候选"
    assert bridge.context.refreshes == 0
    assert bridge.request_path.read_bytes() == request
    assert not list(bridge.directory.glob("*.commit"))


@pytest.mark.parametrize("flags", [{}, {"shift": True}, {"release": True}])
def test_tab_passes_through_without_applying_results_or_clearing_context(bridge, flags):
    bridge.commit("我们计划", "womenjihua")
    bridge.context.input, bridge.context.caret_pos = "shishi", 6
    bridge.filter()
    bridge.response()
    refreshes = bridge.context.refreshes
    assert bridge.press(0xFF09, **flags) == 2
    key = bridge.lua.globals().make_key(0xFF09, bridge.lua.table_from(flags))
    notifier = bridge.context.unhandled_key_notifier
    notifier.emit(notifier, bridge.context, key)
    assert identities(bridge.displayed) == [1, 2, 3]
    assert bridge.context.refreshes == refreshes
    assert bridge.processor_env.smart_im_state.history == "我们计划"


@pytest.mark.parametrize("flags", [{"ctrl": True}, {"alt": True}, {"super": True}])
@pytest.mark.parametrize("unhandled", [False, True])
def test_tab_shortcuts_pass_through_and_reset_context(bridge, flags, unhandled):
    bridge.commit("我们计划", "womenjihua")
    bridge.context.input, bridge.context.caret_pos = "shishi", 6
    bridge.filter()
    bridge.response()
    refreshes = bridge.context.refreshes
    if unhandled:
        key = bridge.lua.globals().make_key(0xFF09, bridge.lua.table_from(flags))
        notifier = bridge.context.unhandled_key_notifier
        notifier.emit(notifier, bridge.context, key)
    else:
        assert bridge.press(0xFF09, **flags) == 2
        assert bridge.press(0xFF09, release=True, **flags) == 2
    assert identities(bridge.displayed) == [1, 2, 3]
    assert bridge.context.refreshes == refreshes
    assert bridge.processor_env.smart_im_state.history == ""
    assert not bridge.request_path.exists()
    assert not bridge.response_path.exists()


def test_pending_notification_keeps_originals_without_rebuilding(bridge):
    bridge.filter()
    assert bridge.press(shift=True) == 1
    assert bridge.press(release=True) == 1
    assert bridge.press() == 1
    assert bridge.context.segment.prompt == "AI计算中"
    assert bridge.context.refreshes == 0
    assert identities(bridge.filter()) == [1, 2, 3]


def test_identity_result_without_context_does_not_claim_ai_ranking(bridge):
    bridge.filter()
    bridge.response("1,2,3")
    bridge.press()
    assert identities(bridge.filter()) == [1, 2, 3]
    assert bridge.context.segment.prompt == "缺少上下文，保留原候选"


def test_identity_result_with_context_can_recommend_the_existing_first_candidate(bridge):
    bridge.commit("我们计划", "womenjihua")
    bridge.context.input, bridge.context.caret_pos = "shishi", 6
    bridge.filter()
    bridge.response("1,2,3")
    bridge.press()
    assert identities(bridge.displayed) == [1, 2, 3]
    assert bridge.context.segment.prompt == "★ AI 推荐"


@pytest.mark.parametrize("change", ["input", "caret", "candidate", "metadata", "learning"])
def test_applied_order_is_invalidated_by_changed_snapshot(bridge, change):
    bridge.filter()
    bridge.response()
    bridge.press()
    assert identities(bridge.filter()) == [2, 1, 3]
    old_revision = parse_rank_request(bridge.request_path.read_bytes()).revision
    if change == "input":
        bridge.context.input = "shishia"
        bridge.context.caret_pos = 7
    elif change == "caret":
        bridge.context.caret_pos = 5
    elif change == "candidate":
        bridge.candidates[1].text = "时时"
    elif change == "metadata":
        bridge.candidates[1].comment = "changed"
    else:
        bridge.option("smart_im_learning", True)
    assert identities(bridge.filter()) == [1, 2, 3]
    assert parse_rank_request(bridge.request_path.read_bytes()).revision > old_revision
    bridge.press()  # The old file is still present; it must not be applied.
    assert identities(bridge.filter()) == [1, 2, 3]


def test_mixed_candidate_spans_only_reorder_matching_slots(bridge):
    bridge.candidates[2]._end = 3
    assert identities(bridge.filter()) == [1, 2, 3]
    request = parse_rank_request(bridge.request_path.read_bytes())
    assert request.pinyin == "shishi"
    assert request.candidates == ("事实", "实施")
    originals = bridge.candidates
    bridge.response("2,1")
    bridge.press()
    output = bridge.filter()
    assert identities(output) == [3, 2, 1]
    assert all(
        bridge.lua.eval("rawequal")(candidate, originals[index])
        for candidate, index in zip(output, [3, 2, 1], strict=True)
    )
    assert output[1].comment == "comment2"
    assert output[1]._end == 3


def test_mixed_candidate_spans_invalidate_when_an_unranked_slot_changes(bridge):
    bridge.candidates[2]._end = 3
    bridge.filter()
    bridge.response("2,1")
    bridge.press()
    assert identities(bridge.filter()) == [3, 2, 1]
    previous = parse_rank_request(bridge.request_path.read_bytes()).revision
    bridge.candidates[2].comment = "new partial candidate"
    assert identities(bridge.filter()) == [1, 2, 3]
    assert parse_rank_request(bridge.request_path.read_bytes()).revision > previous
    bridge.press()
    assert identities(bridge.filter()) == [1, 2, 3]


@pytest.mark.parametrize("mixed", [False, True])
def test_one_eligible_candidate_never_claims_to_be_computing(bridge, mixed):
    if mixed:
        bridge.candidates[2]._end = 3
        bridge.candidates[3]._end = 3
    else:
        bridge.candidates = bridge.make_candidates(["事实"])
    bridge.enable_refresh()
    original = identities(bridge.filter())
    for _ in range(3):
        assert bridge.press() == 1
        assert "无需AI排序" in bridge.context.segment.prompt
        assert "计算中" not in bridge.context.segment.prompt
        assert identities(bridge.filter()) == original
        assert not bridge.request_path.exists()


def test_request_write_failure_retries_only_on_a_natural_filter(bridge):
    bridge.lua.execute(
        """
        original_open = io.open
        io.open = function(path, mode)
          if path:match('%.request%.tmp$') then return nil end
          return original_open(path, mode)
        end
        """
    )
    bridge.enable_refresh()
    bridge.filter()
    assert bridge.press() == 1
    assert "请求写入失败" in bridge.context.segment.prompt
    assert "计算中" not in bridge.context.segment.prompt
    assert not bridge.request_path.exists()
    bridge.lua.execute("io.open = original_open")
    bridge.press()
    assert not bridge.request_path.exists()
    bridge.filter()
    assert bridge.request_path.exists()
    assert "计算中" in bridge.context.segment.prompt
    bridge.response()
    bridge.press()
    assert identities(bridge.filter()) == [2, 1, 3]


def test_timeout_is_visible_without_starving_late_response(bridge):
    bridge.filter()
    request = bridge.request_path.read_bytes()
    bridge.lua.globals().now += 25
    bridge.heartbeat()
    for _ in range(3):
        assert bridge.press() == 1
        assert "响应超时" in bridge.context.segment.prompt
        assert "计算中" not in bridge.context.segment.prompt
        assert bridge.request_path.read_bytes() == request
    assert bridge.context.refreshes == 0
    bridge.response()
    bridge.press()
    assert identities(bridge.filter()) == [2, 1, 3]


def test_request_pinyin_uses_the_candidates_shared_segment(bridge):
    bridge.context.input = "woshishi"
    bridge.context.caret_pos = 8
    bridge.filter(bridge.make_candidates(["事实", "实时"], start=2, finish=8))
    assert parse_rank_request(bridge.request_path.read_bytes()).pinyin == "shishi"


@pytest.mark.parametrize("status", ["kSelected", "kConfirmed"])
@pytest.mark.parametrize(
    "prefix,spelling,pinyin,texts",
    [
        ("铁血", "tiexue", "zhanshi", ["展示", "战士"]),
        ("解决", "jiejue", "fangan", ["反感", "方案"]),
    ],
)
def test_selected_uncommitted_prefix_is_sent_as_context(
    bridge, status, prefix, spelling, pinyin, texts
):
    bridge.option("smart_im_learning", True)
    bridge.context.input = spelling + pinyin
    bridge.context.caret_pos = len(bridge.context.input)
    bridge.set_segments((prefix, 0, len(spelling), status))
    bridge.filter(bridge.make_candidates(texts, start=len(spelling), finish=len(spelling + pinyin)))
    request = parse_rank_request(bridge.request_path.read_bytes())
    assert request.pinyin == pinyin
    assert request.context == prefix
    assert request.candidates == tuple(texts)
    assert not list(bridge.directory.glob("*.commit"))
    assert bridge.processor_env.smart_im_state.history == ""


def test_prefix_combines_multiple_selected_segments_and_committed_history(bridge):
    bridge.commit("我们", "women")
    bridge.context.input = "jihuaquanshishishi"
    bridge.context.caret_pos = len(bridge.context.input)
    bridge.set_segments(("计划", 0, 5, "kConfirmed"), ("全市", 5, 11, "kSelected"))
    bridge.filter(bridge.make_candidates(["实施", "实时"], start=11, finish=17))
    assert parse_rank_request(bridge.request_path.read_bytes()).context == "我们计划全市"


def test_active_candidate_is_never_read_or_included_in_its_own_context(bridge):
    bridge.context.input = "tiexuezhanshi"
    bridge.context.caret_pos = 13
    bridge.set_segments(("铁血", 0, 6, "kSelected"), ("展示", 6, 13, "kGuess"))
    bridge.context.segments[2].get_selected_candidate = bridge.lua.eval(
        "function() error('active menu recursively requested') end"
    )
    bridge.filter(bridge.make_candidates(["展示", "战士"], start=6, finish=13))
    assert parse_rank_request(bridge.request_path.read_bytes()).context == "铁血"
    bridge.response("2,1")
    bridge.press()
    assert identities(bridge.filter()) == [2, 1]


@pytest.mark.parametrize("change", ["text", "status", "range"])
def test_changed_selected_prefix_invalidates_ready_result_before_notification(bridge, change):
    bridge.context.input = "tiexuezhanshi"
    bridge.context.caret_pos = 13
    bridge.set_segments(("铁血", 0, 6, "kSelected"))
    bridge.filter(bridge.make_candidates(["展示", "战士"], start=6, finish=13))
    previous = parse_rank_request(bridge.request_path.read_bytes()).revision
    bridge.response("2,1")
    segment = bridge.context.segments[1]
    if change == "text":
        segment.candidate.text = "贴血"
    elif change == "status":
        segment.status = "kGuess"
    else:
        segment._end = 5
    bridge.enable_refresh()
    bridge.press()
    assert identities(bridge.filter()) == [1, 2]
    request = parse_rank_request(bridge.request_path.read_bytes())
    assert request.revision > previous
    assert request.context == ("贴血" if change == "text" else "")
    bridge.response("2,1")
    bridge.press()
    assert identities(bridge.filter()) == [2, 1]


def test_selected_prefix_remains_bounded_and_is_not_duplicated_after_commit(bridge):
    bridge.commit("你" * 64)
    bridge.commit("😀" * 64)
    bridge.context.input = "tiexuezhanshi"
    bridge.context.caret_pos = 13
    bridge.set_segments(("铁血", 0, 6, "kConfirmed"))
    bridge.filter(bridge.make_candidates(["展示", "战士"], start=6, finish=13))
    request = parse_rank_request(bridge.request_path.read_bytes())
    assert request.context == "你" * 62 + "😀" * 64 + "铁血"
    bridge.commit("铁血战士", "tiexuezhanshi")
    bridge.set_segments()
    bridge.context.input = "shishi"
    bridge.context.caret_pos = 6
    bridge.filter(bridge.make_candidates(["事实", "实时"]))
    request = parse_rank_request(bridge.request_path.read_bytes())
    assert request.context == "你" * 60 + "😀" * 64 + "铁血战士"


def test_service_started_after_existing_menu_is_requested_on_next_filter(tmp_path):
    bridge = LuaHarness(tmp_path, heartbeat=False)
    bridge.filter()
    assert not bridge.request_path.exists()
    bridge.heartbeat()
    assert bridge.press() == 1
    assert bridge.context.refreshes == 0
    assert not bridge.request_path.exists()
    assert identities(bridge.filter()) == [1, 2, 3]
    assert bridge.request_path.exists()
    bridge.close()


def test_filter_reissues_an_unchanged_composition_after_mailbox_cleanup(bridge):
    bridge.filter()
    previous = parse_rank_request(bridge.request_path.read_bytes()).revision
    bridge.response()
    bridge.request_path.unlink()
    bridge.response_path.unlink()
    bridge.lua.globals().now += 31
    bridge.heartbeat()

    assert bridge.press() == 1
    assert identities(bridge.filter()) == [1, 2, 3]
    current = parse_rank_request(bridge.request_path.read_bytes()).revision
    assert current > previous
    bridge.response(revision=previous)  # A delayed old worker must not win.
    bridge.press()
    assert identities(bridge.filter()) == [1, 2, 3]
    assert parse_rank_request(bridge.request_path.read_bytes()).revision == current
    bridge.response()
    bridge.press()
    assert identities(bridge.filter()) == [2, 1, 3]


def test_repeated_pending_notification_does_not_invalidate_an_inflight_request(bridge):
    bridge.filter()
    request = bridge.request_path.read_bytes()
    for _ in range(3):
        assert bridge.press() == 1
        assert identities(bridge.filter()) == [1, 2, 3]
    assert bridge.request_path.read_bytes() == request
    assert bridge.context.refreshes == 0
    bridge.response()
    bridge.press()
    assert identities(bridge.filter()) == [2, 1, 3]


@pytest.mark.parametrize("action", ["commit", "escape", "ai_off"])
def test_invalidation_removes_only_own_rank_mailbox_files(bridge, action):
    bridge.option("smart_im_learning", True)
    bridge.filter()
    bridge.response()
    other_request = bridge.directory / "other-session.request"
    other_response = bridge.directory / "other-session.response"
    for path in [other_request, other_response]:
        path.write_text("other session")
    if action == "commit":
        bridge.commit("事实")
        assert len(list(bridge.directory.glob("*.commit"))) == 1
    elif action == "escape":
        bridge.press(0xFF1B)
    else:
        bridge.option("smart_im_ai", False)
    assert not bridge.request_path.exists()
    assert not bridge.response_path.exists()
    assert other_request.read_text() == other_response.read_text() == "other session"


def test_ai_off_disables_requests_and_learning_but_consumes_notifications(bridge):
    bridge.option("smart_im_learning", True)
    bridge.option("smart_im_ai", False)
    assert identities(bridge.filter()) == [1, 2, 3]
    assert bridge.press() == 1
    assert bridge.press(release=True) == 1
    assert bridge.press(0xFF09) == 2
    bridge.commit("事实")
    assert not bridge.request_path.exists()
    assert not list(bridge.directory.glob("*.commit"))


def test_top_nine_are_bounded_and_remainder_untouched(bridge):
    texts = [f"候选{i}" for i in range(20)]
    assert identities(bridge.filter(bridge.make_candidates(texts))) == list(range(1, 21))
    assert bridge.first_yield_reads == 9
    assert len(parse_rank_request(bridge.request_path.read_bytes()).candidates) == 9
    bridge.response("9,8,7,6,5,4,3,2,1")
    bridge.press()
    assert identities(bridge.filter()) == list(range(9, 0, -1)) + list(range(10, 21))


def test_commit_learning_requires_explicit_switch_and_uses_precommit_context(bridge):
    bridge.commit("尊重", "zunzhong")
    assert not list(bridge.directory.glob("*.commit"))
    bridge.option("smart_im_learning", True)
    bridge.commit("事实")
    files = list(bridge.directory.glob("*.commit"))
    assert len(files) == 1  # processor/filter share exactly one commit notifier.
    event = parse_commit_event(files[0].read_bytes())
    assert event.text == "事实"
    assert event.pinyin == "shishi"
    assert event.context == "尊重"
    assert event.learning is True
    bridge.option("smart_im_learning", False)
    bridge.commit("实时")
    assert len(list(bridge.directory.glob("*.commit"))) == 1


@pytest.mark.parametrize("reason", ["escape", "left", "shortcut", "unhandled", "ttl", "app"])
def test_session_context_is_conservatively_reset(bridge, reason):
    bridge.commit("尊重", "zunzhong")
    if reason == "escape":
        bridge.press(0xFF1B)
    elif reason == "left":
        bridge.press(0xFF51)
    elif reason == "shortcut":
        bridge.press(ord("v"), ctrl=True)
    elif reason == "unhandled":
        key = bridge.lua.globals().make_key(ord("x"), bridge.lua.table())
        n = bridge.context.unhandled_key_notifier
        n.emit(n, bridge.context, key)
    elif reason == "ttl":
        bridge.lua.globals().now += 31
        bridge.heartbeat()
    else:
        bridge.context.properties.client_app = "another.exe"
    bridge.context.input = "shishi"
    bridge.context.caret_pos = 6
    bridge.filter()
    assert parse_rank_request(bridge.request_path.read_bytes()).context == ""


def test_unicode_context_is_bounded_without_splitting_multibyte_characters(bridge):
    for text in ["你" * 64, "😀" * 64, "好"]:
        bridge.commit(text)
    bridge.filter()
    context = parse_rank_request(bridge.request_path.read_bytes()).context
    assert context == "你" * 63 + "😀" * 64 + "好"


@pytest.mark.parametrize("field", ["pinyin", "candidate"])
def test_overlong_fields_never_write_a_request(bridge, field):
    if field == "pinyin":
        bridge.context.input = "a" * 97
        bridge.context.caret_pos = 97
    else:
        bridge.candidates[1].text = "你" * 65
    assert identities(bridge.filter()) == [1, 2, 3]
    assert not bridge.request_path.exists()
    bridge.enable_refresh()
    bridge.press()
    assert "无法AI排序" in bridge.context.segment.prompt
    assert "计算中" not in bridge.context.segment.prompt


def test_component_cleanup_disconnects_notifiers_only_after_last_reference(bridge):
    bridge.option("smart_im_learning", True)
    bridge.module.processor.fini(bridge.processor_env)
    bridge.commit("事实")
    assert len(list(bridge.directory.glob("*.commit"))) == 1
    bridge.module.filter.fini(bridge.filter_env)
    bridge.commit("实施")
    assert len(list(bridge.directory.glob("*.commit"))) == 1
    assert all(
        not connection.active
        for notifier in [
            bridge.context.commit_notifier,
            bridge.context.update_notifier,
            bridge.context.option_update_notifier,
            bridge.context.unhandled_key_notifier,
        ]
        for connection in notifier.connections.values()
    )


def test_sessions_are_isolated_in_one_lua_vm(bridge):
    other = bridge.lua.globals().new_context()
    env = bridge.lua.table_from({"engine": bridge.lua.table_from({"context": other})})
    bridge.module.processor.init(env)
    assert other.properties.smart_im_session != bridge.session
    bridge.commit("尊重")
    assert env.smart_im_state.history == ""
    bridge.module.processor.fini(env)
