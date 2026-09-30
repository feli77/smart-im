import json
import sys

import pytest

from smart_im.cli import main


def test_cli_suggest(tmp_path, capsys):
    assert main(["--data-dir", str(tmp_path), "suggest", "nihao"]) == 0
    assert json.loads(capsys.readouterr().out)["candidates"][0]["text"] == "你好"


def test_cli_requires_explicit_reset_confirmation(tmp_path):
    with pytest.raises(SystemExit) as exc:
        main(["--data-dir", str(tmp_path), "reset"])
    assert exc.value.code == 2


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows platform guard")
def test_windows_has_actionable_platform_error(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["windows"])
    assert exc.value.code == 2
    assert "需要 Windows" in capsys.readouterr().err
