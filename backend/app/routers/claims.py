"""
瓯医数链 - 理赔助手路由

P0-2 升级：pre-review 基于入参 + 用户参保类型真实计算（过渡实现，P1-1 接入完整引擎）
"""

import contextlib
import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from app.database import get_db
from app.deps import SessionDep
from app.schemas import PreReviewRequest
from app.services import claims_engine
from app.services.ocr_service import get_ocr_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/claims", tags=["理赔助手"])


@router.post("/ocr")
async def ocr_process(file: UploadFile = File(...), _session: str = SessionDep):
    """OCR 识别医疗发票图片（真实调用 OCR.space，失败降级 mock）"""
    contents = await file.read()
    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件大小超过10MB限制")
    ocr_service = get_ocr_service()
    result = await ocr_service.recognize(contents, filename=file.filename or "receipt.jpg")
    return result


@router.post("/pre-review")
async def pre_review(request: PreReviewRequest, db: AsyncSession = Depends(get_db), _session: str = SessionDep):
    """报销预审：基于完整报销计算引擎，分步推导 + 大病保险 + 自然语言解释

    P1-1 升级：从过渡实现接入 claims_engine 完整算法。
    """
    # 从用户参保类型增强入参
    insurance_type = request.insurance_type or "职工医保"
    user = None
    with contextlib.suppress(Exception):
        user = await crud.get_user(db, request.user_id) if hasattr(request, "user_id") and request.user_id else None
    if user is not None:
        insurance_type = user.insurance_type

    inp = claims_engine.ClaimsInput(
        total_amount=request.total_amount,
        visit_type=request.visit_type or "门诊",
        insurance_type=insurance_type,
        hospital_level="二级",
        chronic_disease=False,
    )
    result = claims_engine.calculate(inp)

    return {
        **result.to_dict(),
        "required_documents": [
            {"name": "门诊病历", "status": "uploaded"},
            {"name": "费用清单", "status": "uploaded"},
            {"name": "处方笺", "status": "uploaded"},
            {"name": "检查报告", "status": "missing"},
            {"name": "转诊单", "status": "not_required"},
        ],
    }


@router.post("/prereview-uploaded")
async def prereview_uploaded(user_id: str = "user_001", db: AsyncSession = Depends(get_db), _session: str = SessionDep):
    """多文件上传后的编排预审：档案管家（存档汇总）× 报销助手（解读+完整性+测算）协同。

    读取用户最近存档的上传资料（BodyDocument.extracted_text），不重复 OCR。
    """
    docs = await crud.list_recent_body_documents(db, user_id, limit=10, within_minutes=120)
    if not docs:
        return {
            "response": "**【报销助手】**\n未检测到最近上传的报销材料，请先通过回形针上传发票、病历等资料，再发起预审。",
            "agents_invoked": ["claims_agent"],
            "multi_agent": False,
            "documents": [],
            "total_amount": None,
            "completeness": [],
            "estimate": None,
        }

    review = await claims_engine.build_uploaded_prereview(db, user_id)
    return {
        "response": review["response"],
        "agents_invoked": ["body_agent", "claims_agent"],
        "multi_agent": True,
        "documents": review["documents"],
        "total_amount": review["total_amount"],
        "completeness": review["completeness"],
        "estimate": review["estimate"],
    }
