"""Synthetic heterogeneous EHR generator: 3 simulated hospitals.

Task: heart-failure 30-day readmission prediction.
Hospitals draw biased slices of a shared patient population with
site-specific measurement noise and missingness, so single-site models
are structurally weaker than a federated one. All data is synthetic —
zero privacy/compliance risk for demos.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

FEATURES = [
    "age", "sex_male", "bmi", "systolic_bp", "hba1c", "creatinine",
    "ejection_fraction", "sodium", "hemoglobin", "prior_admissions",
    "comorbidity_count", "polypharmacy", "followup_planned",
]
TARGET = "readmit_30d"

HOSPITALS = {
    # 规模 / 患者结构 / 检验能力 / 随访水平各不相同 —— 真实基层医疗格局的缩影
    "A_三甲医院": dict(n=4200, age_mean=71, age_std=11, care_effect=-0.30,
                    lab_noise=0.02, missing_ef=0.05, missing_hba1c=0.05,
                    followup_rate=0.86),
    "B_县人民医院": dict(n=2400, age_mean=68, age_std=13, care_effect=-0.05,
                     lab_noise=0.06, missing_ef=0.12, missing_hba1c=0.10,
                     followup_rate=0.68),
    "C_社区卫生中心": dict(n=1100, age_mean=64, age_std=15, care_effect=+0.08,
                       lab_noise=0.10, missing_ef=0.22, missing_hba1c=0.20,
                       followup_rate=0.55),
}


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-z))


def _true_logit(df: pd.DataFrame, care_effect: float) -> np.ndarray:
    """Ground-truth risk model shared by the whole population."""
    creatinine_excess = np.clip(df["creatinine"] - 1.0, 0, None)
    bmi_excess = np.clip(df["bmi"] - 27.5, 0, None)
    z = (
        -2.65  # 基准校准：全人群 30 天再入院率约 20%，贴近真实心衰流行病学
        + 0.042 * df["age"]
        + 0.27 * df["prior_admissions"]
        + 0.21 * df["comorbidity_count"]
        - 0.047 * df["ejection_fraction"]
        + 0.50 * (df["hba1c"] > 8.0).astype(float)
        + 0.45 * creatinine_excess
        + 0.05 * bmi_excess
        - 0.32 * df["followup_planned"]
        + 0.04 * df["polypharmacy"]
        - 0.25 * df["sex_male"]
        + care_effect
    )
    return z


def generate_hospital(name: str, rng: np.random.Generator) -> pd.DataFrame:
    cfg = HOSPITALS[name]
    n = cfg["n"]
    age = np.clip(rng.normal(cfg["age_mean"], cfg["age_std"], n), 30, 100)
    df = pd.DataFrame({
        "age": age,
        "sex_male": rng.random(n) < 0.52,
        "bmi": np.clip(rng.normal(25.5, 3.8, n), 15, 45),
        "systolic_bp": np.clip(rng.normal(138, 20, n), 80, 230),
        "hba1c": np.clip(rng.gamma(6.0, 1.05, n) + 0.9, 3.5, 16),
        "creatinine": np.clip(rng.lognormal(0.0, 0.32, n) * 0.95 + 0.15, 0.3, 8),
        "ejection_fraction": np.clip(rng.normal(48, 11, n), 10, 75),
        "sodium": np.clip(rng.normal(139, 3.4, n), 120, 152),
        "hemoglobin": np.clip(rng.normal(12.4, 1.9, n), 5, 19),
        "prior_admissions": rng.poisson(0.9, n).astype(float),
        "comorbidity_count": np.clip(rng.poisson(2.3, n), 0, 10).astype(float),
        "polypharmacy": np.clip(rng.poisson(3.2, n), 0, 15).astype(float),
        "followup_planned": (rng.random(n) < cfg["followup_rate"]).astype(float),
    })

    # 测量层：小医院仪器/流程差异 → 特征含噪 + 缺失（缺失用本院中位数填充）
    noise = 1.0 + rng.normal(0, cfg["lab_noise"], (n, 1))
    for col in ("hba1c", "creatinine", "ejection_fraction"):
        df[col] = df[col] * noise.ravel() * (1 + rng.normal(0, cfg["lab_noise"], n))
    for col, p in (("ejection_fraction", cfg["missing_ef"]),
                   ("hba1c", cfg["missing_hba1c"])):
        mask = rng.random(n) < p
        df.loc[mask, col] = np.nan
        df[col] = df[col].fillna(df[col].median())

    p = _sigmoid(_true_logit(df, cfg["care_effect"]))
    df[TARGET] = (rng.random(n) < p).astype(int)
    df["site"] = name
    return df


def generate_all(seed: int = 2026) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    return {name: generate_hospital(name, rng) for name in HOSPITALS}


if __name__ == "__main__":
    data = generate_all()
    for name, df in data.items():
        print(f"{name}: n={len(df)}, 阳性率={df[TARGET].mean():.1%}, "
              f"平均年龄={df['age'].mean():.1f}")
