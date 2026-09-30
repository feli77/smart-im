"""Install only Smart IM's two assets; never patch an existing Rime scheme."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def default_rime_user_dir() -> Path:
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if not appdata:
            raise ValueError("APPDATA 未设置，请使用 --user-dir 指定 Rime 用户目录。")
        return Path(appdata) / "Rime"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Rime"
    raise ValueError("请通过 --user-dir 指定 Rime 用户目录（不同 Linux 前端路径不同）。")


def runtime_dir(user_dir: Path | str | None = None) -> Path:
    return (
        Path(user_dir).expanduser() if user_dir is not None else default_rime_user_dir()
    ) / "smart_im_runtime"


def install_rime(user_dir: Path | str | None = None, *, force: bool = False) -> dict:
    """Preflight collisions before writing, and back up changed owned files.

    This does not install Weasel, deploy dictionaries, select an input scheme,
    or modify ``default.custom.yaml`` / ``rime.lua``.
    """
    destination = Path(user_dir).expanduser() if user_dir is not None else default_rime_user_dir()
    destination = destination.resolve()
    assets = Path(__file__).parent / "rime_assets"
    relative_paths = (Path("smart_im.schema.yaml"), Path("lua/smart_im.lua"))
    payloads = {relative: (assets / relative).read_bytes() for relative in relative_paths}
    changed = []
    for relative, payload in payloads.items():
        target = destination / relative
        if target.is_symlink() or target.parent.is_symlink():
            raise ValueError(f"拒绝覆盖符号链接：{target}")
        if target.exists() and target.read_bytes() != payload:
            changed.append(relative)
    if changed and not force:
        names = ", ".join(str(name) for name in changed)
        raise ValueError(f"目标文件已有不同内容：{names}。使用 --force 备份后更新。")

    backups = []
    for relative, payload in payloads.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if relative in changed:
            number = 1
            backup = target.with_name(target.name + ".bak")
            while backup.exists():
                number += 1
                backup = target.with_name(target.name + f".bak{number}")
            # Exclusive create prevents overwriting a concurrently created backup.
            with backup.open("xb") as file:
                file.write(target.read_bytes())
            backups.append(str(backup))
        if target.exists() and target.read_bytes() == payload:
            continue
        fd, temporary_name = tempfile.mkstemp(
            prefix=target.name + ".", suffix=".tmp", dir=target.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(payload)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    bus = runtime_dir(destination)
    bus.mkdir(parents=True, exist_ok=True, mode=0o700)
    return {
        "user_dir": str(destination),
        "runtime_dir": str(bus),
        "installed": [str(destination / relative) for relative in relative_paths],
        "backups": backups,
        "next_step": "在小狼毫输入法设定中勾选 Smart IM 并重新部署，然后运行 smart-im serve。",
    }
