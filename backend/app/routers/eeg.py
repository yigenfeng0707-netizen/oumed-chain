"""
瓯医数链 - 脑电健康路由（EEG Router）

BCI×医保创新模块的 API 入口
- POST /api/eeg/{user_id}/session：发起一次 EEG 采集会话（合成信号 + 完整评估）
- GET  /api/eeg/{user_id}/latest：获取最近一次 EEG 评估
- GET  /api/eeg/{user_id}/history：EEG 历史趋势
- GET  /api/eeg/{user_id}/realtime：实时数据块（前端轮询模拟实时采集）
- GET  /api/eeg/{user_id}/policy-links：脑电异常 → 医保政策联动推荐
- GET  /api/eeg/states：支持的心理状态列表（前端场景选择用）
- GET  /api/eeg/real/list：真实公开数据集 EEG 评估列表（eegmmidb 等）
- GET  /api/eeg/real/{record_id}：单条真实 EEG 评估详情
"""

import json
import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from app.database import get_db
from app.services.eeg import engine as eeg_engine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/eeg", tags=["脑电健康"])

# 真实公开数据集 manifest（scripts/ingest_real_eeg.py 产出）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_REAL_MANIFEST = _PROJECT_ROOT / "data" / "real_eeg" / "manifest.json"


def _load_real_manifest() -> dict:
    """读取真实 EEG 数据集 manifest（不存在时返回空结构，不报错）。"""
    try:
        return json.loads(_REAL_MANIFEST.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": "1.0", "datasets": {}, "sessions": []}
    except Exception as e:
        logger.warning("读取真实 EEG manifest 失败: %s", e)
        return {"version": "1.0", "datasets": {}, "sessions": []}


@router.get("/states")
async def list_mental_states():
    """支持的心理状态列表（前端场景选择用）"""
    states = []
    for key, meta in eeg_engine.MENTAL_STATES.items():
        states.append({
            "key": key,
            "label": meta["label"],
            "stress": meta["stress"],
            "attention": meta["attention"],
            "sleep": meta["sleep"],
            "cognitive": meta["cognitive"],
        })
    return {"states": states, "channels": eeg_engine.CHANNELS, "sample_rate": eeg_engine.SAMPLE_RATE}


@router.get("/real/list")
async def list_real_eeg_sessions(
    source: str = Query(None, description="按数据源过滤（demo/eegmmidb/local）"),
    limit: int = Query(20, ge=1, le=100),
):
    """真实公开数据集 EEG 评估列表（PhysioNet eegmmidb 等）。

    数据由 scripts/ingest_real_eeg.py 接入，manifest 位于
    data/real_eeg/manifest.json。返回概览（含五维健康指标）。
    """
    m = _load_real_manifest()
    sessions = m.get("sessions", [])
    if source:
        sessions = [s for s in sessions if s.get("source") == source]
    sessions = sessions[:limit]
    return {
        "total": len(m.get("sessions", [])),
        "returned": len(sessions),
        "datasets": m.get("datasets", {}),
        "sessions": [
            {
                "record_id": s.get("record_id"),
                "source": s.get("source"),
                "mental_state": s.get("mental_state"),
                "mental_state_label": s.get("mental_state_label"),
                "channels": s.get("channels"),
                "origin_sample_rate": s.get("origin_sample_rate"),
                "duration_seconds": s.get("duration_seconds"),
                "metrics": s.get("metrics"),
                "alerts_count": len(s.get("alerts") or []),
                "dataset_meta": s.get("dataset_meta"),
                "origin_file": s.get("origin_file"),
            }
            for s in sessions
        ],
        "note": "真实公开数据集 EEG（PhysioNet eegmmidb，ODC-By 许可），指标仅供科研演示，不构成医疗诊断。",
    }


@router.get("/real/{record_id}")
async def get_real_eeg_session(record_id: str):
    """单条真实 EEG 评估详情：五维指标 + 频段功率 + 预警 + 医保政策联动。"""
    m = _load_real_manifest()
    target = None
    for s in m.get("sessions", []):
        if s.get("record_id") == record_id:
            target = s
            break
    if target is None:
        raise HTTPException(status_code=404, detail=f"真实 EEG 记录不存在: {record_id}")
    return target


@router.post("/{user_id}/session")
async def create_eeg_session(
    user_id: str,
    mental_state: str = Query("auto", description="心理状态：auto/relaxed/focused/stressed/fatigued/sleep_deprived"),
    duration_seconds: int = Query(4, ge=1, le=30, description="采集时长（秒）"),
    db: AsyncSession = Depends(get_db),
):
    """发起一次 EEG 采集会话

    流程：合成信号 → 频域特征提取 → 健康指标 → 异常预警 → 医保政策联动 → 入库
    """
    profile = await crud.get_user_health_profile(db, user_id)
    if not profile.get("found"):
        raise HTTPException(status_code=404, detail=f"用户 {user_id} 不存在")

    # auto 模式：根据用户画像推荐心理状态
    if mental_state == "auto":
        mental_state = eeg_engine.pick_mental_state_by_profile(profile)

    if mental_state not in eeg_engine.MENTAL_STATES:
        raise HTTPException(status_code=400, detail=f"不支持的心理状态：{mental_state}")

    # 完整评估
    session = eeg_engine.assess_session(
        user_id=user_id,
        mental_state=mental_state,
        duration_seconds=duration_seconds,
        user_profile=profile,
    )

    # 入库（摘要）
    try:
        await crud.create_eeg_record(
            db=db,
            user_id=user_id,
            session_id=session.session_id,
            duration_seconds=session.duration_seconds,
            mental_state=session.mental_state,
            mental_state_label=session.mental_state_label,
            avg_band_powers=session.avg_band_powers,
            metrics=session.metrics,
            alert_count=len(session.alerts),
            policy_link_count=len(session.policy_links),
            summary=session.summary,
        )
    except Exception as e:
        logger.warning("EEG 记录入库失败（不影响返回）: %s", e)

    return session.to_dict()


@router.get("/{user_id}/latest")
async def get_latest_eeg(user_id: str, db: AsyncSession = Depends(get_db)):
    """获取用户最近一次 EEG 评估（从数据库读取历史摘要，再实时生成波形）"""
    profile = await crud.get_user_health_profile(db, user_id)
    if not profile.get("found"):
        raise HTTPException(status_code=404, detail=f"用户 {user_id} 不存在")

    record = await crud.get_latest_eeg_record(db, user_id)
    if record is None:
        # 无历史记录，实时生成一次
        mental_state = eeg_engine.pick_mental_state_by_profile(profile)
        session = eeg_engine.assess_session(
            user_id=user_id, mental_state=mental_state, user_profile=profile, seed=42,
        )
        return {**session.to_dict(), "from_history": False}

    # 历史摘要 + 实时波形（基于历史心理状态重新生成波形，保证可视化）
    signals, channels, sr = eeg_engine.generate_synthetic_eeg(
        mental_state=record.mental_state, seed=42,
    )
    waveform = eeg_engine._downsample_waveform(signals, channels, target_points=128)
    return {
        **crud.eeg_record_to_dict(record),
        "channels": channels,
        "sample_rate": sr,
        "waveform": waveform,
        "from_history": True,
    }


@router.get("/{user_id}/history")
async def get_eeg_history(
    user_id: str,
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """获取用户 EEG 历史趋势"""
    profile = await crud.get_user_health_profile(db, user_id)
    if not profile.get("found"):
        raise HTTPException(status_code=404, detail=f"用户 {user_id} 不存在")

    records = await crud.get_eeg_records(db, user_id, limit=limit)
    history = [crud.eeg_record_to_dict(r) for r in records]

    # 趋势聚合：压力/注意力/睡眠/认知负荷 4 维时序
    trend = []
    for r in reversed(records):  # 时间正序
        metrics = __import__("json").loads(r.metrics) if r.metrics else {}
        trend.append({
            "timestamp": r.recorded_at.isoformat() if r.recorded_at else None,
            "mental_state": r.mental_state,
            "mental_state_label": r.mental_state_label,
            "stress_index": metrics.get("stress_index", 0),
            "attention_index": metrics.get("attention_index", 0),
            "sleep_quality": metrics.get("sleep_quality", 0),
            "cognitive_load": metrics.get("cognitive_load", 0),
        })

    return {
        "user_id": user_id,
        "user_name": profile.get("name"),
        "total_sessions": len(history),
        "history": history,
        "trend": trend,
    }


@router.get("/{user_id}/realtime")
async def get_realtime_chunk(
    user_id: str,
    mental_state: str = Query("relaxed"),
    seed: int = Query(0, ge=0, le=100000),
):
    """实时数据块（前端轮询模拟实时采集，每次返回 1 秒数据）"""
    if mental_state not in eeg_engine.MENTAL_STATES:
        mental_state = "relaxed"
    return eeg_engine.realtime_stream(mental_state=mental_state, chunk_seconds=1.0, seed=seed or None)


@router.get("/{user_id}/policy-links")
async def get_policy_links(user_id: str, db: AsyncSession = Depends(get_db)):
    """脑电异常 → 医保政策联动推荐（基于最近一次 EEG 评估）"""
    profile = await crud.get_user_health_profile(db, user_id)
    if not profile.get("found"):
        raise HTTPException(status_code=404, detail=f"用户 {user_id} 不存在")

    record = await crud.get_latest_eeg_record(db, user_id)
    if record is None:
        # 无历史，实时评估一次
        mental_state = eeg_engine.pick_mental_state_by_profile(profile)
        session = eeg_engine.assess_session(
            user_id=user_id, mental_state=mental_state, user_profile=profile, seed=42,
        )
        return {
            "user_id": user_id,
            "user_name": profile.get("name"),
            "mental_state": session.mental_state,
            "mental_state_label": session.mental_state_label,
            "policy_links": session.policy_links,
            "summary": session.summary,
        }

    # 基于历史指标重新计算联动
    import json
    metrics = json.loads(record.metrics) if record.metrics else {}
    links = eeg_engine.link_to_policies(metrics, profile)
    return {
        "user_id": user_id,
        "user_name": profile.get("name"),
        "mental_state": record.mental_state,
        "mental_state_label": record.mental_state_label,
        "policy_links": links,
        "summary": record.summary or "",
    }


# ============================================================
# v2.2 新增：真实设备接入 + 文件导入
# ============================================================

@router.get("/device/check")
async def check_device():
    """检查 LSL EEG 设备连接状态（不采集，仅探测）"""
    from app.services.eeg.device_adapter import check_lsl_connection
    return check_lsl_connection()


@router.post("/{user_id}/session-device")
async def create_eeg_session_from_device(
    user_id: str,
    duration_seconds: int = Query(4, ge=1, le=30, description="采集时长（秒）"),
    mental_state: str = Query("auto", description="心理状态标签（auto 则根据信号推断）"),
    db: AsyncSession = Depends(get_db),
):
    """从真实 EEG 设备采集信号并评估（通过 LSL 协议）

    前置条件：
    1. EEG 设备已通过蓝牙/USB 连接到电脑
    2. LSL 推流正在运行（如 muselsl stream）
    3. pylsl 已安装：pip install pylsl

    流程：LSL 采集 → 频域分析 → 健康指标 → 预警 → 政策联动 → 入库
    """
    from app.services.eeg.device_adapter import acquire_from_lsl, get_device_config

    profile = await crud.get_user_health_profile(db, user_id)
    if not profile.get("found"):
        raise HTTPException(status_code=404, detail=f"用户 {user_id} 不存在")

    config = get_device_config()

    try:
        signals, channels, sample_rate, device_info = acquire_from_lsl(
            stream_name=config["lsl_stream_name"],
            stream_type=config["lsl_stream_type"],
            duration_seconds=duration_seconds,
        )
    except ImportError as e:
        raise HTTPException(status_code=503, detail=f"依赖未安装: {e}") from e
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"设备连接失败: {e}") from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"采集失败: {e}") from e

    # 信号质量检查
    if device_info.signal_quality == "poor":
        logger.warning("EEG 信号质量差: %s", device_info.quality_detail)

    # 完整评估（复用引擎）
    session = eeg_engine.assess_real_session(
        user_id=user_id,
        signals=signals,
        channels=channels,
        sample_rate=sample_rate,
        mental_state=mental_state,
        user_profile=profile,
        device_info=device_info.to_dict(),
    )

    # 入库
    try:
        await crud.create_eeg_record(
            db=db,
            user_id=user_id,
            session_id=session.session_id,
            duration_seconds=session.duration_seconds,
            mental_state=session.mental_state,
            mental_state_label=session.mental_state_label,
            avg_band_powers=session.avg_band_powers,
            metrics=session.metrics,
            alert_count=len(session.alerts),
            policy_link_count=len(session.policy_links),
            summary=session.summary,
        )
    except Exception as e:
        logger.warning("EEG 记录入库失败（不影响返回）: %s", e)

    return session.to_dict()


