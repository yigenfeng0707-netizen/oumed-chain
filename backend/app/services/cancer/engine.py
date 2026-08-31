"""泛癌卫士引擎：画像预测、队列对比与风险报告编排。

两种服务形态（`status()` 标识，前端据此显示徽标）：
- oncoformer：本地/带权重部署，真模型实时推理
- precomputed：云端轻量形态，读 cancer_precompute.py 的离线结果
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.config import settings
from app.services.cancer import cohort as cohort_svc
from app.services.cancer.model_provider import (
    CANCER_ZH,
    ModelUnavailableError,
    get_cancer_model,
)
from app.services.cancer.visit_synthesizer import synthesize_visits

logger = logging.getLogger(__name__)

DISCLAIMER = "基于 Oncoformer 研究模型的演示输出（温附医团队，Cell 2026），非临床诊断依据。"

# 患者级 max 概率 → 风险分层（演示口径）
_LEVELS = ((0.6, "高"), (0.3, "中"), (0.0, "低"))


def _level(p: float) -> str:
    for th, name in _LEVELS:
        if p >= th:
            return name
    return "低"


def status() -> dict[str, Any]:
    provider = get_cancer_model()
    cohort_json = cohort_svc.load_cohort_json()
    return {
        "agent": "泛癌卫士",
        "model": "Oncoformer (demo ckpt, 上游 Apache-2.0)",
        "engine": "oncoformer" if provider.available() else "precomputed",
        "model_loaded": provider.is_loaded(),
        "cohort_precomputed": cohort_json is not None,
        "cohort_patients": len(cohort_svc.list_patients()) if cohort_json else 0,
        "population": cohort_svc.cohort_stats(),
        "disclaimer": DISCLAIMER,
    }


async def predict_for_user(
    user_id: str,
    profile: dict[str, Any] | None,
) -> dict[str, Any]:
    """对平台用户做泛癌风险预测（真模型可用时），否则降级队列叙事。"""
    provider = get_cancer_model()
    profile = profile or {}
    if not provider.available():
        return _cohort_fallback_report(profile)

    df = synthesize_visits(profile, user_id=user_id)
    try:
        raw = await asyncio.to_thread(provider.predict_df, df, mode="ehr_only")
    except Exception as e:  # noqa: BLE001
        logger.warning("泛癌真模型推理失败，降级队列模式: %s", e)
        return _cohort_fallback_report(profile)

    return _build_report(
        engine="oncoformer",
        mode="ehr_only",
        source="synthetic_visits",
        raw=raw,
        profile=profile,
        note="就诊序列由平台档案合成为研究队列特征空间（模拟就诊序列）",
    )


def predict_cohort_patient(pid: str, modes: list[str] | None = None) -> dict[str, Any]:
    """队列患者预测：本地优先真模型实时，否则查预计算 JSON。"""
    modes = modes or ["fused", "ehr_only", "img_only"]
    provider = get_cancer_model()
    if provider.available() and cohort_svc.cohort_data_dir() is not None:
        try:
            return cohort_svc.realtime_predict(pid, modes)
        except (ModelUnavailableError, KeyError):
            pass
    pre = cohort_svc.get_precomputed(pid)
    if pre is None:
        raise KeyError(f"队列患者 {pid} 无预计算结果")
    filtered = {m: pre["modes"][m] for m in modes if m in pre["modes"]}
    return {
        "pid": pid,
        "engine": "oncoformer-precomputed",
        "modes": filtered,
        "meta": pre["meta"],
    }


# ----------------------------------------------------------------------
# 报告构建
# ----------------------------------------------------------------------

def _risk_rows(scores: dict[str, float]) -> list[dict[str, Any]]:
    rows = [
        {
            "cancer": name,
            "cancer_zh": CANCER_ZH.get(name, name),
            "prob": round(p, 4),
            "level": _level(p),
        }
        for name, p in scores.items()
    ]
    rows.sort(key=lambda r: r["prob"], reverse=True)
    return rows


def _build_report(
    *,
    engine: str,
    mode: str,
    source: str,
    raw: dict[str, Any],
    profile: dict[str, Any],
    note: str,
) -> dict[str, Any]:
    scores = raw["scores"]
    concurrent = _risk_rows(scores["concurrent"])
    future = _risk_rows(scores["future"])
    return {
        "engine": engine,
        "mode": mode,
        "source": source,
        "note": note,
        "n_visits": raw.get("n_visits"),
        "risks": {"concurrent": concurrent, "future": future},
        "top_risk": (future or concurrent)[0] if (future or concurrent) else None,
        "pred_age": raw.get("pred_age"),
        "profile_age": profile.get("age"),
        "disclaimer": DISCLAIMER,
    }


def _cohort_fallback_report(profile: dict[str, Any]) -> dict[str, Any]:
    """真模型不可用（云端轻量部署）时：用预计算队列给人群基线叙事。"""
    stats = cohort_svc.cohort_stats() or {}
    total = stats.get("total", 790)
    prevalence = stats.get("prevalence", {})
    rows = [
        {
            "cancer": name,
            "cancer_zh": CANCER_ZH.get(name, name),
            "prob": round(cnt / max(total, 1), 4),
            "level": "队列基线",
        }
        for name, cnt in sorted(prevalence.items(), key=lambda kv: -kv[1])
    ]
    return {
        "engine": "cohort_fallback",
        "mode": "cohort_fallback",
        "source": "compass_cohort",
        "note": "当前部署未加载真模型权重，以下为 790 例真实脱敏队列的患癌人群占比基线",
        "n_visits": None,
        "risks": {"concurrent": rows, "future": []},
        "top_risk": rows[0] if rows else None,
        "pred_age": None,
        "profile_age": profile.get("age"),
        "disclaimer": DISCLAIMER,
    }


# ----------------------------------------------------------------------
# 聊天文本渲染
# ----------------------------------------------------------------------

def format_report_text(report: dict[str, Any]) -> str:
    """把预测报告渲染为聊天回复（markdown 风格，与其它 agent 一致）。"""
    lines = ["**泛癌卫士 · Oncoformer 泛癌风险评估**", ""]
    if report["engine"] == "oncoformer":
        lines.append(f"基于您的健康档案合成 {report['n_visits']} 次就诊序列，真模型推理结果如下：")
    else:
        lines.append(f"{report['note']}：")
    lines.append("")

    sections = [("concurrent", "即时诊断风险（当前就诊）"), ("future", "未来风险（诊断前预测窗口）")]
    for key, title in sections:
        rows = report["risks"].get(key) or []
        if not rows:
            continue
        lines.append(f"**{title}**")
        for r in rows[:4]:
            pct = f"{r['prob'] * 100:.1f}%"
            lines.append(f"- {r['cancer_zh']}：{pct}（{r['level']}风险）")
        lines.append("")

    if report.get("pred_age") and report.get("profile_age"):
        lines.append(
            f"模型从就诊序列推断的年龄为 {report['pred_age']:.0f} 岁，"
            f"档案年龄 {report['profile_age']} 岁（推断偏差是模型可信度的参考信号之一）。"
        )
        lines.append("")
    lines.append(f"> {report['disclaimer']}")
    return "\n".join(lines)


__all__ = [
    "status",
    "predict_for_user",
    "predict_cohort_patient",
    "format_report_text",
    "DISCLAIMER",
]
