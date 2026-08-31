"""
瓯医数链 - 档案管家路由（人体健康档案）

- GET  /api/body/organs                 器官/部位分类表（前端 3D 网格契约）
- GET  /api/body/{user_id}/records      档案记录（时间倒序，可按 organ 过滤）
- POST /api/body/{user_id}/upload       上传 CT/MRI 报告、病历（图片/PDF/文本）→ 档案管家归档并回复

只增不删：本路由不提供任何修改/删除记录的接口。
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from app.database import get_db
from app.services import orchestrator
from app.services.body import extractor
from app.services.body.taxonomy import LABELS

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/body", tags=["档案管家"])


@router.get("/organs")
async def list_organs():
    """器官/部位 key → 中文名（前端 3D 模型网格命名契约）"""
    return {"organs": LABELS}


@router.get("/{user_id}/records")
async def get_records(user_id: str, organ: str | None = None, db: AsyncSession = Depends(get_db)):
    """用户健康档案记录：按检查时间倒序（未注明时间的排最后），每条带来源标签。"""
    if organ and organ not in LABELS:
        raise HTTPException(status_code=400, detail=f"未知部位: {organ}，可选 {list(LABELS)}")
    rows = await crud.get_body_records(db, user_id, organ=organ)
    return {
        "user_id": user_id,
        "total": len(rows),
        "organs": await crud.get_body_organ_summary(db, user_id),
        "records": [crud.body_record_to_dict(r) for r in rows],
        "disclaimer": extractor.DISCLAIMER,
    }


@router.post("/{user_id}/upload")
async def upload_document(user_id: str, file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    """上传医疗资料 → 转录文字 → 档案管家归档（追加）并给出回复。

    图片：阿里云视觉模型逐字转录（降级 OCR）；PDF：文本层；其他：按 UTF-8 文本读取。
    识别不到部位信息时资料仍会存档，records_added = 0。
    """
    contents = await file.read()
    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件大小超过10MB限制")

    filename = file.filename or "upload"
    mime = file.content_type or ""
    if mime.startswith("image/"):
        text = await extractor.extract_from_image(contents, mime, orchestrator._llm, filename)
    elif mime == "application/pdf" or filename.lower().endswith(".pdf"):
        text = extractor.extract_from_pdf(contents)
    else:
        text = contents.decode("utf-8", errors="ignore")

    doc_kind = extractor.classify_doc_kind(filename, text)
    try:
        user_profile = await crud.get_user_health_profile(db, user_id)
    except Exception as e:
        logger.warning("查询用户画像失败: %s", e)
        user_profile = None

    try:
        result = await asyncio.wait_for(
            orchestrator.handle_body_document(
                user_id, text, doc_kind, filename, mime, user_profile=user_profile, db=db,
            ),
            timeout=45.0,
        )
    except TimeoutError:
        logger.warning("档案管家处理上传超时(45s)")
        raise HTTPException(status_code=504, detail="档案管家处理超时，请稍后重试") from None
    return result
