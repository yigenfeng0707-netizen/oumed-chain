"""
瓯医数链 - 政策解读路由

P0-2 升级：
- match: 基于用户真实慢病/参保类型匹配政策（规则引擎，P1-3 接入 policy_matcher 完整算法）
- search: 真实调用 KnowledgeBase 向量检索
- detail: 查询 PolicyDocument 表
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from app.database import get_db
from app.schemas import PolicySearchRequest
from app.services import orchestrator, policy_matcher

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/policy", tags=["政策解读"])


@router.get("/match/{user_id}")
async def match_policies(user_id: str, db: AsyncSession = Depends(get_db)):
    """基于用户画像精准匹配医保政策（P1-3：policy_matcher 引擎）"""
    profile = await crud.get_user_health_profile(db, user_id)
    if not profile.get("found"):
        raise HTTPException(status_code=404, detail=f"用户 {user_id} 不存在")

    report = policy_matcher.match(profile)
    return report.to_dict()


@router.post("/search")
async def search_policies(request: PolicySearchRequest):
    """政策搜索：优先用 KnowledgeBase 向量检索，降级用 policy_matcher 关键词搜索"""
    query = request.query
    category = request.category

    results = []
    # 优先向量检索
    kb = orchestrator._kb
    if kb is not None:
        try:
            search_results = await kb.search(query, top_k=10, min_score=0.2, category=category)
            results = [
                {
                    "policy_id": i + 1,
                    "title": r.title,
                    "category": category or "综合",
                    "publish_date": "",
                    "summary": r.content[:200] if r.content else "",
                    "source": r.source,
                    "score": round(r.score, 4),
                }
                for i, r in enumerate(search_results)
            ]
        except Exception as e:
            logger.error("政策向量检索失败: %s，降级关键词搜索", e)

    # 降级：关键词搜索
    if not results:
        results = policy_matcher.search(query, category=category, top_k=10)

    return {"keyword": query, "results": results, "total": len(results)}


@router.get("/{policy_id}")
async def get_policy_detail(policy_id: int, db: AsyncSession = Depends(get_db)):
    """查询政策文档详情（查 PolicyDocument 表）"""
    doc = await crud.get_policy_document(db, policy_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"政策 {policy_id} 不存在")

    return {
        "policy_id": doc.id,
        "title": doc.title,
        "content": doc.content,
        "source": doc.source or "省级医疗保障部门",
        "publish_date": doc.publish_date.isoformat() if doc.publish_date else "",
        "category": doc.category or "综合",
        "tags": doc.tags or "",
    }
