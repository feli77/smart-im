"""Independent integration regressions for decoding, ranking and local learning."""

import sqlite3

import pytest

from smart_im.engine import Engine


class FlatReviewModel:
    name = "review-flat"

    def score(self, context, text):
        return -1.0

    def predict(self, context, limit=5):
        return []


@pytest.mark.parametrize("raw", ["blm", "bulum", "bulumo"])
def test_new_phrase_learns_canonical_pinyin_and_survives_restart(tmp_path, raw):
    with Engine(tmp_path, model=FlatReviewModel(), learning=True) as engine:
        engine.commit(raw, "布鲁墨", "项目代号叫")
    with Engine(tmp_path, model=FlatReviewModel(), learning=True) as engine:
        candidates = engine.suggest("bulumo").candidates
        assert candidates[0].text == "布鲁墨"
        assert candidates[0].pinyin == "bulumo"
        assert candidates[0].source == "personal"
        assert engine.stats()["selections"] == 1


@pytest.mark.parametrize("separated", ["bu'lu'mo", "bu lu mo"])
def test_personal_phrase_accepts_explicit_syllable_boundaries(tmp_path, separated):
    with Engine(tmp_path, model=FlatReviewModel(), learning=True) as engine:
        engine.commit("bulumo", "布鲁墨")
        assert engine.suggest(separated).candidates[0].text == "布鲁墨"


def test_personal_learning_respects_explicit_syllable_boundaries(tmp_path):
    with Engine(tmp_path, model=FlatReviewModel(), learning=True) as engine:
        for _ in range(20):
            engine.commit("xian", "先")
        assert engine.suggest("xian").candidates[0].text == "先"
        assert "先" not in {candidate.text for candidate in engine.suggest("xi'an").candidates}


def test_polyphonic_selected_reading_is_retained(tmp_path):
    with Engine(tmp_path, model=FlatReviewModel(), learning=True) as engine:
        engine.commit("chong", "重")
    with sqlite3.connect(tmp_path / "learning.sqlite3") as database:
        rows = database.execute("SELECT pinyin, text, count FROM words").fetchall()
    assert rows == [("chong", "重", 1)]


def test_typo_commit_does_not_teach_misspelled_pinyin(tmp_path):
    with Engine(tmp_path, model=FlatReviewModel(), learning=True) as engine:
        engine.commit("nihoa", "你好")
    with sqlite3.connect(tmp_path / "learning.sqlite3") as database:
        rows = database.execute("SELECT pinyin, text FROM words").fetchall()
    assert rows == [("nihao", "你好")]


def test_comma_confirmation_learns_underlying_word(tmp_path):
    with Engine(tmp_path, model=FlatReviewModel(), learning=True) as engine:
        engine.commit("nihao", "你好，", "打个招呼")
        assert engine.stats()["selections"] == 1
        assert engine.predict("打个招呼")[0].text == "你好"
    with sqlite3.connect(tmp_path / "learning.sqlite3") as database:
        rows = database.execute("SELECT pinyin, text FROM words").fetchall()
    assert rows == [("nihao", "你好")]


def test_learning_retains_only_committed_phrase_and_short_context(tmp_path):
    context = "不会保存整篇文档的长段落。" * 100 + "这次项目的代号叫"
    with Engine(tmp_path, model=FlatReviewModel(), learning=True) as engine:
        engine.suggest("bulumo", context)
        assert engine.stats()["selections"] == 0
        engine.commit("bulumo", "布鲁墨", context)
    with sqlite3.connect(tmp_path / "learning.sqlite3") as database:
        words = database.execute("SELECT text FROM words").fetchall()
        transitions = database.execute("SELECT context, text FROM transitions").fetchall()
    assert words == [("布鲁墨",)]
    assert transitions == [(context[-8:], "布鲁墨")]


def test_private_operations_do_not_open_personal_store(tmp_path, monkeypatch):
    with Engine(tmp_path, model=FlatReviewModel(), learning=True) as engine:
        engine.commit("bulumo", "布鲁墨", "项目叫")
    with Engine(tmp_path, model=FlatReviewModel(), learning=True) as engine:

        def forbidden_store_access():
            raise AssertionError("private operation attempted personal store access")

        monkeypatch.setattr(engine, "_personal", forbidden_store_access)
        assert "布鲁墨" not in {
            candidate.text for candidate in engine.suggest("bulumo", private=True).candidates
        }
        assert engine.predict("项目叫", private=True) == []
        engine.commit("nihao", "你好", private=True)


def test_shipped_model_changes_homophone_ranking_with_context(tmp_path):
    with Engine(tmp_path) as engine:
        assert engine.suggest("shishi", "开始").candidates[0].text == "实施"
        assert engine.suggest("shishi", "这是").candidates[0].text == "事实"


def test_shipped_model_preserves_sensible_phrase_segmentation(tmp_path):
    with Engine(tmp_path) as engine:
        assert engine.suggest("wozhidao").candidates[0].text == "我知道"


def test_learned_unseen_expression_can_outrank_generic_predictions(tmp_path):
    with Engine(tmp_path, learning=True) as engine:
        for _ in range(12):
            engine.commit("daihaojiaobulumo", "代号叫布鲁墨", "项目")
        predictions = engine.predict("项目")
        assert predictions[0].text == "代号叫布鲁墨"
        assert predictions[0].source == "personal"


def test_exact_syllable_keeps_priority_over_model_favored_completion(tmp_path):
    class CompletionLovingModel(FlatReviewModel):
        def score(self, context, text):
            return 1000.0 if text == "你好" else -10.0

    with Engine(tmp_path, model=CompletionLovingModel()) as engine:
        candidate = engine.suggest("ni").candidates[0]
        assert candidate.source == "dictionary"
        assert candidate.pinyin == "ni"


@pytest.mark.parametrize("raw", ["ni!hao", "你好吗", "n" * 65, "nihao" * 1000])
def test_invalid_or_oversized_input_cannot_commit_a_prefix(tmp_path, raw):
    with Engine(tmp_path, model=FlatReviewModel()) as engine:
        assert engine.suggest(raw).candidates == []
    assert not (tmp_path / "learning.sqlite3").exists()


def test_correction_offsets_reference_original_unicode_text(tmp_path):
    prefix = "🧑‍💻历史内容" * 100
    context = prefix + "请按装软件，按装以后登陆账号。"
    with Engine(tmp_path, model=FlatReviewModel()) as engine:
        corrections = engine.suggest("", context).corrections
    assert len(corrections) == 3
    assert corrections[0].start == len(prefix) + 1
    assert corrections[1].start == len(prefix) + 6
    for correction in corrections:
        assert context[correction.start : correction.end] == correction.original


def test_nonfinite_optional_model_score_cannot_poison_candidates(tmp_path):
    class NonfiniteModel(FlatReviewModel):
        def score(self, context, text):
            return float("nan")

    with Engine(tmp_path, model=NonfiniteModel()) as engine:
        assert engine.suggest("nihao").candidates[0].text == "你好"
