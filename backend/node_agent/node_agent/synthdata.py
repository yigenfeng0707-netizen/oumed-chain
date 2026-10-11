"""演示/联调用合成 EHR 生成器（独立实现，不依赖平台包）。

为双机演练提供三份异构医院 CSV（规模/人群/质量差异贴近真实基层格局）。
真实接入时医院只需替换为本院脱敏导出的同 schema CSV——Agent 代码零改动。
"""

from __future__ import annotations

import csv
import sys

import numpy as np

from node_agent.trainer import FEATURES, TARGET

# 三家演示医院：规模/阳性率/随访水平各异（与平台演示口径同构）
SITES = {
    "hospital_a": dict(n=1200, age_mean=71, age_std=11, care_effect=-0.30, followup=0.86),
    "hospital_b": dict(n=800, age_mean=68, age_std=13, care_effect=-0.05, followup=0.68),
    "hospital_c": dict(n=400, age_mean=64, age_std=15, care_effect=+0.08, followup=0.55),
}


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-z))


def generate(name: str, seed: int = 2026) -> list[dict]:
    """生成一家医院的行级合成数据（仅本地使用，永不上行）。"""
    cfg = SITES[name]
    # 确定性站点偏移（勿用 hash()：跨进程非确定，破坏可复现性）
    site_seed = seed + sum(ord(c) for c in name) % 1000
    rng = np.random.default_rng(site_seed)
    n = cfg["n"]
    age = np.clip(rng.normal(cfg["age_mean"], cfg["age_std"], n), 30, 100)
    bmi = np.clip(rng.normal(25.5, 3.8, n), 15, 45)
    ef = np.clip(rng.normal(48, 11, n), 10, 75)
    hba1c = np.clip(rng.gamma(6.0, 1.05, n) + 0.9, 3.5, 16)
    creatinine = np.clip(rng.lognormal(0.0, 0.32, n) * 0.95 + 0.15, 0.3, 8)
    followup = (rng.random(n) < cfg["followup"]).astype(float)
    prior_adm = rng.poisson(0.9, n).astype(float)
    comorb = np.clip(rng.poisson(2.3, n), 0, 10).astype(float)

    z = (-2.65 + 0.042 * age + 0.27 * prior_adm + 0.21 * comorb
         - 0.047 * ef + 0.50 * (hba1c > 8.0) + 0.45 * np.clip(creatinine - 1.0, 0, None)
         + 0.05 * np.clip(bmi - 27.5, 0, None) - 0.32 * followup + cfg["care_effect"])
    y = (rng.random(n) < _sigmoid(z)).astype(int)

    return [{
        "age": round(float(age[i]), 1),
        "sex_male": int(rng.random() < 0.52),
        "bmi": round(float(bmi[i]), 1),
        "systolic_bp": round(float(np.clip(rng.normal(138, 20), 80, 230)), 0),
        "hba1c": round(float(hba1c[i]), 2),
        "creatinine": round(float(creatinine[i]), 2),
        "ejection_fraction": round(float(ef[i]), 0),
        "sodium": round(float(np.clip(rng.normal(139, 3.4), 120, 152)), 1),
        "hemoglobin": round(float(np.clip(rng.normal(12.4, 1.9), 5, 19)), 1),
        "prior_admissions": int(prior_adm[i]),
        "comorbidity_count": int(comorb[i]),
        "polypharmacy": int(np.clip(rng.poisson(3.2), 0, 15)),
        "followup_planned": int(followup[i]),
        TARGET: int(y[i]),
    } for i in range(n)]


def write_csv(path: str, name: str, seed: int = 2026) -> int:
    rows = generate(name, seed)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[*FEATURES, TARGET])
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


if __name__ == "__main__":
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    for site in SITES:
        path = f"{out_dir}/{site}.csv"
        n = write_csv(path, site)
        print(f"已生成 {path}（{n} 行，含 {len(FEATURES)} 特征 + {TARGET}）")
