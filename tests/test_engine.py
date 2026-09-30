import socket

from smart_im.engine import Engine
from smart_im.types import Candidate


class FlatModel:
    name = "test-flat"

    def score(self, context, text):
        return -1.0

    def predict(self, context, limit=5):
        return [Candidate("继续", source="model")]


def test_basic_pipeline_and_offline(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("core attempted network access")

    monkeypatch.setattr(socket, "socket", no_network)
    with Engine(tmp_path) as engine:
        result = engine.suggest("nihao")
        assert result.candidates[0].text == "你好"
        assert result.elapsed_ms >= 0
        assert result.model_name
        assert engine.predict("我们")
    assert not (tmp_path / "learning.sqlite3").exists()


def test_learning_changes_rank_and_survives_restart(tmp_path):
    with Engine(tmp_path, FlatModel(), learning=True) as engine:
        candidates = engine.suggest("shishi").candidates
        assert len(candidates) >= 2
        chosen = candidates[-1].text
        for _ in range(12):
            engine.commit("shishi", chosen, "我们")
        assert engine.suggest("shishi").candidates[0].text == chosen
    with Engine(tmp_path, FlatModel(), learning=True) as engine:
        assert engine.suggest("shishi").candidates[0].text == chosen
        assert chosen in [c.text for c in engine.predict("我们")]


def test_private_skips_personal_reads_and_writes(tmp_path):
    with Engine(tmp_path, FlatModel(), learning=True) as engine:
        baseline = [c.text for c in engine.suggest("shishi", private=True).candidates]
        chosen = baseline[-1]
        for _ in range(12):
            engine.commit("shishi", chosen)
        before = engine.stats()
        engine.commit("nihao", "你好", private=True)
        assert engine.stats() == before
        assert [c.text for c in engine.suggest("shishi", private=True).candidates] == baseline
        engine.learning = False
        assert [c.text for c in engine.suggest("shishi").candidates] == baseline
        engine.commit("nihao", "你好")
        assert engine.stats()["selections"] == before["selections"]


def test_clear_preserves_basic_input(tmp_path):
    with Engine(tmp_path, FlatModel(), learning=True) as engine:
        engine.commit("nihao", "你好")
        engine.clear_learning()
        assert engine.stats()["selections"] == 0
        assert engine.suggest("nihao").candidates[0].text == "你好"


def test_model_failure_keeps_dictionary_available(tmp_path):
    class BrokenModel(FlatModel):
        def score(self, context, text):
            raise RuntimeError("bad optional model")

        def predict(self, context, limit=5):
            raise RuntimeError("bad optional model")

    with Engine(tmp_path, BrokenModel()) as engine:
        assert engine.suggest("nihao").candidates[0].text == "你好"
        assert "降级" in engine.suggest("nihao").model_name
        assert engine.predict("我们") == []


def test_context_changes_candidate_order(tmp_path):
    class ContextModel(FlatModel):
        def score(self, context, text):
            if context == "执行" and text == "实施":
                return 5.0
            if context == "这是" and text == "事实":
                return 5.0
            return -5.0

    with Engine(tmp_path, ContextModel()) as engine:
        assert engine.suggest("shishi", "执行").candidates[0].text == "实施"
        assert engine.suggest("shishi", "这是").candidates[0].text == "事实"


def test_reject_overlong_composition(tmp_path):
    with Engine(tmp_path, FlatModel()) as engine:
        assert engine.suggest("nihao" * 40).candidates == []
