"""LLM 并发压测（生产化改造 P2-3.5 验收：并发 5 路稳定）。

零新依赖：httpx + asyncio，直接打 /api/agents/chat 全链路
（意图识别 LLM → 智能体路由 → LLM/RAG 生成 → SQLite 落库）。

用法（后端需以 DEMO_MODE=true 启动，避免鉴权摩擦）：
    python scripts/loadtest_llm_concurrency.py --url http://127.0.0.1:8010 \
        --concurrency 5 --rounds 3

输出：成功率、p50/p90/p95/max 延迟、QPS、逐请求明细、按智能体分组统计。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import dataclass, field

import httpx

# 覆盖不同智能体的典型问题（意图识别会路由到不同 agent）
QUESTIONS = [
    "我的高血压门诊用药能报销多少比例？",
    "最新的门诊慢特病政策有什么变化？",
    "最近总是头晕，可能是什么原因？",
    "脑电监测能发现什么问题？",
    "帮我看看这份出院小结的报销要点",
    "我需要长期服用华法林，有什么用药注意事项？",
    "癌症风险预测是怎么做的？",
    "体检报告里的血脂偏高要紧吗？",
]


@dataclass
class Sample:
    worker: int
    seq: int
    question: str
    status: int
    latency: float
    agent_type: str = ""
    resp_chars: int = 0
    error: str = ""


@dataclass
class Report:
    samples: list[Sample] = field(default_factory=list)
    t_start: float = 0.0
    t_end: float = 0.0


def pct(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(int(len(sorted_vals) * p / 100), len(sorted_vals) - 1)
    return sorted_vals[idx]


async def health_probe(client: httpx.AsyncClient) -> float:
    t = time.perf_counter()
    r = await client.get("/api/health")
    r.raise_for_status()
    return time.perf_counter() - t


async def worker(
    wid: int,
    rounds: int,
    client: httpx.AsyncClient,
    report: Report,
) -> None:
    for i in range(rounds):
        q = QUESTIONS[(wid + i) % len(QUESTIONS)]
        s = Sample(worker=wid, seq=i, question=q, status=0, latency=0.0)
        t = time.perf_counter()
        try:
            r = await client.post(
                "/api/agents/chat",
                json={"message": q, "user_id": f"user_{(wid % 8) + 1:03d}"},
            )
            s.latency = time.perf_counter() - t
            s.status = r.status_code
            if r.status_code == 200:
                body = r.json()
                s.agent_type = body.get("agent_type", "?")
                s.resp_chars = len(body.get("response") or "")
            else:
                s.error = r.text[:200]
        except Exception as e:  # noqa: BLE001 —— 压测要记录一切失败形态
            s.latency = time.perf_counter() - t
            s.error = f"{type(e).__name__}: {e}"
        report.samples.append(s)
        print(f"  w{wid}#{i} {s.status} {s.latency:6.1f}s {s.agent_type:22s} "
              f"{s.resp_chars:5d}字 {('ERR: ' + s.error) if s.error else ''}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8010")
    ap.add_argument("--concurrency", type=int, default=5)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--timeout", type=float, default=150.0)
    args = ap.parse_args()

    report = Report()
    async with httpx.AsyncClient(
        base_url=args.url, timeout=httpx.Timeout(args.timeout)
    ) as client:
        # 健康基线（区分 LLM 延迟与框架开销）
        h = await health_probe(client)
        print(f"[健康基线] GET /api/health -> {h*1000:.1f} ms")

        print(f"[压测开始] 并发={args.concurrency} 轮次/worker={args.rounds} "
              f"总请求={args.concurrency * args.rounds}")
        report.t_start = time.perf_counter()
        await asyncio.gather(*[
            worker(w, args.rounds, client, report)
            for w in range(args.concurrency)
        ])
        report.t_end = time.perf_counter()

    # ---------- 汇总 ----------
    total = report.samples
    ok = [s for s in total if s.status == 200 and not s.error]
    lat = sorted(s.latency for s in total)
    wall = report.t_end - report.t_start
    lines = [
        "",
        "=" * 62,
        "LLM 并发压测结果",
        "=" * 62,
        f"目标:          {args.url}/api/agents/chat（全链路，含意图识别+生成+落库）",
        f"并发 / 总请求: {args.concurrency} 路 / {len(total)} 请求（wall {wall:.1f}s）",
        f"成功率:        {len(ok)}/{len(total)} ({len(ok)/max(len(total),1)*100:.1f}%)",
        f"延迟 p50:      {pct(lat, 50):.1f}s",
        f"延迟 p90:      {pct(lat, 90):.1f}s",
        f"延迟 p95:      {pct(lat, 95):.1f}s",
        f"延迟 max:      {max(lat) if lat else 0:.1f}s",
        f"吞吐:          {len(total)/wall:.3f} req/s",
    ]

    # 按智能体分组
    by_agent: dict[str, list[float]] = {}
    for s in ok:
        by_agent.setdefault(s.agent_type, []).append(s.latency)
    if by_agent:
        lines.append("-" * 62)
        lines.append("按智能体分组（仅成功请求）:")
        for agent, ls in sorted(by_agent.items()):
            ls.sort()
            lines.append(f"  {agent:24s} n={len(ls)}  p50={pct(ls,50):.1f}s  "
                         f"max={max(ls):.1f}s")

    out = "\n".join(lines)
    print(out)
    with open("loadtest_result.json", "w", encoding="utf-8") as f:
        json.dump({
            "config": vars(args),
            "summary": {
                "success_rate": len(ok) / max(len(total), 1),
                "total": len(total),
                "ok": len(ok),
                "wall_s": wall,
                "p50": pct(lat, 50), "p90": pct(lat, 90),
                "p95": pct(lat, 95), "max": max(lat) if lat else 0,
                "qps": len(total) / wall,
            },
            "by_agent": {k: {"n": len(v), "p50": pct(sorted(v), 50), "max": max(v)}
                         for k, v in by_agent.items()},
            "samples": [vars(s) for s in total],
        }, f, ensure_ascii=False, indent=2)
    print("\n明细已写入 loadtest_result.json")


if __name__ == "__main__":
    asyncio.run(main())
