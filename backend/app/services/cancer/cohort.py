"""COMPASS 示例队列访问层（双形态）。

- 预计算形态（云端/无 torch）：读 data/cancer_cohort.json（由
  backend/scripts/cancer_precompute.py 用真模型离线生成，随部署走）。
- 实时形态（本地装 torch + 设置 ONCOFORMER_DATA_DIR）：直接对队列
  parquet 行跑 Oncoformer 三模态推理。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.config import settings

from .model_provider import (
    CANCER_COLS,
    ModelUnavailableError,
    cancer_names_zh,
    cohort_data_dir,
    get_cancer_model,
)

logger = logging.getLogger(__name__)


def _repo_root() -> Path:
    # backend/app/services/cancer/cohort.py → 仓库根
    return Path(__file__).resolve().parents[4]


def _json_path() -> Path:
    override = (settings.ONCOFORMER_COHORT_JSON or "").strip()
    return Path(override) if override else _repo_root() / "data" / "cancer_cohort.json"


_cache: dict[str, Any] | None = None


def load_cohort_json() -> dict[str, Any] | None:
    """读预计算结果；文件缺失或损坏返回 None。"""
    global _cache
    if _cache is not None:
        return _cache
    p = _json_path()
    if not p.exists():
        return None
    try:
        _cache = json.loads(p.read_text(encoding="utf-8"))
        return _cache
    except Exception as e:  # noqa: BLE001
        logger.warning("预计算队列 JSON 读取失败: %s", e)
        return None


def cohort_stats() -> dict[str, Any] | None:
    data = load_cohort_json()
    return data.get("population") if data else None


def list_patients() -> list[dict[str, Any]]:
    data = load_cohort_json()
    if not data:
        return []
    return data.get("patients", [])


def get_precomputed(pid: str) -> dict[str, Any] | None:
    for p in list_patients():
        if p.get("pid") == pid:
            return p
    return None


_parquet_cache: Any = None


def _load_metadata():
    """加载上游 metadata.parquet（仅本地实时模式需要 pyarrow）。"""
    global _parquet_cache
    data_dir = cohort_data_dir()
    if data_dir is None:
        return None
    if _parquet_cache is None:
        from .oncoformer_lib.Utils import load_parquet

        path = data_dir / "metadata.parquet"
        if not path.exists():
            logger.warning("队列 metadata 不存在: %s", path)
            return None
        _parquet_cache = load_parquet(str(path))
    return _parquet_cache


def realtime_predict(pid: str, modes: list[str] | None = None) -> dict[str, Any]:
    """对队列中一个真实脱敏患者跑真模型三模态推理（本地）。"""
    modes = modes or ["fused", "ehr_only", "img_only"]
    meta = _load_metadata()
    if meta is None:
        raise ModelUnavailableError("未配置 ONCOFORMER_DATA_DIR 或队列数据缺失")
    rows = meta.index[meta["demo_patient_id"] == pid]
    if len(rows) == 0:
        raise KeyError(f"队列中不存在患者 {pid}")
    row_df = meta.loc[[rows[0]]]

    provider = get_cancer_model()
    data_dir = str(cohort_data_dir())
    per_mode: dict[str, Any] = {}
    for mode in modes:
        result = provider.predict_df(row_df, mode=mode, image_dir=data_dir)
        per_mode[mode] = {
            "scores": result["scores"],
            "pred_age": result["pred_age"],
            "n_visits": result["n_visits"],
        }
    return {
        "pid": pid,
        "engine": "oncoformer",
        "modes": per_mode,
        "meta": _row_meta(row_df.iloc[0]),
    }


def _row_meta(row: Any) -> dict[str, Any]:
    import numpy as np

    def _any_cancer(col: str, idx: int) -> bool:
        arr = np.asarray(row[col])
        if arr.ndim < 2 or arr.shape[0] <= idx:
            return False
        v = arr[idx]
        return bool((v[v != -1] == 1).any())

    stage = str(row.get("cancer_stage", "NA"))
    return {
        "cancers_present": [c for i, c in enumerate(CANCER_COLS)
                            if _any_cancer("c_cls_labels", i)],
        "cancer_stage": stage if stage and stage != "nan" else "NA",
        "has_image": bool(str(row.get("xray_path", "")).strip()),
    }


__all__ = [
    "load_cohort_json",
    "cohort_stats",
    "list_patients",
    "get_precomputed",
    "realtime_predict",
    "cancer_names_zh",
]
