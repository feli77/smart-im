from pathlib import Path

import pytest

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


def test_rerank_cli_is_permutation(capsys):
    import json

    assert main(["rerank", "实时", "事实", "实施", "--context", "我们计划"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert sorted(result["order"]) == [0, 1, 2]
    assert sorted(result["candidates"]) == sorted(["实时", "事实", "实施"])
