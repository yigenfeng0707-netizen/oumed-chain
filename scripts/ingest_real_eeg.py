"""真实公开 EEG 数据集接入（PhysioNet eegmmidb）— 可重复执行

流程：
1. 从 PhysioNet 下载若干 eegmmidb EDF 记录到 data/real_eeg/raw/（已存在则跳过）
2. 纯 Python 解析 EDF（BCI2000 列式信号头自动检测，无需 pyedflib）
3. 复用 eeg_engine.assess_real_session 走完整评估管线（频域→五维指标→预警→政策联动）
4. 产出 data/real_eeg/manifest.json（随镜像部署；原始 EDF 不入库，见 .gitignore）

数据集：PhysioNet EEG Motor Movement/Imagery (eegmmidb v1.0.0)，ODC-By 1.0 许可。
用法：python scripts/ingest_real_eeg.py
"""

import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

RAW_DIR = ROOT / "data" / "real_eeg" / "raw"
MANIFEST = ROOT / "data" / "real_eeg" / "manifest.json"

BASE_URL = "https://physionet.org/files/eegmmidb/1.0.0"
LICENSE = "ODC-By 1.0"
DATASET_URL = "https://physionet.org/content/eegmmidb/1.0.0/"

# 采样记录：覆盖 T0 基线 / T1 运动执行 / T2 运动想象三类范式、多个受试者
TARGETS = [
    ("S001", "R01"), ("S001", "R02"), ("S002", "R03"), ("S003", "R04"),
    ("S004", "R07"), ("S005", "R09"), ("S006", "R11"), ("S007", "R12"),
]

PARADIGM = {
    "R01": "T0 基线·睁眼", "R02": "T0 基线·闭眼",
    "R03": "T1 运动执行·握拳", "R04": "T2 运动想象·握拳",
    "R07": "T1 运动执行·握拳", "R09": "T1 运动执行·左右握拳",
    "R11": "T1 运动执行·握拳", "R12": "T2 运动想象·握拳",
}


# ============================================================
# 纯 Python EDF 解析（支持标准行式 / BCI2000 列式信号头）
# ============================================================

def _f(b: bytes) -> float:
    s = b.decode("ascii", errors="ignore").strip()
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def parse_edf_pure(path: Path) -> tuple[list[np.ndarray], list[str], int, dict]:
    """解析 EDF/EDF+，返回 (signals, labels, sample_rate, meta)。

    eegmmidb（BCI2000 导出）信号头为列式布局，标准 EDF 为行式——自动检测。
    """
    data = path.read_bytes()
    n_records = int(_f(data[236:244]))
    rec_duration = _f(data[244:252]) or 1.0
    ns = int(_f(data[252:256]))

    base = 256
    # 行式判定：每通道偏移 216 处的 samples_per_record > 0 全部成立
    row_ok = all(
        _f(data[base + i * 256 + 216: base + i * 256 + 224]) > 0 for i in range(ns)
    )

    if row_ok:
        labels, phys_min, phys_max, dig_min, dig_max, sprs = [], [], [], [], [], []
        for i in range(ns):
            off = base + i * 256
            labels.append(data[off: off + 16].decode("ascii", errors="ignore").strip())
            phys_min.append(_f(data[off + 104: off + 112]))
            phys_max.append(_f(data[off + 112: off + 120]))
            dig_min.append(_f(data[off + 120: off + 128]))
            dig_max.append(_f(data[off + 128: off + 136]))
            sprs.append(int(_f(data[off + 216: off + 224])))
        data_start = base + ns * 256
    else:
        # 列式（BCI2000）：同名字段跨所有通道连续排列
        label_off = base
        trans_off = label_off + ns * 16
        pdim_off = trans_off + ns * 80
        pmin_off = pdim_off + ns * 8
        pmax_off = pmin_off + ns * 8
        dmin_off = pmax_off + ns * 8
        dmax_off = dmin_off + ns * 8
        pre_off = dmax_off + ns * 8 + ns * 8
        spr_off = pre_off + ns * 80
        labels = [
            data[label_off + i * 16: label_off + (i + 1) * 16].decode("ascii", "ignore").strip()
            for i in range(ns)
        ]
        phys_min = [_f(data[pmin_off + i * 8: pmin_off + (i + 1) * 8]) for i in range(ns)]
        phys_max = [_f(data[pmax_off + i * 8: pmax_off + (i + 1) * 8]) for i in range(ns)]
        dig_min = [_f(data[dmin_off + i * 8: dmin_off + (i + 1) * 8]) for i in range(ns)]
        dig_max = [_f(data[dmax_off + i * 8: dmax_off + (i + 1) * 8]) for i in range(ns)]
        sprs = [int(_f(data[spr_off + i * 8: spr_off + (i + 1) * 8])) for i in range(ns)]
        data_start = spr_off + ns * 8 + ns * 32

    spr = sprs[0] if sprs and sprs[0] > 0 else 160
    sample_rate = int(round(spr / rec_duration))

    # 数据区：每记录 ns 通道 × spr 点 × 2 字节
    need = n_records * ns * spr * 2
    raw = np.frombuffer(data[data_start: data_start + need], dtype="<i2")
    if len(raw) < need:
        n_records = len(raw) // (ns * spr)  # 截断容错
        raw = raw[: n_records * ns * spr]

    signals = []
    for i in range(ns):
        # 通道交织存储：第 i 通道在各记录内偏移 i*spr
        idx = np.arange(n_records) * (ns * spr) + i * spr
        blocks = [raw[k: k + spr] for k in idx]
        ch = np.concatenate(blocks).astype(np.float64)
        # 数字→物理值换算
        lo, hi = phys_min[i], phys_max[i]
        dlo, dhi = dig_min[i], dig_max[i]
        if dhi > dlo:
            ch = lo + (ch - dlo) * (hi - lo) / (dhi - dlo)
        signals.append(ch)

    meta = {
        "n_records": n_records,
        "record_duration": rec_duration,
        "header_layout": "row" if row_ok else "column(BCI2000)",
        "amplitude_uv_range": [float(min(s.min() for s in signals)),
                                float(max(s.max() for s in signals))],
    }
    return signals, labels, sample_rate, meta