@router.post("/{user_id}/import")
async def import_eeg_file(
    user_id: str,
    file: UploadFile = File(..., description="EEG 文件（.csv / .edf / .txt）"),
    sample_rate: int = Query(256, ge=1, le=1000, description="采样率（Hz），CSV/TXT 需指定"),
    mental_state: str = Query("auto", description="心理状态标签"),
    db: AsyncSession = Depends(get_db),
):
    """导入 EEG 文件并分析（支持 CSV / EDF / TXT）

    CSV 格式：
    - 第一行：通道名（逗号分隔），如 TP9,AF7,AF8,TP10
    - 第二行起：每行一个采样点的各通道电压值（微伏 μV）

    EDF 格式：
    - 临床标准 EDF/EDF+ 文件
    - 自动提取通道和采样率
    - 需安装 pyedflib：pip install pyedflib
    """
    from app.services.eeg.device_adapter import load_from_csv, load_from_edf

    profile = await crud.get_user_health_profile(db, user_id)
    if not profile.get("found"):
        raise HTTPException(status_code=404, detail=f"用户 {user_id} 不存在")

    # 读取文件内容
    content = await file.read()
    filename = file.filename or "uploaded_eeg"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    try:
        if ext == "csv" or ext == "txt":
            signals, channels, sr, device_info = load_from_csv(
                content, sample_rate=sample_rate, filename=filename,
            )
        elif ext == "edf":
            # EDF 需要写入临时文件（pyedflib 只支持文件路径）
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".edf", delete=False) as tmp:
                tmp.write(content)
                tmp_path = tmp.name
            try:
                signals, channels, sr, device_info = load_from_edf(tmp_path)
            finally:
                os.unlink(tmp_path)
        else:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的文件格式: .{ext}，支持 .csv / .edf / .txt",
            )
    except HTTPException:
        raise
    except ImportError as e:
        raise HTTPException(status_code=503, detail=f"依赖未安装: {e}") from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"文件解析失败: {e}") from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"导入失败: {e}") from e

    # 完整评估
    session = eeg_engine.assess_real_session(
        user_id=user_id,
        signals=signals,
        channels=channels,
        sample_rate=sr,
        mental_state=mental_state,
        user_profile=profile,
        device_info=device_info.to_dict(),
    )

    # 入库
    try:
        await crud.create_eeg_record(
            db=db,
            user_id=user_id,
            session_id=session.session_id,
            duration_seconds=session.duration_seconds,
            mental_state=session.mental_state,
            mental_state_label=session.mental_state_label,
            avg_band_powers=session.avg_band_powers,
            metrics=session.metrics,
            alert_count=len(session.alerts),
            policy_link_count=len(session.policy_links),
            summary=session.summary,
        )
    except Exception as e:
        logger.warning("EEG 记录入库失败（不影响返回）: %s", e)

    return session.to_dict()
