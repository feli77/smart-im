"""Local command-line access to every core capability."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from dataclasses import asdict
from pathlib import Path


def parser() -> argparse.ArgumentParser:
    app = argparse.ArgumentParser(description="灵序 Smart IM · 本地中文输入法")
    app.add_argument("--data-dir", help="本地学习数据目录")
    app.add_argument("--learn", action="store_true", help="开启个性化统计读写（默认关闭）")
    app.add_argument("--model", metavar="LOCAL_GGUF", help="可选本地 GGUF 权重文件")
    commands = app.add_subparsers(dest="command", required=True)
    for name in ("suggest", "predict", "commit", "correct"):
        cmd = commands.add_parser(name)
        cmd.add_argument("text", help="拼音 / 上下文 / 上屏文本 / 待检查文本")
        if name in ("suggest", "commit"):
            cmd.add_argument("--context", default="")
        if name == "commit":
            cmd.add_argument("--pinyin", default="")
        if name in ("suggest", "predict"):
            cmd.add_argument("--limit", type=int, default=5)
        if name != "correct":
            cmd.add_argument("--private", action="store_true", help="本次不读取或保存个人统计")
    commands.add_parser("desktop", help="打开原生桌面体验台")
    commands.add_parser("windows", help="旧版钩子浮窗演示；日常使用推荐 Rime + serve")
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
    bench = commands.add_parser("benchmark", help="离线热态候选基准（不写学习数据）")
    bench.add_argument("--iterations", type=int, default=100)
    return app


def emit(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    app = parser()
    args = app.parse_args(argv)
    if args.command == "windows" and sys.platform != "win32":
        app.error("Windows 浮窗需要 Windows；当前系统可使用 desktop 或 suggest。")
    if args.command == "reset" and not args.yes:
        app.error("清空学习数据需要 reset --yes。")
    if args.command == "benchmark" and not 1 <= args.iterations <= 10000:
        app.error("iterations 必须介于 1 和 10000。")
    try:
        if args.command == "install-rime":
            from .rime_install import install_rime

            emit(install_rime(args.user_dir, force=args.force))
            return 0
        model = None
        if args.model:
            from .models import GGUFModel

            model = GGUFModel(args.model)
        if args.command in ("desktop", "windows"):
            if args.command == "desktop":
                from .desktop import main as launch
            else:
                from .windows import main as launch
            return launch(data_dir=args.data_dir, model=model, learning=args.learn) or 0
        from .engine import Engine

        with Engine(args.data_dir, model=model, learning=args.learn) as engine:
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
            elif args.command == "suggest":
                emit(asdict(engine.suggest(args.text, args.context, args.limit, args.private)))
            elif args.command == "predict":
                emit([asdict(c) for c in engine.predict(args.text, args.limit, args.private)])
            elif args.command == "correct":
                emit([asdict(c) for c in engine.correct(args.text)])
            elif args.command == "commit":
                engine.commit(args.pinyin, args.text, args.context, args.private)
                emit({"learning_enabled": args.learn and not args.private, "stats": engine.stats()})
            elif args.command == "stats":
                emit(engine.stats())
            elif args.command == "reset":
                engine.clear_learning()
                emit(engine.stats())
            elif args.command == "benchmark":
                queries = ["nihao", "zhongguo", "jintiankaishi", "shishi", "nh", "nihoa"]
                for query in queries:
                    engine.suggest(query, "我们", private=True)
                durations = []
                for i in range(args.iterations):
                    started = time.perf_counter()
                    engine.suggest(queries[i % len(queries)], "我们", private=True)
                    durations.append((time.perf_counter() - started) * 1000)
                durations.sort()
                emit(
                    {
                        "model": engine.model.name,
                        "iterations": len(durations),
                        "warm": True,
                        "context": "我们",
                        "queries": queries,
                        "p50_ms": round(statistics.median(durations), 3),
                        "p95_ms": round(durations[max(0, math.ceil(len(durations) * 0.95) - 1)], 3),
                        "max_ms": round(max(durations), 3),
                    }
                )
    except (ImportError, OSError, ValueError, RuntimeError) as exc:
        print(f"Smart IM: {exc}", file=sys.stderr)
        return 1
    return 0
