"""用户画像 → Oncoformer 输入就诊序列合成器。

平台 mock 用户没有真实检验明细，本模块按 COMPASS 队列的特征分布
（feat_info.json 的 min/max/mean/std）合成 8-16 次就诊的血常规/生化/体征
序列，使真模型可以在演示中推理。产出为上游 17 列 parquet schema 的单行
DataFrame，交给 ChunkedOncoDataset 复用其分箱/切片/CXR 装载逻辑。

诚实性约定：产出必须以"模拟就诊序列"标注（与平台合成医院数据的演示
定位一致），不得作为真实临床数据宣称。
"""

from __future__ import annotations

import zlib
from typing import Any

import numpy as np
import pandas as pd

from .model_provider import CANCER_COLS

_LIB = __import__("pathlib").Path(__file__).resolve().parent / "oncoformer_lib"

# COMPASS GENDER 取值 0-3，上游未公布码表；演示约定 0=男 1=女
_GENDER_TOKEN = {"男": 0, "female": 1, "女": 1, "male": 0}
# 真实数据存在的特征缺失（缺失率与平台"特征缺失率 5%-22%"的口径一致）
_MISSING_RATE = (0.05, 0.18)
_MAX_VISITS = 16  # 与 seq_max_len 对齐，超过会被运行时切片截掉


def _feat_info() -> dict:
    import json

    return json.loads((_LIB / "feat_info.json").read_text(encoding="utf-8"))


def _seed_for(user_id: str) -> np.random.Generator:
    return np.random.default_rng(zlib.crc32(user_id.encode("utf-8")) & 0xFFFFFFFF)


def synthesize_visits(profile: dict[str, Any], user_id: str = "anon") -> pd.DataFrame:
    """按画像合成单患者就诊序列，返回 1 行 17 列 schema 的 DataFrame。"""
    info = _feat_info()
    rng = _seed_for(user_id)

    age = float(profile.get("age") or 55)
    gender = str(profile.get("gender") or "男")
    chronic = profile.get("chronic_diseases") or []
    n_chronic = len(chronic) if isinstance(chronic, list) else 0
    requested = profile.get("visit_count_6m") or 10
    n_visits = int(min(max(requested, 6), _MAX_VISITS))

    # 就诊时间轴：首诊 0 起，按 30-120 天间隔递增（不超过上游 3650 天上限）
    gaps = rng.integers(30, 120, size=n_visits - 1) if n_visits > 1 else np.array([], dtype=int)
    time_index = np.concatenate([[0], np.cumsum(gaps)]).astype(np.int64)
    time_index = np.clip(time_index, 0, 3650)

    # 特征基线：年龄/慢病带来温和偏移，逐 visit 加噪声
    age_shift = (age - 60.0) / 15.0
    chronic_shift = 0.35 * min(n_chronic, 4)
    names = list(info["float_cols"].keys())
    raw = np.zeros((len(names), n_visits), dtype=np.float64)
    tokens = np.zeros((len(names), n_visits), dtype=np.int64)
    for i, name in enumerate(names):
        f = info["float_cols"][name]
        base = f["mean"] + age_shift * 0.12 * f["std"] + chronic_shift * f["std"] * rng.random()
        vals = rng.normal(base, 0.45 * f["std"] + 1e-6, size=n_visits)
        # 生命体征类特征基本不缺失；检验类按 visit 递增强性缺失
        if name.startswith("sign_"):
            miss_p = 0.02
        else:
            miss_p = rng.uniform(*_MISSING_RATE)
        miss = rng.random(n_visits) < miss_p
        vals = np.clip(vals, f["min"], f["max"])
        tok = np.floor((vals - f["min"]) / (f["max"] - f["min"] + 1e-9) * 256).astype(np.int64)
        tok = np.clip(tok, 0, 255)
        tok[miss] = -1
        raw[i], tokens[i] = vals, tok
        raw[i][miss] = np.nan

    gender_token = _GENDER_TOKEN.get(gender, 0)
    cat_tok = np.full((1, n_visits), gender_token, dtype=np.int64)
    cat_raw = cat_tok.copy()

    age_norm = (age - 60.0) / 15.0
    row = {
        "demo_patient_id": f"synthetic_{user_id}",
        "demo_visit_id": [f"V{i + 1}" for i in range(n_visits)],
        "tokenized_category_feats": cat_tok,
        "tokenized_float_feats": tokens,
        "category_feats": cat_raw,
        "float_feats": raw,
        "valid_mask": np.ones(n_visits, dtype=bool),
        "time_index": time_index,
        "cohort_id": 0,
        "xray_path": "",
        "c_cls_labels": np.zeros((len(CANCER_COLS), n_visits), dtype=np.int64),
        "f_cls_labels": np.zeros((len(CANCER_COLS), n_visits), dtype=np.int64),
        "c_reg_labels": np.full((1, n_visits), age_norm, dtype=np.float64),
        "diag_cols": list(CANCER_COLS),
        "reg_cols": ["age"],
        "dataset_fold10": 0,
        "cancer_stage": "NA",
    }
    return pd.DataFrame([row])


__all__ = ["synthesize_visits"]
