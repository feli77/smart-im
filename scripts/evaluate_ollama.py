"""Diagnose the current Ollama reranker with fixed, manually labelled candidates.

Run: uv run python scripts/evaluate_ollama.py --model qwen3:1.7b --repeat 2
Requires an already running local Ollama and an already installed model. This
script never downloads models or captures text from the input method.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

from smart_im.ollama_model import OllamaError, OllamaReranker

DEFAULT_CASES = Path(__file__).resolve().parents[1] / "tests" / "data" / "ollama_cases.json"
LIMITATION = (
    "人工标注的小样本诊断集，包含已用于排查的例子，不是独立验证集或代表性准确率。"
    "仅测固定候选池的 OllamaReranker.rerank；不包含 Rime 候选召回、上下文提取、"
    "缓存、输入事件或界面刷新，不代表完整输入链路延迟。重复调用不是新增独立样本。"
)


def load_cases(path: Path) -> list[dict]:
    document = json.loads(path.read_text(encoding="utf-8"))
    cases = document.get("cases") if isinstance(document, dict) else None
    if not isinstance(cases, list) or not cases:
        raise ValueError("Cases file must contain a nonempty 'cases' array")
    seen = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("Each case must be an object")
        case_id = case.get("id")
        context = case.get("context")
        pinyin = case.get("pinyin")
        candidates = case.get("candidates")
        expected = case.get("expected_first")
        if (
            not isinstance(case_id, str)
            or not case_id
            or case_id in seen
            or not isinstance(context, str)
            or not context.strip()
            or len(context) > OllamaReranker.MAX_CONTEXT
            or not isinstance(pinyin, str)
            or not pinyin
            or len(pinyin) > OllamaReranker.MAX_PINYIN
            or not isinstance(candidates, list)
            or not 2 <= len(candidates) <= OllamaReranker.MAX_CANDIDATES
            or any(
                not isinstance(text, str) or not 1 <= len(text) <= OllamaReranker.MAX_TEXT
                for text in candidates
            )
            or not isinstance(expected, list)
            or not expected
            or any(not isinstance(text, str) or text not in candidates for text in expected)
        ):
            raise ValueError("Invalid case id, context, pinyin, candidates, or expected_first")
        seen.add(case_id)
    return cases


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def evaluate(model: OllamaReranker, cases: list[dict], repeat: int) -> dict:
    runs = []
    for repetition in range(1, repeat + 1):
        for case in cases:
            started = time.perf_counter()
            selected = None
            error = None
            try:
                order = model.rerank(case["context"], case["candidates"], case["pinyin"])
                selected = case["candidates"][order[0]]
            except OllamaError as exc:
                # OllamaError messages intentionally contain no prompt/response text.
                error = str(exc)
            elapsed_ms = (time.perf_counter() - started) * 1000
            row = {
                "id": case["id"],
                "repeat": repetition,
                "selected": selected,
                "hit": selected in case["expected_first"] if error is None else False,
                "baseline_hit": case["candidates"][0] in case["expected_first"],
                "elapsed_ms": round(elapsed_ms, 3),
                "error": error,
            }
            runs.append(row)
            status = "ERROR" if error else "HIT" if row["hit"] else "MISS"
            print(
                f"[{len(runs):>3}/{len(cases) * repeat}] {case['id']} "
                f"repeat={repetition} {status} {elapsed_ms:.1f} ms",
                flush=True,
            )

    subsequent = [row["elapsed_ms"] for row in runs[1:]]
    count = len(runs)
    hits = sum(row["hit"] for row in runs)
    baseline_hits = sum(row["baseline_hit"] for row in runs)
    errors = sum(row["error"] is not None for row in runs)
    return {
        "limitation": LIMITATION,
        "model": model.model,
        "endpoint": model.endpoint,
        "timeout_seconds": model.timeout,
        "distinct_cases": len(cases),
        "repeat": repeat,
        "calls": count,
        "top1_hits": hits,
        "top1_hit_rate": hits / count,
        "baseline_top1_hits": baseline_hits,
        "baseline_top1_hit_rate": baseline_hits / count,
        "request_errors": errors,
        "request_error_rate": errors / count,
        "first_call": runs[0],
        "subsequent_latency_ms_including_errors": {
            "count": len(subsequent),
            "p50": percentile(subsequent, 0.5),
            "p95": percentile(subsequent, 0.95),
            "max": max(subsequent) if subsequent else None,
        },
        "runs": runs,
    }


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", help="installed Ollama model; default: current reranker default")
    parser.add_argument(
        "--repeat", type=positive_int, default=1, help="repeat the same cases (default: 1)"
    )
    parser.add_argument(
        "--cases", type=Path, default=DEFAULT_CASES, help="labelled JSON cases file"
    )
    parser.add_argument(
        "--output", type=Path, help="explicitly save a JSON report; default: no file"
    )
    args = parser.parse_args()
    try:
        cases = load_cases(args.cases)
        model = OllamaReranker(model=args.model) if args.model else OllamaReranker()
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    print(LIMITATION)
    print(f"Model: {model.model}; distinct cases: {len(cases)}; repeat: {args.repeat}")
    report = evaluate(model, cases, args.repeat)
    count = report["calls"]
    print(
        f"首选命中: {report['top1_hits']}/{count} ({report['top1_hit_rate']:.1%}); "
        f"原序基线: {report['baseline_top1_hits']}/{count} "
        f"({report['baseline_top1_hit_rate']:.1%})"
    )
    print(
        f"请求/解析错误率: {report['request_errors']}/{count} "
        f"({report['request_error_rate']:.1%})；错误计入首选未命中。"
    )
    first = report["first_call"]
    print(
        f"首个调用单列: {first['elapsed_ms']:.1f} ms ({first['id']}); "
        "可能包含模型加载，但本脚本不卸载模型，不能据此认定为冷启动。"
    )
    latency = report["subsequent_latency_ms_including_errors"]
    if latency["count"]:
        print(
            f"后续 {latency['count']} 次总调用耗时（含失败）: "
            f"p50={latency['p50']:.1f} ms; p95={latency['p95']:.1f} ms; "
            f"max={latency['max']:.1f} ms"
        )
    else:
        print("后续调用 p50/p95: N/A（只有一次调用）")
    if args.output:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", "utf-8")
        print(f"Report saved: {args.output}")
    return 1 if report["request_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
