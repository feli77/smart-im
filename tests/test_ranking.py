import math

import pytest

from smart_im.engine import Engine
from smart_im.ranking import external_pinyin_key, rank_external


class ContextModel:
    name = "context-test"

    def score(self, context, text):
        return -1.0 if (context, text) in {("执行", "实施"), ("这是", "事实")} else -5.0


class FlatModel(ContextModel):
    def score(self, context, text):
        return -3.0


def test_external_rank_uses_context_and_explicit_learning(tmp_path):
    with Engine(tmp_path, ContextModel(), learning=True) as engine:
        texts = ["事实", "实时", "实施"]
        assert engine.rerank(texts, "执行", "shishi") == [2, 0, 1]
        assert engine.rerank(texts, "这是", "shishi") == [0, 1, 2]
        engine.commit_external("ss", "实施")
        assert engine.rerank(texts, pinyin="ss")[0] == 2


def test_duplicates_remain_distinct_indices_and_input_is_unchanged(tmp_path):
    texts = ["事实", "实施", "实施", "事实"]
    with Engine(tmp_path, ContextModel()) as engine:
        indices = engine.rerank(texts, "执行", "shishi")
    assert indices == [1, 2, 0, 3]
    assert sorted(indices) == list(range(len(texts)))
    assert texts == ["事实", "实施", "实施", "事实"]


@pytest.mark.parametrize("context", ["", " \n\t"])
def test_without_context_rime_order_wins_and_model_is_not_called(tmp_path, context):
    class NoModel(FlatModel):
        def score(self, context, text):
            raise AssertionError("No contextual evidence: keep Rime ranking")

    with Engine(tmp_path, NoModel()) as engine:
        assert engine.rerank(["陌生词", "普通词", "你好"], context) == [0, 1, 2]
        assert engine.stats()["model_error"] is None
    assert not (tmp_path / "learning.sqlite3").exists()


@pytest.mark.parametrize("bad_score", [None, math.nan, math.inf, -math.inf, "bad"])
def test_partial_model_failure_restores_entire_original_order(tmp_path, bad_score):
    class BrokenModel(ContextModel):
        def score(self, context, text):
            if text == "实时":
                if bad_score is None:
                    raise RuntimeError("model failed after scoring an earlier candidate")
                return bad_score
            return super().score(context, text)

    with Engine(tmp_path, BrokenModel(), learning=True) as engine:
        for _ in range(5):
            engine.commit_external("shishi", "实施", "执行")
        assert engine.rerank(["事实", "实时", "实施"], "执行", "shishi") == [0, 1, 2]
        assert engine.stats()["model_error"] is not None


def test_failure_in_empty_context_baseline_also_restores_original(tmp_path):
    class BadBaseline(ContextModel):
        def score(self, context, text):
            return math.nan if not context else -1.0

    with Engine(tmp_path, BadBaseline()) as engine:
        assert engine.rerank(["事实", "实施"], "执行") == [0, 1]


@pytest.mark.parametrize("texts", [[], ["事实"]])
def test_noop_ranking_clears_previous_model_failure_status(tmp_path, texts):
    class BrokenModel(FlatModel):
        def score(self, context, text):
            raise RuntimeError("model failed")

    with Engine(tmp_path, BrokenModel()) as engine:
        engine.rerank(["事实", "实施"], "执行")
        assert engine.model_error == "RuntimeError"
        assert engine.rerank(texts, "执行") == list(range(len(texts)))
        assert engine.model_error == ""
        assert engine.stats()["model_error"] is None


def test_explicit_learning_uses_raw_key_and_preserves_exact_mixed_text(tmp_path):
    texts = ["老师", "律师，AI"]
    with Engine(tmp_path, FlatModel(), learning=True) as engine:
        assert engine.rerank(texts, pinyin="lvshi") == [0, 1]
        assert engine.stats()["selections"] == 0
        engine.commit_external("LÜ4-SHI", texts[1], "预约")
        for raw in ["lü shi", "lu:4'shi", "lvshi"]:
            assert engine.rerank(texts, pinyin=raw) == [1, 0]
        assert engine.rerank(texts, pinyin="laoshi") == [0, 1]
        assert engine.stats()["selections"] == 1
    with Engine(tmp_path, FlatModel(), learning=True) as engine:
        assert engine.rerank(texts, pinyin="lvshi") == [1, 0]


