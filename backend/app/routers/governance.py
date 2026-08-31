"""瓯医数链 · AI 病历治理路由（数据供给侧）

POST /api/governance/deidentify  —— PHI 脱敏（规则引擎，确定性）
POST /api/governance/govern      —— 脱敏 + 结构化全流水线（本地 LLM 优先，规则兜底）
"""

import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.deps import SessionDep
from app.services.governance import deidentify, govern

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/governance", tags=["AI病历治理"])


class NoteRequest(BaseModel):
    text: str = Field(min_length=5, max_length=20000, description="非结构化病历文本")
    use_llm: bool = Field(default=True, description="是否使用本地大模型结构化")


@router.post("/deidentify")
def deidentify_endpoint(req: NoteRequest, _session: str = SessionDep):
    """PHI 脱敏：身份证/手机号/姓名/住院号等敏感实体识别与掩码。"""
    result = deidentify(req.text)
    return result.to_dict()


@router.post("/govern")
def govern_endpoint(req: NoteRequest, _session: str = SessionDep):
    """完整治理流水线：脱敏 → 结构化（本地 qwen3:4b，失败自动规则兜底）。"""
    return govern(req.text, use_llm=req.use_llm)
