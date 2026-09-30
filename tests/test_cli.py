import json
import sys
from types import SimpleNamespace

import pytest

from smart_im.cli import main, parser
from smart_im.engine import Engine


def test_cli_rerank_defaults_to_local_ollama_qwen(tmp_path, capsys, monkeypatch):
    calls = []

    def rerank(model, context, texts, pinyin=""):
        assert model.model == "qwen3:1.7b"
        assert model.endpoint == "http://127.0.0.1:11434"
        assert model.timeout == 10.0
        calls.append((context, texts, pinyin))
        return [2, 0, 1]

    monkeypatch.setattr("smart_im.ollama_model.OllamaReranker.rerank", rerank)
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
    assert calls == [("我们计划", ["实时", "事实", "实施"], "shishi")]
    assert result == {"order": [2, 0, 1], "candidates": ["实施", "实时", "事实"]}
    assert not (tmp_path / "learning.sqlite3").exists()


def test_cli_without_context_keeps_originals_without_requesting_ollama(capsys, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("An empty-context request must not contact Ollama")

    monkeypatch.setattr("smart_im.ollama_model.OllamaReranker.rerank", unexpected)
    assert main(["rerank", "实时", "事实", "实施", "--pinyin", "shishi"]) == 0
    assert json.loads(capsys.readouterr().out)["order"] == [0, 1, 2]


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


@pytest.mark.parametrize("backend", ["tiny", "ollama"])
def test_removed_backend_option_is_rejected(backend):
    with pytest.raises(SystemExit) as exc:
        parser().parse_args(["serve", "--backend", backend])
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
        "--model",
        "qwen3:0.6b",
        "--model-timeout",
        "15",
    ]
    assert main(args) == 0
    assert calls == [("qwen3:0.6b", "http://127.0.0.1:11434", 15.0)]
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
                "--ollama-url",
                "https://example.com",
            ]
        )
        == 1
    )
    assert "local HTTP origin" in capsys.readouterr().err


@pytest.mark.parametrize("platform", ["win32", "linux"])
@pytest.mark.parametrize("interrupted", [False, True])
def test_cli_serve_enables_refresh_only_on_windows_without_learning(
    tmp_path, capsys, monkeypatch, platform, interrupted
):
    calls = []
    refresh = object()
    runtime = tmp_path / "runtime"

    def create_refresh():
        calls.append("refresh")
        return refresh

    class FakeService:
        def __init__(self, location, engine, *, refresh):
            assert location == runtime
            assert engine.learning is False
            assert engine.model.model == "qwen3:1.7b"
            calls.append(("service", refresh))

        def run(self):
            calls.append("run")
            if interrupted:
                raise KeyboardInterrupt

        def close(self):
            calls.append("close")

    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setitem(
        sys.modules,
        "smart_im.rime_refresh",
        SimpleNamespace(WindowsCandidateRefresh=create_refresh),
    )
    monkeypatch.setattr("smart_im.rime_service.MailboxService", FakeService)
    assert main(["--data-dir", str(tmp_path), "serve", "--runtime-dir", str(runtime)]) == 0
    expected = ["refresh"] if platform == "win32" else []
    expected.extend([("service", refresh if platform == "win32" else None), "run", "close"])
    assert calls == expected
    assert "服务已启动" in capsys.readouterr().err
    assert not (tmp_path / "learning.sqlite3").exists()
