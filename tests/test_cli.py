import json

import pytest

from smart_im.cli import main, parser
from smart_im.engine import Engine


def test_cli_rerank_uses_bundled_model(tmp_path, capsys):
    assert (
        main(
            [
                "--data-dir",
                str(tmp_path),
                "rerank",
                "实时",
                "事实",
                "实施",
                "--context",
                "我们计划",
                "--pinyin",
                "shishi",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result == {"order": [2, 0, 1], "candidates": ["实施", "实时", "事实"]}
    assert not (tmp_path / "learning.sqlite3").exists()


def test_cli_stats_and_reset_existing_learning(tmp_path, capsys):
    with Engine(tmp_path, learning=True) as engine:
        engine.commit_external("shishi", "实施", "执行")
    assert main(["--data-dir", str(tmp_path), "stats"]) == 0
    assert json.loads(capsys.readouterr().out)["selections"] == 1
    assert main(["--data-dir", str(tmp_path), "reset", "--yes"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["selections"] == result["phrases"] == result["contexts"] == 0


def test_cli_requires_explicit_reset_confirmation(tmp_path):
    with pytest.raises(SystemExit) as exc:
        main(["--data-dir", str(tmp_path), "reset"])
    assert exc.value.code == 2


@pytest.mark.parametrize(
    "command",
    [
        "suggest",
        "predict",
        "correct",
        "commit",
        "desktop",
        "windows",
        "benchmark",
    ],
)
def test_removed_commands_are_rejected(command):
    with pytest.raises(SystemExit) as exc:
        parser().parse_args([command])
    assert exc.value.code == 2


def test_removed_model_option_is_rejected():
    with pytest.raises(SystemExit) as exc:
        parser().parse_args(["--model", "model.gguf", "stats"])
    assert exc.value.code == 2


def test_cli_selects_ollama_and_reports_model_failure(tmp_path, capsys, monkeypatch):
    calls = []

    class BatchModel:
        name = "Ollama test (local)"
        fail = False

        def __init__(self, model, endpoint, timeout):
            calls.append((model, endpoint, timeout))

        def rerank(self, context, texts, pinyin=""):
            if self.fail:
                raise RuntimeError("private input must never be logged")
            return [1, 0]

    monkeypatch.setattr("smart_im.ollama_model.OllamaReranker", BatchModel)
    args = [
        "--data-dir",
        str(tmp_path),
        "rerank",
        "事实",
        "实施",
        "--context",
        "执行",
        "--backend",
        "ollama",
        "--model",
        "qwen3:1.7b",
        "--model-timeout",
        "15",
    ]
    assert main(args) == 0
    assert calls == [("qwen3:1.7b", "http://127.0.0.1:11434", 15.0)]
    assert json.loads(capsys.readouterr().out)["order"] == [1, 0]
    BatchModel.fail = True
    assert main(args) == 1
    output = capsys.readouterr()
    assert json.loads(output.out)["order"] == [0, 1]
    assert "模型不可用" in output.err
    assert "private input" not in output.err


def test_cli_rejects_remote_ollama_endpoint_before_sending_input(capsys):
    assert (
        main(
            [
                "rerank",
                "事实",
                "实施",
                "--context",
                "执行",
                "--backend",
                "ollama",
                "--ollama-url",
                "https://example.com",
            ]
        )
        == 1
    )
    assert "local HTTP origin" in capsys.readouterr().err
