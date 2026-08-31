"""用真模型离线预计算 COMPASS 队列代表患者的三模态风险。

产物 data/cancer_cohort.json 随部署包上云，使云端（无 torch）也能
演示真实模型输出；本地装了 torch 的环境仍可实时推理。

用法（backend 目录，需 requirements-cancer.txt 环境）:
    python scripts/cancer_precompute.py \
        --data-dir D:/APPs/温州AI医疗比赛/wenzhou-ai-med-archive/oncoformer/sample_data/compass \
        --ckpt     D:/APPs/温州AI医疗比赛/wenzhou-ai-med-archive/oncoformer/checkpoints/demo_finetune.ckpt \
        --n 24
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

BACKEND_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))  # noqa: E402

CANCER_COLS = [
    "Lung cancer", "Colorectal cancer", "Gastric cancer", "Liver cancer",
    "Breast cancer", "Ovarian/Cervical cancer", "Prostate cancer",
]
MODES = ["fused", "ehr_only", "img_only"]


def pick_patients(meta, n: int) -> list[int]:
    """每个癌种选 2 例阳性（尽量含不同分期）+ 6 例阴性对照，全部需有胸片。"""
    import numpy as np

    def has_image(i: int) -> bool:
        return bool(str(meta.iloc[i]["xray_path"] or "").strip())

    def cancer_pos(i: int, k: int) -> bool:
        arr = np.asarray(meta.iloc[i]["c_cls_labels"])
        return arr.ndim == 2 and arr.shape[0] > k and (arr[k][arr[k] != -1] == 1).any()

    def any_cancer(i: int) -> bool:
        return any(cancer_pos(i, k) for k in range(len(CANCER_COLS)))

    picked: list[int] = []
    for k in range(len(CANCER_COLS)):
        pos = [i for i in range(len(meta)) if has_image(i) and cancer_pos(i, k)]
        early = [i for i in pos if str(meta.iloc[i]["cancer_stage"]) in ("I", "II", "IIA", "IIB")]
        late = [i for i in pos if i not in early]
        chosen = (early[:1] + late[:1])[:2]
        for i in chosen:
            if i not in picked:
                picked.append(i)
    controls = [i for i in range(len(meta))
                if has_image(i) and not any_cancer(i) and i not in picked]
    picked.extend(controls[: max(6, n - len(picked))])
    return picked[:n]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True, help="COMPASS sample_data/compass 目录")
    parser.add_argument("--ckpt", required=True, help="demo_finetune.ckpt 路径")
    parser.add_argument("--out", default=str(REPO_ROOT / "data" / "cancer_cohort.json"))
    parser.add_argument("--n", type=int, default=24)
    args = parser.parse_args()

    import os

    os.environ.setdefault("ONCOFORMER_CKPT_PATH", args.ckpt)

    from app.services.cancer.model_provider import get_cancer_model
    from app.services.cancer.oncoformer_lib.Utils import load_parquet

    meta = load_parquet(str(Path(args.data_dir) / "metadata.parquet"))
    idxs = pick_patients(meta, args.n)
    print(f"[precompute] 队列 {len(meta)} 例，选中 {len(idxs)} 例")

    provider = get_cancer_model()
    patients = []
    t0 = time.time()
    for j, i in enumerate(idxs):
        row = meta.iloc[i]
        row_df = meta.loc[[i]]
        per_mode = {}
        for mode in MODES:
            r = provider.predict_df(row_df, mode=mode, image_dir=args.data_dir)
            per_mode[mode] = {
                "scores": r["scores"],
                "pred_age": r["pred_age"],
                "n_visits": r["n_visits"],
            }
        meta_info = _row_meta(row)
        patients.append({
            "pid": str(row["demo_patient_id"]),
            "meta": meta_info,
            "modes": per_mode,
        })
        print(f"[precompute] {j + 1}/{len(idxs)} {row['demo_patient_id']} "
              f"stage={meta_info['cancer_stage']} elapsed={time.time() - t0:.0f}s")

    # 队列总体统计（供云端降级叙事用）
    prevalence = {}
    for k, name in enumerate(CANCER_COLS):
        cnt = 0
        for i in range(len(meta)):
            arr = np.asarray(meta.iloc[i]["c_cls_labels"])
            if arr.ndim == 2 and arr.shape[0] > k and (arr[k][arr[k] != -1] == 1).any():
                cnt += 1
        prevalence[name] = cnt
    payload = {
        "_comment": "COMPASS 示例队列预计算结果（真模型离线推理）。"
                    "上游数据/权重: kaiwang13/Oncoformer (Apache-2.0)，仅研究演示用途。",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "ckpt": Path(args.ckpt).name,
        "population": {
            "total": int(len(meta)),
            "prevalence": prevalence,
        },
        "patients": patients,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[precompute] 完成 → {out} ({out.stat().st_size / 1024:.0f} KB, "
          f"总耗时 {time.time() - t0:.0f}s)")


def _row_meta(row) -> dict:
    def _any_cancer(idx: int) -> bool:
        arr = np.asarray(row["c_cls_labels"])
        return arr.ndim == 2 and arr.shape[0] > idx and (arr[idx][arr[idx] != -1] == 1).any()

    stage = str(row.get("cancer_stage", "NA"))
    return {
        "cancers_present": [c for i, c in enumerate(CANCER_COLS) if _any_cancer(i)],
        "cancer_stage": stage if stage and stage != "nan" else "NA",
        "has_image": bool(str(row.get("xray_path", "") or "").strip()),
    }


if __name__ == "__main__":
    main()
