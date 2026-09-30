"""Install and run the Rime adapter, inspect ranking, and manage local learning."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def parser() -> argparse.ArgumentParser:
    app = argparse.ArgumentParser(description="灵序 Smart IM · Rime 本地候选重排")
    app.add_argument("--data-dir", help="本地学习数据目录")
    app.add_argument("--learn", action="store_true", help="开启个性化统计读写（默认关闭）")
    commands = app.add_subparsers(dest="command", required=True)
    rerank = commands.add_parser("rerank", help="仅重排外部候选（Rime 路线的核心接口）")
    rerank.add_argument("texts", nargs="+")
    rerank.add_argument("--context", default="")
    rerank.add_argument("--pinyin", default="")
    rerank.add_argument("--private", action="store_true")
    install = commands.add_parser("install-rime", help="安装独立 Rime 方案与 Lua 适配")
    install.add_argument(
        "--user-dir", type=Path, help="Rime 用户目录；Windows 默认为 %%APPDATA%%/Rime"
    )
    install.add_argument("--force", action="store_true", help="备份已有不同内容后更新本项目文件")
    serve = commands.add_parser("serve", help="运行 Rime 的本地 AI 后台服务")
    location = serve.add_mutually_exclusive_group()
    location.add_argument("--user-dir", type=Path, help="Rime 用户目录")
    location.add_argument("--runtime-dir", type=Path, help="直接指定信箱目录，用于测试或自定义部署")
    commands.add_parser("stats", help="查看本地统计")
    reset = commands.add_parser("reset", help="清空本地学习统计")
    reset.add_argument("--yes", action="store_true", help="确认清空")
    return app


def emit(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    app = parser()
    args = app.parse_args(argv)
    if args.command == "reset" and not args.yes:
        app.error("清空学习数据需要 reset --yes。")
    try:
        if args.command == "install-rime":
            from .rime_install import install_rime

            emit(install_rime(args.user_dir, force=args.force))
            return 0
        from .engine import Engine

        with Engine(args.data_dir, learning=args.learn) as engine:
            if args.command == "serve":
                from .rime_install import runtime_dir
                from .rime_service import MailboxService

                location = args.runtime_dir or runtime_dir(args.user_dir)
                service = MailboxService(location, engine)
                print(f"Smart IM 服务已启动：{location}；Ctrl+C 退出。", file=sys.stderr)
                try:
                    service.run()
                except KeyboardInterrupt:
                    pass
                finally:
                    service.close()
            elif args.command == "rerank":
                order = engine.rerank(args.texts, args.context, args.pinyin, args.private)
                emit({"order": order, "candidates": [args.texts[index] for index in order]})
            elif args.command == "stats":
                emit(engine.stats())
            elif args.command == "reset":
                engine.clear_learning()
                emit(engine.stats())
    except (ImportError, OSError, ValueError, RuntimeError) as exc:
        print(f"Smart IM: {exc}", file=sys.stderr)
        return 1
    return 0
