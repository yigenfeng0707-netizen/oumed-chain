"""泛癌卫士 REST 接口（Oncoformer 泛癌预测）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import create_cancer_prediction, get_cancer_predictions, get_user
from app.database import get_db
from app.services.cancer import engine

router = APIRouter(prefix="/api/cancer", tags=["泛癌卫士"])


class PredictRequest(BaseModel):
    mode: str = Field(default="ehr_only", description="ehr_only（默认）/ fused（需真实胸片）")


def _patient_summary(p: dict) -> dict:
    """列表视图：每个模式只留 top3 风险，避免整包传输。"""
    modes = {}
    for mode, payload in p.get("modes", {}).items():
        scores = payload.get("scores", {})
        top = {}
        for horizon, row_map in scores.items():
            rows = sorted(row_map.items(), key=lambda kv: -kv[1])[:3]
            top[horizon] = [{"cancer": c, "prob": round(v, 4)} for c, v in rows]
        modes[mode] = {
            "top": top,
            "pred_age": payload.get("pred_age"),
            "n_visits": payload.get("n_visits"),
        }
    return {"pid": p["pid"], "meta": p.get("meta", {}), "modes": modes}


@router.get("/status")
async def cancer_status():
    """泛癌卫士服务形态（真模型 / 预计算）与队列统计。"""
    return engine.status()


@router.post("/{user_id}/predict")
async def predict_for_user(
    user_id: str, req: PredictRequest | None = None, db: AsyncSession = Depends(get_db)
):
    """对平台用户做泛癌风险预测并存档。"""
    user = await get_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    from app.crud import get_user_health_profile

    profile = await get_user_health_profile(db, user_id)
    report = await engine.predict_for_user(user_id, profile)
    if req and req.mode and req.mode != "ehr_only" and report["engine"] == "oncoformer":
        # 非 ehr_only 需要 picture 级输入，用户档案预测固定走 ehr_only，
        # 这里仅透传给报告标注（防止误标 fused）
        report["requested_mode"] = req.mode
    record = await create_cancer_prediction(
        db,
        user_id=user_id,
        engine=report["engine"],
        mode=report["mode"],
        source=report["source"],
        result=report,
    )
    return {"record_id": record.id, **report}


@router.get("/records/{user_id}")
async def prediction_history(user_id: str, limit: int = 10, db: AsyncSession = Depends(get_db)):
    """用户泛癌预测历史。"""
    records = await get_cancer_predictions(db, user_id, limit=limit)
    return [
        {
            "id": r.id,
            "recorded_at": r.recorded_at.isoformat() if r.recorded_at else None,
            "engine": r.engine,
            "mode": r.mode,
            "source": r.source,
            "result": r.result,
        }
        for r in records
    ]


@router.get("/cohort/patients")
async def cohort_patients():
    """COMPASS 示例队列列表（真模型实时环境额外标注 has_image）。"""
    from app.services.cancer import cohort as cohort_svc

    patients = [_patient_summary(p) for p in cohort_svc.list_patients()]
    return {"patients": patients, "population": cohort_svc.cohort_stats()}


@router.post("/cohort/{pid}/predict")
async def cohort_predict(pid: str, modes: str = "fused,ehr_only,img_only"):
    """队列患者多模态预测（本地真模型实时；云端返回预计算结果）。"""
    wanted = [m.strip() for m in modes.split(",") if m.strip()]
    try:
        return engine.predict_cohort_patient(pid, wanted)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
