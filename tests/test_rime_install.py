import json
from pathlib import Path

import pytest
from test_ollama_model import ollama_server as ollama_server

from smart_im.cli import main
from smart_im.rime_install import install_rime


def test_install_keeps_user_configuration_and_is_idempotent(tmp_path):
    existing = tmp_path / "default.custom.yaml"
    existing.write_text("my settings", encoding="utf-8")
    result = install_rime(tmp_path)
    assert len(result["installed"]) == 2
    assert all(Path(file).is_file() for file in result["installed"])
    assert (tmp_path / "smart_im_runtime").is_dir()
    assert existing.read_text("utf-8") == "my settings"
    assert not (tmp_path / "rime.lua").exists()
    assert install_rime(tmp_path)["backups"] == []


def test_conflict_preflight_does_not_partially_install(tmp_path):
    (tmp_path / "lua").mkdir()
    own_file = tmp_path / "lua" / "smart_im.lua"
    own_file.write_text("custom user edits", encoding="utf-8")
    with pytest.raises(ValueError, match="--force"):
        install_rime(tmp_path)
    assert not (tmp_path / "smart_im.schema.yaml").exists()
    assert own_file.read_text("utf-8") == "custom user edits"
    result = install_rime(tmp_path, force=True)
    assert len(result["backups"]) == 1
    assert Path(result["backups"][0]).read_text("utf-8") == "custom user edits"


def test_install_cli(tmp_path, capsys):
    assert main(["install-rime", "--user-dir", str(tmp_path)]) == 0
    assert "Smart IM" in capsys.readouterr().out


def test_rerank_cli_is_permutation(tmp_path, capsys, ollama_server):
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
                "--ollama-url",
                ollama_server.endpoint,
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result == {"order": [1, 0, 2], "candidates": ["事实", "实时", "实施"]}
    assert len(ollama_server.requests) == 1
    path, _, payload = ollama_server.requests[0]
    assert path == "/api/chat"
    assert payload["model"] == "qwen3:1.7b"
    assert json.loads(payload["messages"][1]["content"])["context"] == "我们计划"
    assert not (tmp_path / "learning.sqlite3").exists()
