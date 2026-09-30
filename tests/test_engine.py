"""Rime engine regressions for Ollama defaults and bounded personal learning."""

import socket
import sqlite3

import pytest

from smart_im.engine import Engine
from smart_im.ollama_model import OllamaReranker


class FlatModel:
    name = "test-flat"

    def rerank(self, context, texts, pinyin=""):
        return list(range(len(texts)))


def test_default_ollama_setup_and_contextless_ranking_perform_no_network_or_storage_io(
    tmp_path, monkeypatch
):
    def no_network(*args, **kwargs):
        raise AssertionError("core attempted network access")

    monkeypatch.setattr(socket, "socket", no_network)
    data_dir = tmp_path / "personal"
    with Engine(data_dir) as engine:
        assert isinstance(engine.model, OllamaReranker)
        assert engine.model.model == "qwen3:1.7b"
        assert engine.rerank(["实时", "事实", "实施"]) == [0, 1, 2]
        assert engine.stats()["selections"] == 0
        assert engine.stats()["model_error"] is None
    assert not data_dir.exists()


@pytest.mark.parametrize("reopen", [False, True])
def test_clear_removes_learned_order_and_allows_learning_again(tmp_path, reopen):
    texts = ["事实", "实施"]
    engine = Engine(tmp_path, FlatModel(), learning=True)
    engine.commit_external("shishi", "实施", "执行")
    assert engine.rerank(texts, pinyin="shishi") == [1, 0]
    if reopen:
        engine.close()
        engine = Engine(tmp_path, FlatModel(), learning=True)
    with engine:
        engine.clear_learning()
        assert engine.stats()["selections"] == 0
        assert engine.stats()["contexts"] == 0
        assert engine.rerank(texts, "执行", "shishi") == [0, 1]
        engine.commit_external("shishi", "实施")
        assert engine.rerank(texts, pinyin="shishi") == [1, 0]


def test_only_confirmed_candidate_and_short_context_are_stored(tmp_path):
    context = "不应保存的完整文档。" * 100 + "这次项目的代号叫"
    with Engine(tmp_path, FlatModel(), learning=True) as engine:
        engine.rerank(["蓝墨", "布鲁墨"], context, "blm")
        assert engine.stats()["selections"] == 0
        engine.commit_external("blm", "布鲁墨", context)
    with sqlite3.connect(tmp_path / "learning.sqlite3") as database:
        assert database.execute("SELECT pinyin, text, count FROM words").fetchall() == [
            ("blm", "布鲁墨", 1)
        ]
        assert database.execute("SELECT context, text FROM transitions").fetchall() == [
            (context[-8:], "布鲁墨")
        ]


@pytest.mark.parametrize("learning,private", [(False, False), (True, True)])
def test_existing_store_is_not_opened_for_private_or_disabled_learning(
    tmp_path, monkeypatch, learning, private
):
    with Engine(tmp_path, FlatModel(), learning=True) as engine:
        engine.commit_external("shishi", "实施", "执行")
    with Engine(tmp_path, FlatModel(), learning=learning) as engine:

        def no_store():
            raise AssertionError("Personal storage must not be accessed")

        monkeypatch.setattr(engine, "_personal", no_store)
        assert engine.rerank(["事实", "实施"], "执行", "shishi", private) == [0, 1]
        engine.commit_external("shishi", "实施", "执行", private)


def test_model_error_does_not_expose_exception_text(tmp_path):
    class BrokenModel(FlatModel):
        def rerank(self, context, texts, pinyin=""):
            raise RuntimeError("private input: " + context + "".join(texts))

    with Engine(tmp_path, BrokenModel()) as engine:
        assert engine.rerank(["事实", "实施"], "敏感上下文") == [0, 1]
        assert engine.model_error == "RuntimeError"
        assert engine.stats()["model_error"] == "RuntimeError"


def test_context_manager_closes_store_on_error_and_close_is_idempotent(tmp_path):
    engine = Engine(tmp_path, FlatModel(), learning=True)
    with pytest.raises(RuntimeError, match="caller failed"):
        with engine:
            engine.commit_external("shishi", "实施")
            store = engine._store
            raise RuntimeError("caller failed")
    engine.close()
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        store.stats()