# ============================================================
# 下载（已存在则跳过）
# ============================================================

def download(subj: str, run: str, retries: int = 4) -> Path:
    import time

    import httpx

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    dest = RAW_DIR / f"{subj}{run}.edf"
    if dest.exists() and dest.stat().st_size > 100_000:
        return dest

    url = f"{BASE_URL}/{subj}/{subj}{run}.edf"
    tmp = dest.with_suffix(".edf.part")
    for attempt in range(1, retries + 1):
        try:
            print(f"  下载 {url}（第 {attempt} 次）")
            with httpx.Client(timeout=httpx.Timeout(connect=30, read=180, write=60, pool=60),
                              follow_redirects=True) as c:
                with c.stream("GET", url) as r:
                    r.raise_for_status()
                    with open(tmp, "wb") as f:
                        for chunk in r.iter_bytes(1 << 16):
                            f.write(chunk)
            os.replace(tmp, dest)
            return dest
        except Exception as e:
            tmp.unlink(missing_ok=True)
            if attempt == retries:
                raise
            wait = 8 * attempt
            print(f"  失败（{type(e).__name__}），{wait}s 后重试…")
            time.sleep(wait)
    return dest


# ============================================================
# 主流程
# ============================================================

def main():
    from app.services.eeg import engine as eeg_engine
    from app.services.eeg.device_adapter import from_numpy

    sessions = []
    for subj, run in TARGETS:
        record_id = f"eegmmidb_{subj}{run}"
        print(f"[{record_id}]")
        path = download(subj, run)
        signals, labels, sample_rate, meta = parse_edf_pure(path)
        duration = round(len(signals[0]) / sample_rate, 1)
        print(f"  解析: {len(signals)}通道 x {sample_rate}Hz x {duration}s"
              f"（头布局 {meta['header_layout']}）")

        # 复用设备适配器（映射 Muse 4 通道 + 质量评估）→ 完整评估管线
        mapped_signals, channels, sr, device_info = from_numpy(signals, labels, sample_rate)
        session = eeg_engine.assess_real_session(
            user_id=record_id,
            signals=mapped_signals,
            channels=channels,
            sample_rate=sr,
            mental_state="auto",
            user_profile=None,
            device_info=device_info.to_dict(),
        )
        d = session.to_dict()
        d.update({
            "record_id": record_id,
            "source": "eegmmidb",
            "origin_sample_rate": sample_rate,
            "origin_channels": labels,
            "origin_file": path.name,
            "dataset_meta": {
                "subject": subj,
                "run": run,
                "paradigm": PARADIGM.get(run, run),
                "license": LICENSE,
                "url": DATASET_URL,
            },
        })
        sessions.append(d)
        print(f"  评估: {d['mental_state']}（{d['mental_state_label']}），"
              f"{len(d['alerts'])} 项预警，{len(d['policy_links'])} 条政策联动")

    manifest = {
        "generated_at": datetime.now(UTC).isoformat(),
        "datasets": {
            "eegmmidb": {
                "label": "PhysioNet EEG Motor Movement/Imagery",
                "count": len(sessions),
                "license": LICENSE,
                "url": DATASET_URL,
                "citation": "Schalk, G. et al. BCI2000 (eegmmidb). PhysioNet.",
            }
        },
        "sessions": sessions,
        "note": "真实公开数据集 EEG（PhysioNet eegmmidb，ODC-By 许可），指标仅供科研演示，不构成医疗诊断。",
    }

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    # 原子写：临时文件 + os.replace，避免中断产生半截 JSON
    fd, tmp = tempfile.mkstemp(dir=str(MANIFEST.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=1)
        os.replace(tmp, MANIFEST)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

    print(f"\n完成：{len(sessions)} 条评估 -> {MANIFEST}")


if __name__ == "__main__":
    main()
