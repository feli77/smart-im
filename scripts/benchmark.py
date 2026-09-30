"""Reproducible engine-only microbenchmark; no personal data or network access."""

import argparse
import json
import platform
import statistics
import time
from pathlib import Path

from smart_im.engine import Engine


def summarize(values):
    values = sorted(values)
    return {
        "n": len(values),
        "p50_ms": round(statistics.median(values), 3),
        "p95_ms": round(values[int(len(values) * 0.95) - 1], 3),
        "max_ms": round(max(values), 3),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    queries = [
        "nihao",
        "zhongguo",
        "jintiankaishi",
        "shishi",
        "nh",
        "nihoa",
        "wozhidao",
        "womenjintianqushangban",
        "xian",
        "xi'an",
    ]
    contexts = [
        "我们计划",
        "系统支持",
        "尊重",
        "我认为",
        "今天",
        "项目",
        "这个问题",
        "明天",
        "工作",
        "",
    ]
    started = time.perf_counter()
    with Engine() as engine:
        startup = (time.perf_counter() - started) * 1000
        first, repeated, predictions = [], [], []
        for context in contexts:
            for pinyin in queries:
                started = time.perf_counter()
                engine.suggest(pinyin, context, private=True)
                first.append((time.perf_counter() - started) * 1000)
        for index in range(1000):
            started = time.perf_counter()
            engine.suggest(queries[index % 10], contexts[(index // 10) % 10], private=True)
            repeated.append((time.perf_counter() - started) * 1000)
        for index in range(100):
            started = time.perf_counter()
            engine.predict(contexts[index % 10], private=True)
            predictions.append((time.perf_counter() - started) * 1000)
    result = {
        "platform": platform.system() + " " + platform.machine(),
        "python": platform.python_version(),
        "startup_ms": round(startup, 3),
        "first_pass_queries": summarize(first),
        "repeated_queries": summarize(repeated),
        "predictions": summarize(predictions),
        "notes": "Engine-only, shipped tiny model, learning disabled; excludes UI, native hooks and Windows real-device latency.",
    }
    output = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    print(output, end="")


if __name__ == "__main__":
    main()
