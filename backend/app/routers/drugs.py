"""
瓯医数链 - 药品卫士路由（药品拍照识别与用药安全）

- POST /api/drugs/scan      药品拍照识别（视觉模型 → OCR+LLM → mock）+ 富化（归类/相互作用/有效期）
- POST /api/drugs/register  用户确认后把扫描到的药品写入 medication_records

扫描只读不写库；是否登记到用药记录必须由用户确认后显式调用 /register。
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from app.database import get_db
from app.deps import SessionDep
from app.schemas import DrugRegisterRequest
from app.services import orchestrator
from app.services.drug_scan import engine as drug_engine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/drugs", tags=["药品卫士"])


async def _existing_med_names(db: AsyncSession, user_id: str) -> list[str]:
    """用户现有用药名称列表（相互作用核查用）；查询失败返回空列表。"""
    try:
        user = await crud.get_user(db, user_id)
        if user is None:
            return []
        meds = await crud.get_medication_records(db, user.id, limit=100)
        return [m.medication_name for m in meds]
    except Exception as e:
        logger.warning("查询用户用药记录失败: %s", e)
        return []


@router.post("/scan")
async def scan_drug(
    file: UploadFile = File(...),
    user_id: str = "user_001",
    db: AsyncSession = Depends(get_db),
    _session: str = SessionDep,
):
    """拍照识别药品：返回结构化信息 + 类别 + 相互作用提示 + 有效期核验。

    本接口只读不写库；是否加入用药记录由用户确认后调用 /register 决定。
    """
    contents = await file.read()
    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件大小超过10MB限制")

    existing = await _existing_med_names(db, user_id)
    try:
        drug, source = await asyncio.wait_for(
            drug_engine.recognize_drug(contents, filename=file.filename or "drug.jpg", llm=orchestrator._llm),
            timeout=45.0,
        )
    except TimeoutError:
        logger.warning("药品识别超时(45s)，降级 mock")
        drug, source = drug_engine.mock_drug_result(), "mock"

    result = drug_engine.build_scan_result(drug, existing, source)
    return {**result, "chat_response": drug_engine.format_chat_response(result)}


@router.post("/register")
async def register_drug(request: DrugRegisterRequest, db: AsyncSession = Depends(get_db), _session: str = SessionDep):
    """用户确认后，把扫描到的药品登记到用药记录（并复核相互作用）。"""
    drug = request.drug or {}
    if not (str(drug.get("generic_name") or "").strip() or str(drug.get("brand_name") or "").strip()):
        raise HTTPException(status_code=400, detail="药品名称为空，无法登记")

    try:
        info = await drug_engine.register_drug(db, request.user_id, drug, category=request.category or "")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    # 登记后以最新用药列表复核相互作用（含新登记的药本身）
    existing = await _existing_med_names(db, request.user_id)
    interactions = drug_engine.check_interactions(info["medication_name"], existing)
    return {
        "registered": True,
        **info,
        "interactions": interactions,
        "message": f"已将 {info['medication_name']} 加入您的用药记录。",
    }
