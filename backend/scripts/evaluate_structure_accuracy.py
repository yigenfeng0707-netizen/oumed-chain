"""病历结构化字段级准确率评测（P2.5 生产化验收：准确率 ≥95%）。

对 data/structure_eval_set.json（200 条合成入院记录 + 字段级标注）逐条
执行结构化提取，并按 app/services/structure_eval.py 的口径做字段级对照：
    总体字段准确率 = 正确叶子字段数 / 总叶子字段数（每条 9 个叶子字段）

默认走真实治理链路 governance.llm_structure（本地 Ollama 优先 → 云端 →
规则兜底），并发受控（默认 5 路，与并发验收档一致）；--extractor rules
可离线冒烟（仅规则引擎，不调模型）。

用法（在 backend 目录）：
    python scripts/evaluate_structure_accuracy.py --limit 20          # 试跑
    python scripts/evaluate_structure_accuracy.py                     # 全量 200 条
    python scripts/evaluate_structure_accuracy.py --extractor rules   # 离线冒烟

输出：分字段准确率表 + 总体准确率 + 提取器分布，明细写入
structure_eval_result.json（同目录，不入库）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # noqa: E402

from app.services import governance  # noqa: E402
from app.services.structure_eval import FIELD_NAMES, score_record, summarize  # noqa: E402

DEFAULT_DATASET = "../data/structure_eval_set.json"


async def evaluate(dataset_path: str, limit: int | None, concurrency: int,
                   extractor: str) -> tuple[dict, list]:
    data = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    records = data["records"][: limit] if limit else data["records"]
    print(f"[评测] 数据集={dataset_path} 记录数={len(records)} "
          f"提取器={extractor} 并发={concurrency}")

    sem = asyncio.Semaphore(max(1, concurrency))
    progress = {"done": 0, "ok": 0}

    def extract(note: str):
        if extractor == "rules":
            return governance.rule_structure(note)
        return governance.llm_structure(note)

    async def run_one(rec: dict) -> dict:
        async with sem:
            t0 = time.perf_counter()
            try:
                pred = await asyncio.to_thread(extract, rec["note"])
            except Exception as e:  # noqa: BLE001 —— 单条失败计 0 分，不中断评测
                print(f"  [{rec['id']}] 提取异常: {type(e).__name__}: {e}")
                pred = None
            elapsed = time.perf_counter() - t0

        scored = score_record(pred, rec["gold"])
        extractor_name = (pred or {}).get("extractor", "failed")
        progress["done"] += 1
        progress["ok"] += scored["correct"]
        if progress["done"] % 10 == 0 or progress["done"] == len(records):
            print(f"  进度 {progress['done']}/{len(records)}  "
                  f"累计字段得分 {progress['ok']}/"
                  f"{progress['done'] * scored['total']}  "
                  f"末条 {elapsed:.1f}s ({extractor_name})")
        return {
            "id": rec["id"], "extractor": extractor_name,
            "elapsed_s": round(elapsed, 2), "scored": scored,
            "pred": pred, "gold": rec["gold"],
        }

    t_start = time.perf_counter()
    results = list(await asyncio.gather(*[run_one(r) for r in records]))
    wall = time.perf_counter() - t_start

    summary = summarize([r["scored"] for r in results])
    summary["config"] = {
        "dataset": str(dataset_path), "records": len(records),
        "concurrency": concurrency, "extractor": extractor,
        "wall_s": round(wall, 1),
        "avg_latency_s": round(sum(r["elapsed_s"] for r in results) / max(len(results), 1), 1),
    }
    summary["extractor_distribution"] = dict(Counter(r["extractor"] for r in results))
    return summary, results


def print_report(summary: dict) -> None:
    cfg, pf = summary["config"], summary["per_field"]
    lines = ["", "=" * 60, "病历结构化字段级准确率评测（P2.5 验收线 ≥95%）", "=" * 60]
    lines.append(f"记录数: {cfg['records']}  提取器: {cfg['extractor']}  "
                 f"并发: {cfg['concurrency']}  总耗时: {cfg['wall_s']}s  "
                 f"平均单条: {cfg['avg_latency_s']}s")
    lines.append(f"提取器分布: {summary['extractor_distribution']}")
    lines.append("-" * 60)
    for name in FIELD_NAMES:
        bar = "█" * round(pf[name] * 20)
        lines.append(f"  {name:22s} {pf[name] * 100:6.2f}%  {bar}")
    lines.append("-" * 60)
    acc = summary["overall_accuracy"]
    lines.append(f"总体字段准确率: {summary['total_correct']}/{summary['total_fields']} "
                 f"= {acc * 100:.2f}%  （验收线 95%：{'✅ 达标' if acc >= 0.95 else '❌ 未达标'}）")
    print("\n".join(lines))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=DEFAULT_DATASET)
    ap.add_argument("--limit", type=int, default=None, help="只评测前 N 条（试跑）")
    ap.add_argument("--concurrency", type=int, default=5)
    ap.add_argument("--extractor", choices=["auto", "rules"], default="auto",
                    help="auto=真实治理链路（本地LLM→云端→规则）；rules=仅规则引擎离线冒烟")
    ap.add_argument("--output", default="structure_eval_result.json")
    args = ap.parse_args()

    summary, results = await evaluate(args.dataset, args.limit, args.concurrency, args.extractor)
    print_report(summary)
    Path(args.output).write_text(
        json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print(f"\n明细已写入 {Path(args.output).resolve()}")


if __name__ == "__main__":
    asyncio.run(main())
