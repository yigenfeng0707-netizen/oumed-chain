"""
瓯医数链 - 数据管家路由（湖仓一体数据智能体）

- POST /api/data/query     智能数据查询（自然语言 → 只读 SQL → 结果）
- GET  /api/data/catalog   湖仓数据资产目录（仓层表 + 湖层文件 + 血缘）
- GET  /api/data/quality   数据质量报告与血缘说明

安全边界：只允许只读查询，SQL 经引擎安全校验（表白名单 + 强制 LIMIT），
个人数据访问的授权校验由安全守门（/api/security）统一管理。
"""

import asyncio
import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import SessionDep
from app.schemas import DataQueryRequest
from app.services import orchestrator
from app.services.data_lake import engine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/data", tags=["数据管家"])


@router.post("/query")
async def data_query(request: DataQueryRequest, db: AsyncSession = Depends(get_db), _session: str = SessionDep):
    """智能数据查询：自然语言问题 → 模板/LLM 生成只读 SQL → 执行并返回结构化结果。

    复用编排器的 LLM 实例（可用时走 NL2SQL，否则模板降级），
    45s 超时保护，超时降级为目录摘要。
    """
    try:
        result = await asyncio.wait_for(
            engine.smart_query(db, request.question, llm=orchestrator._llm, user_id=request.user_id),
            timeout=45.0,
        )
    except TimeoutError:
        logger.warning("智能数据查询超时(45s)，返回目录摘要")
        result = await engine.catalog(db)
        return {"answer_summary": "查询处理超时，先为您返回湖仓数据资产目录。", "catalog": result}

    return {
        **result,
        "chat_response": engine.format_chat_response(result),
    }


@router.get("/catalog")
async def data_catalog(db: AsyncSession = Depends(get_db)):
    """湖仓一体数据资产目录"""
    return await engine.catalog(db)


@router.get("/quality")
async def data_quality(db: AsyncSession = Depends(get_db)):
    """数据质量报告（口径异常/空值检查 + 血缘说明）"""
    return await engine.quality_report(db)