def test_only_matching_personal_context_changes_rank(tmp_path):
    with Engine(tmp_path, FlatModel(), learning=True) as engine:
        engine.commit_external("dx", "多谢，收到！", "回复")
        texts = ["您好", "多谢，收到！"]
        assert engine.rerank(texts, "回复", "different") == [1, 0]
        assert engine.rerank(texts, "无关", "different") == [0, 1]


@pytest.mark.parametrize("private,learning", [(True, True), (False, False)])
def test_learning_disabled_and_private_skip_all_personal_reads_and_writes(
    tmp_path, monkeypatch, private, learning
):
    with Engine(tmp_path, FlatModel(), learning=True) as engine:
        engine.commit_external("shishi", "实施", "执行")
        engine.learning = learning

        def no_store():
            raise AssertionError("Personal storage must not be accessed")

        monkeypatch.setattr(engine, "_personal", no_store)
        assert engine.rerank(["事实", "实施"], "执行", "shishi", private) == [0, 1]
        engine.commit_external("shishi", "实施", "执行", private)


@pytest.mark.parametrize("pinyin", ["", "shi!", "你hao", "a" * 97, None])
def test_invalid_raw_keys_are_not_learned(tmp_path, pinyin):
    with Engine(tmp_path, FlatModel(), learning=True) as engine:
        engine.commit_external(pinyin, "你好，AI")
        assert engine.stats()["selections"] == 0
    assert not (tmp_path / "learning.sqlite3").exists()


@pytest.mark.parametrize("texts", [[""], ["字" * 65], [None], ["字"] * 33, "你好"])
def test_external_pool_bounds_are_checked(tmp_path, texts):
    with Engine(tmp_path, FlatModel()) as engine:
        with pytest.raises(ValueError):
            engine.rerank(texts)


def test_valid_pool_edges_and_context_bound(tmp_path):
    contexts = []

    class RecordingModel(FlatModel):
        def score(self, context, text):
            contexts.append(context)
            return super().score(context, text)

    with Engine(tmp_path, RecordingModel()) as engine:
        assert engine.rerank([]) == []
        assert engine.rerank(["字" * 64]) == [0]
        assert engine.rerank(["字"] * 32, "前" * 200) == list(range(32))
    assert contexts == ["前" * 128, ""]


def test_model_evidence_is_bounded_and_ties_are_stable():
    class HugeModel(FlatModel):
        def score(self, context, text):
            return 1e100 if context and text == "尾" else -1e100

    texts = [str(index) for index in range(31)] + ["尾"]
    assert rank_external(texts, "上下文", HugeModel(), {}, {})[0] == 0

    class TieModel(FlatModel):
        def score(self, context, text):
            return 0.35 / 0.85 if context and text == "乙" else 0.0

    assert rank_external(["甲", "乙"], "上下文", TieModel(), {}, {}) == [0, 1]


def test_external_key_supports_tones_separators_and_abbreviations():
    assert external_pinyin_key("Xī’ān") == "xian"
    assert external_pinyin_key("Lǜ4 SHI") == "lvshi"
    assert external_pinyin_key("ss") == "ss"


def test_batch_reranker_receives_one_bounded_pool_and_personal_learning_still_works(tmp_path):
    calls = []

    class BatchModel:
        name = "batch-test"

        def rerank(self, context, texts, pinyin=""):
            calls.append((context, list(texts), pinyin))
            texts.clear()  # A backend cannot mutate the caller's candidate list.
            return [2, 0, 1]

    texts = ["事实", "实时", "实施"]
    with Engine(tmp_path, BatchModel(), learning=True) as engine:
        assert engine.rerank(texts, "前" * 200, "shishi") == [2, 0, 1]
        assert calls == [("前" * 128, texts, "shishi")]
        assert engine.rerank(texts, pinyin="shishi") == [0, 1, 2]
        assert len(calls) == 1
        engine.commit_external("shishi", "实时")
        assert engine.rerank(texts, "前", "shishi")[0] == 1
        assert engine.rerank(texts, "前", "shishi", private=True) == [2, 0, 1]


@pytest.mark.parametrize("order", [[0, 0, 2], [True, 0, 2], [2, 0], [3, 0, 1], [0.0, 1, 2]])
def test_invalid_batch_order_restores_original_including_personal_boosts(tmp_path, order):
    class BatchModel:
        name = "broken-batch"

        def rerank(self, context, texts, pinyin=""):
            return order

    with Engine(tmp_path, BatchModel(), learning=True) as engine:
        engine.commit_external("shishi", "实施")
        assert engine.rerank(["事实", "实时", "实施"], "执行", "shishi") == [0, 1, 2]
        assert engine.model_error == "ValueError"
