import math
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from smart_im.models import GGUFModel, LocalLanguageModel


@pytest.fixture(scope="module")
def model():
    return LocalLanguageModel()


def test_neural_weights_are_present_and_context_disambiguates(model):
    assert model.parameter_count > 20_000
    assert model.score("尊重", "事实") > model.score("尊重", "实时")
    assert model.score("系统支持", "实时") > model.score("系统支持", "事实")
    assert model.score("我们计划", "实施") > model.score("我们计划", "事实")


def test_unknown_characters_are_finite_and_scores_are_normalized(model):
    assert math.isfinite(model.score("😀🫖", "𠮷🙂"))
    assert model.score("", "") == 0
    assert model.score("", "你好") <= 0


@pytest.mark.parametrize("context", ["我们", "今天", "明天", "项目"])
def test_phrase_predictions_are_continuations(model, context):
    candidates = model.predict(context, limit=3)
    assert 0 < len(candidates) <= 3
    assert all(
        candidate.text and not candidate.text.startswith(context) for candidate in candidates
    )
    assert all(candidate.source == "model" for candidate in candidates)


def test_longest_context_wins_over_generic_suffix(model):
    assert all(c.text.startswith("附件") for c in model.predict("请查收"))
    assert model.predict("") == []
    assert model.predict("我们", 0) == []
    assert model.predict("🫖🫖") == []


def test_caches_bound_input_memory(model):
    for index in range(model.MAX_CACHE + 20):
        model.score(f"{index:04d}", "你")
    assert len(model._scores) <= model.MAX_CACHE
    assert len(model._distributions) <= model.MAX_CACHE


def test_missing_weights_are_actionable(tmp_path):
    with pytest.raises(FileNotFoundError, match="train_tiny_lm.py"):
        LocalLanguageModel(tmp_path / "missing.npz")


def test_gguf_requires_a_local_file(tmp_path):
    with pytest.raises(ValueError, match="existing local"):
        GGUFModel(tmp_path / "missing.gguf")


def test_gguf_missing_dependency_has_install_instructions(tmp_path, monkeypatch):
    path = tmp_path / "local.gguf"
    path.write_bytes(b"test double")
    monkeypatch.setitem(sys.modules, "llama_cpp", None)
    with pytest.raises(RuntimeError, match=r"smart-im\[gguf\]"):
        GGUFModel(path)


def test_gguf_token_budget_and_likelihood_indexing(tmp_path, monkeypatch):
    """Exercise the adapter contract without pretending to load a real model."""

    class FakeLlama:
        def __init__(self, **kwargs):
            self.context_size = kwargs["n_ctx"]
            self.scores = np.zeros((self.context_size, 3), dtype=float)
            self.last_eval = []
            self.last_completion = {}

        def tokenize(self, data, add_bos):
            return ([0] if add_bos else []) + [1] * len(data.decode("utf-8"))

        def reset(self):
            pass

        def token_bos(self):
            return 0

        def eval(self, tokens):
            assert len(tokens) <= self.context_size
            self.last_eval = tokens
            self.scores[:] = [0, 2, 0]

        def create_completion(self, **kwargs):
            assert len(kwargs["prompt"]) + kwargs["max_tokens"] <= self.context_size
            self.last_completion = kwargs
            return {"choices": [{"text": "下午开会"}]}

    monkeypatch.setitem(sys.modules, "llama_cpp", SimpleNamespace(Llama=FakeLlama))
    local_file = tmp_path / "fake.gguf"
    local_file.write_bytes(b"test double, not actual GGUF")
    gguf = GGUFModel(local_file, context_size=128)
    expected = 2 - math.log(math.exp(2) + 2)
    assert gguf.score("我们" * 500, "今天" * 100) == pytest.approx(expected)
    assert gguf.predict("我们" * 500)[0].text == "下午开会"
    assert gguf._model.last_completion["temperature"] == 0
