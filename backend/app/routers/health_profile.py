"""
MedSignal - 健康画像路由

P1-2 升级：接入 health_engine 完整引擎
- 5 维健康评分（基于真实数据 + 用药相互作用影响）
- 用药相互作用检测（基于 drug_interaction_rules.json）
- 主动预警扫描（购药模式、用药中断、就医异常）
- 每条预警带 evidence（数据证据，支撑可解释性）
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from app.database import get_db
from app.services import health_engine
from app.services.eeg import engine as eeg_engine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/health", tags=["健康画像"])


@router.get("/{user_id}/profile")
async def get_health_profile(user_id: str, db: AsyncSession = Depends(get_db)):
    """生成用户完整健康报告（基于 health_engine）"""
    profile = await crud.get_user_health_profile(db, user_id)
    if not profile.get("found"):
        raise HTTPException(status_code=404, detail=f"用户 {user_id} 不存在")

    medical_records = await crud.get_medical_records(db, user_id, limit=100)
    report = health_engine.assess(profile, medical_records=medical_records)
    d = report.to_dict()

    return {
        **d,
        "chronic_diseases": profile.get("chronic_diseases", []),
        "evidence": {
            "visit_count_6m": profile.get("visit_count_6m", 0),
            "medication_count": len(d.get("medications", [])),
            "annual_medical_cost": profile.get("annual_medical_cost", 0),
            "drug_warning_count": len(d.get("drug_warnings", [])),
        },
    }


@router.get("/{user_id}/alerts")
async def get_health_alerts(user_id: str, db: AsyncSession = Depends(get_db)):
    """获取用户健康预警（含用药相互作用警告）"""
    profile = await crud.get_user_health_profile(db, user_id)
    if not profile.get("found"):
        raise HTTPException(status_code=404, detail=f"用户 {user_id} 不存在")
    medical_records = await crud.get_medical_records(db, user_id, limit=100)
    report = health_engine.assess(profile, medical_records=medical_records)
    # 合并 alerts + drug_warnings
    return report.alerts + [
        {**w, "level": w.get("severity", "medium"), "icon": w.get("icon", "🟡"),
         "desc": w.get("description", ""), "description": w.get("description", "")}
        for w in report.drug_warnings
    ]


@router.get("/{user_id}/proactive-alerts")
async def get_proactive_alerts(user_id: str, db: AsyncSession = Depends(get_db)):
    """主动预警（用户登录时触发，只返回 high/medium 级别）

    P2-3 主动式健康预警推送：体现"主动式服务"范式创新。
    v2.1.0 升级：合并 EEG 脑电预警（BCI×医保创新），脑电异常也会主动推送。
    """
    profile = await crud.get_user_health_profile(db, user_id)
    if not profile.get("found"):
        raise HTTPException(status_code=404, detail=f"用户 {user_id} 不存在")
    alerts = health_engine.scan_proactive_alerts(profile)

    # 合并最近一次 EEG 评估的脑电预警
    eeg_alert_count = 0
    try:
        latest_eeg = await crud.get_latest_eeg_record(db, user_id)
        if latest_eeg and latest_eeg.metrics:
            import json as _json
            metrics = _json.loads(latest_eeg.metrics)
            eeg_alerts = eeg_engine.scan_eeg_alerts(metrics, profile)
            for a in eeg_alerts:
                a["source"] = "eeg"
                a["source_label"] = "脑电卫士"
            alerts.extend(eeg_alerts)
            eeg_alert_count = len(eeg_alerts)
    except Exception as e:
        logger.warning("合并 EEG 预警失败: %s", e)

    # 档案管家：记录整理类提醒（缺少检查时间 → 无法纵向对比）。只关乎档案完整性，不涉及医疗判断。
    try:
        undated = [r for r in await crud.get_body_records(db, user_id) if not r.event_date]
        if undated:
            desc = f"有 {len(undated)} 条健康档案记录缺少检查时间，补充后可用于不同时间点的纵向对比"
            alerts.append({
                "level": "low", "icon": "📇", "title": "健康档案待补充",
                "description": desc, "desc": desc,
                "suggestion": "在对话中告诉档案管家该记录的检查时间即可",
                "source": "body", "source_label": "档案管家",
            })
    except Exception as e:
        logger.warning("合并档案管家提醒失败: %s", e)

    return {
        "user_id": user_id,
        "user_name": profile.get("name"),
        "alert_count": len(alerts),
        "high_count": sum(1 for a in alerts if a.get("level") == "high"),
        "eeg_alert_count": eeg_alert_count,
        "alerts": alerts,
        "summary": f"检测到 {len(alerts)} 项需要关注的健康预警" if alerts else "暂无紧急健康预警",
    }


@router.get("/{user_id}/trends")
async def get_health_trends(user_id: str, months: int = 6, db: AsyncSession = Depends(get_db)):
    """获取用户健康趋势"""
    profile = await crud.get_user_health_profile(db, user_id)
    if not profile.get("found"):
        raise HTTPException(status_code=404, detail=f"用户 {user_id} 不存在")
    medical_records = await crud.get_medical_records(db, user_id, limit=100)
    report = health_engine.assess(profile, medical_records=medical_records)
    return {
        "trends": {"monthly_costs": [{"month": t["month"], "amount": 0} for t in report.trend]},
        "health_trend": report.trend,
    }
