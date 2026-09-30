import math

import pytest

from smart_im.models import LocalLanguageModel


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


def test_caches_bound_input_memory(model):
    for index in range(model.MAX_CACHE + 20):
        model.score(f"{index:04d}", "你")
    assert len(model._scores) <= model.MAX_CACHE
    assert len(model._distributions) <= model.MAX_CACHE


def test_missing_weights_are_actionable(tmp_path):
    with pytest.raises(FileNotFoundError, match="train_tiny_lm.py"):
        LocalLanguageModel(tmp_path / "missing.npz")
