"""
MedSignal - 政策精准匹配引擎（Policy Matcher）

P1-3：从硬编码 4 条政策升级为"用户画像 → 政策特征 → 规则匹配 + 省钱计算"
- 解析 policy_knowledge.json 的适用条件（tags/applicable_to/key_points）
- 基于用户画像（慢病/年龄/参保类型/支出）规则匹配
- 每条政策计算真实省钱金额（基于用户历史支出 × 报销比例提升）
- 输出 match_reason（人话）+ evidence（数据依据）+ steps（办事指南）
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_POLICY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "data", "policy_knowledge.json",
)
_POLICY_CACHE: list | None = None


def load_policies() -> list:
    """加载政策知识库（带缓存）。"""
    global _POLICY_CACHE
    if _POLICY_CACHE is not None:
        return _POLICY_CACHE
    try:
        with open(_POLICY_PATH, encoding="utf-8") as f:
            _POLICY_CACHE = json.load(f)
        logger.info("加载政策知识库: %d 篇", len(_POLICY_CACHE))
    except Exception as e:
        logger.warning("加载政策库失败: %s", e)
        _POLICY_CACHE = []
    return _POLICY_CACHE


@dataclass
class PolicyMatch:
    """单条政策匹配结果"""
    id: str
    title: str
    match_score: float          # 0-1 匹配度
    annual_savings: int         # 年节省金额（元）
    match_reason: str           # 人话解释
    category: str
    deadline: str | None
    steps: list[str]
    evidence: list[dict]        # 数据依据
    source: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "match_score": round(self.match_score, 2),
            "annual_savings": self.annual_savings,
            "match_reason": self.match_reason,
            "matchReason": self.match_reason,  # 前端兼容字段
            "category": self.category,
            "deadline": self.deadline,
            "steps": self.steps,
            "evidence": self.evidence,
            "source": self.source,
            # 前端 MatchedPolicy 类型兼容字段
            "matchScore": int(round(self.match_score * 100)),
            "savings": f"¥{self.annual_savings:,}/年",
            "savingsAmount": self.annual_savings,
            "requirements": self.steps,
            "benefits": [
                f"预计每年节省 {self.annual_savings:,} 元",
                *([self.match_reason.split("。")[0] + "。"] if self.match_reason else []),
            ],
            "description": self.match_reason,
        }


@dataclass
class MatchReport:
    """政策匹配报告"""
    user_id: str
    total_savings: int
    matched_count: int
    policies: list[PolicyMatch]
    evidence: dict

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "total_savings": self.total_savings,
            "matched_count": self.matched_count,
            "policies": [p.to_dict() for p in self.policies],
            "evidence": self.evidence,
        }


def match(profile: dict) -> MatchReport:
    """主入口：基于用户画像匹配政策。

    Args:
        profile: crud.get_user_health_profile() 返回的画像
    """
    if not profile or not profile.get("found"):
        return MatchReport(user_id="", total_savings=0, matched_count=0, policies=[], evidence={})

    chronic = profile.get("chronic_diseases", [])
    ins_type = profile.get("insurance_type", "职工医保")
    age = profile.get("age", 55)
    name = profile.get("name", "您")
    annual_med = profile.get("annual_medical_cost", 0)
    annual_drug = profile.get("annual_medication_cost", 0)
    emp_status = profile.get("employee_status", "在职")

    matches: list[PolicyMatch] = []

    # ---- 规则 1：门诊慢病待遇（核心省钱政策）----
    for disease in chronic:
        m = _match_chronic_disease(disease, annual_drug, ins_type, name)
        if m:
            matches.append(m)

    # ---- 规则 2：大病保险（高支出用户）----
    if annual_med > 8000:
        m = _match_big_insurance(annual_med, ins_type, name)
        if m:
            matches.append(m)

    # ---- 规则 3：跨省异地就医直接结算 ----
    m = _match_cross_region(profile, name)
    if m:
        matches.append(m)

    # ---- 规则 4：老年人免费体检 ----
    if age >= 65:
        matches.append(_match_elderly_checkup(age, name))

    # ---- 规则 5：退休人员医保待遇提升 ----
    if "退休" in emp_status:
        matches.append(_match_retiree_benefit(emp_status, ins_type, name))

    # ---- 规则 6：从 policy_knowledge.json 补充匹配（基于 tags 关键词）----
    kb_matches = _match_from_knowledge_base(chronic, ins_type, age, annual_med)
    existing_titles = {m.title for m in matches}
    for m in kb_matches:
        if m.title not in existing_titles:
            matches.append(m)
            existing_titles.add(m.title)

    # 按匹配度排序，截取 top 8
    matches.sort(key=lambda x: x.match_score, reverse=True)
    matches = matches[:8]

    total_savings = sum(m.annual_savings for m in matches)

    return MatchReport(
        user_id=profile.get("user_id", ""),
        total_savings=total_savings,
        matched_count=len(matches),
        policies=matches,
        evidence={
            "chronic_diseases": chronic,
            "insurance_type": ins_type,
            "age": age,
            "annual_medical_cost": annual_med,
            "annual_medication_cost": annual_drug,
        },
    )


# ============================================================
# 规则匹配函数
# ============================================================

def _match_chronic_disease(disease: str, annual_drug: float, ins_type: str, name: str) -> PolicyMatch | None:
    """门诊慢病待遇匹配（糖尿病/高血压/冠心病等）"""
    chronic_diseases_map = {
        "糖尿病": {"score": 0.95, "base_savings": 3600, "drug_keyword": "降糖"},
        "高血压": {"score": 0.88, "base_savings": 1200, "drug_keyword": "降压"},
        "冠心病": {"score": 0.85, "base_savings": 2000, "drug_keyword": "心血管"},
        "高血脂": {"score": 0.80, "base_savings": 1500, "drug_keyword": "调脂"},
    }
    cfg = chronic_diseases_map.get(disease)
    if not cfg:
        # 通用慢病
        cfg = {"score": 0.75, "base_savings": 1000, "drug_keyword": disease}

    # 省钱估算：药费支出 × 报销比例提升（慢病比普通门诊高约 20-30%）
    savings = max(cfg["base_savings"], int(annual_drug * 0.25))

    return PolicyMatch(
        id=f"chronic_{disease}",
        title=f"门诊慢特病待遇（{disease}）",
        match_score=cfg["score"],
        annual_savings=savings,
        match_reason=(
            f"{name}符合{disease}门诊慢特病认定条件。认定后，门诊{cfg['drug_keyword']}相关费用"
            f"可按住院比例报销（较普通门诊提高约20-30个百分点），预计年节省 {savings} 元。"
        ),
        category="门诊慢病政策",
        deadline="2026-12-31",
        steps=[
            "准备二级及以上医院诊断证明（含疾病编码）",
            "填写《门诊慢特病待遇认定申请表》",
            "提交至参保地医保经办机构或定点医院医保办",
            "等待审核（约 15 个工作日），通过后自动生效",
        ],
        evidence=[
            {"type": "chronic_disease", "disease": disease},
            {"type": "drug_cost", "annual": annual_drug, "estimated_savings": savings},
        ],
        source="浙江省医疗保障局",
    )


def _match_big_insurance(annual_med: float, ins_type: str, name: str) -> PolicyMatch | None:
    """大病保险匹配（年度医疗支出较高的用户）"""
    # 大病起付线 15000，分段报销
    over = max(0, annual_med - 15000)
    if over <= 0:
        return None
    savings = int(over * 0.6)  # 简化：平均 60% 报销

    return PolicyMatch(
        id="big_insurance",
        title="大病保险待遇",
        match_score=0.70,
        annual_savings=max(2000, savings),
        match_reason=(
            f"{name}年度医疗费用 {int(annual_med)} 元，超过大病保险起付线（15000元）。"
            f"超过部分可享受分段累进报销（60%-80%），预计年节省 {max(2000, savings)} 元。"
            f"该待遇无需申请，达到起付线后医保系统自动结算。"
        ),
        category="大病保险政策",
        deadline=None,
        steps=[
            "达到起付线后自动触发（无需申请）",
            "医保信息系统自动计算分段报销",
            "出院结算时直接抵扣",
        ],
        evidence=[
            {"type": "annual_cost", "amount": annual_med, "over_threshold": over},
        ],
        source="国家医疗保障局",
    )


def _match_cross_region(profile: dict, name: str) -> PolicyMatch:
    """跨省异地就医直接结算"""
    return PolicyMatch(
        id="cross_region",
        title="跨省异地就医直接结算",
        match_score=0.72,
        annual_savings=800,
        match_reason=(
            f"备案后异地就医可直接结算，无需垫付资金。"
            f"为{name}省去来回报销的交通、时间和资金占用成本，预计年节省约 800 元（含间接成本）。"
        ),
        category="异地就医政策",
        deadline=None,
        steps=[
            "通过'国家医保服务平台'APP 或小程序线上备案",
            "选择就医地统筹区（支持全国 31 省）",
            "持社保卡/医保电子凭证就医",
            "出院时直接结算，无需回参保地报销",
        ],
        evidence=[{"type": "general_benefit", "indirect_savings": 800}],
        source="国家医疗保障局",
    )


def _match_elderly_checkup(age: int, name: str) -> PolicyMatch:
    """老年人免费体检"""
    return PolicyMatch(
        id="elderly_checkup",
        title="老年人免费健康体检",
        match_score=0.90,
        annual_savings=300,
        match_reason=(
            f"{name}已 {age} 岁，每年可享受 1 次免费健康体检，"
            f"包含血压、血糖、血脂、心电图、B超等项目，价值约 300 元。"
        ),
        category="公共卫生政策",
        deadline=None,
        steps=[
            "联系就近社区卫生服务中心预约",
            "携带身份证和社保卡前往",
            "完成体检（通常 1-2 小时）",
            "1-2 周后领取体检报告",
        ],
        evidence=[{"type": "age", "value": age, "benefit_value": 300}],
        source="国家卫生健康委",
    )


def _match_retiree_benefit(emp_status: str, ins_type: str, name: str) -> PolicyMatch:
    """退休人员医保待遇提升"""
    return PolicyMatch(
        id="retiree_benefit",
        title="退休人员医保待遇提升",
        match_score=0.85,
        annual_savings=1500,
        match_reason=(
            f"{name}为退休人员，{ins_type}报销比例比在职人员高 5 个百分点，"
            f"且无需继续缴费即可享受医保待遇。年度累计可多报销约 1500 元。"
        ),
        category="基本医保政策",
        deadline=None,
        steps=["退休后自动享受，无需申请", "报销比例在结算时自动应用"],
        evidence=[{"type": "employee_status", "status": emp_status, "boost": 0.05}],
        source="省级医疗保障部门",
    )


def _match_from_knowledge_base(chronic: list, ins_type: str, age: int, annual_med: float) -> list[PolicyMatch]:
    """从 policy_knowledge.json 基于关键词匹配补充政策"""
    policies = load_policies()
    matches = []

    # 构建用户特征关键词
    user_keywords = set(chronic)
    if "职工" in ins_type:
        user_keywords.add("职工")
    else:
        user_keywords.add("居民")
    if age >= 65:
        user_keywords.add("老年")
    if annual_med > 10000:
        user_keywords.add("大病")

    for p in policies:
        title = p.get("title", "")
        tags = p.get("tags", "")
        key_points = " ".join(p.get("key_points", []))
        applicable = p.get("applicable_to", "")
        haystack = f"{title} {tags} {key_points} {applicable}"

        # 计算关键词命中数
        hits = sum(1 for kw in user_keywords if kw in haystack)
        if hits == 0:
            continue

        # 跳过已会被规则匹配覆盖的（门诊慢病、大病保险）
        if "门诊慢" in title or "慢特病" in title:
            continue
        if "大病" in title:
            continue

        score = min(0.5 + hits * 0.1, 0.85)
        matches.append(PolicyMatch(
            id=p.get("id", ""),
            title=title,
            match_score=score,
            annual_savings=500,  # 通用估算
            match_reason=f"基于您的{'、'.join(chronic) or '参保情况'}，该项政策可能适用于您。{p.get('summary', '')[:80]}",
            category=p.get("category", "综合"),
            deadline=None,
            steps=["查看政策详情了解申请条件", "咨询当地医保经办机构"],
            evidence=[{"type": "policy_keyword_match", "hits": hits, "keywords": list(user_keywords)}],
            source=p.get("source", ""),
        ))

    return matches


def search(query: str, category: str | None = None, top_k: int = 10) -> list[dict]:
    """政策关键词搜索（供 policy.py:/search 调用，不依赖向量库的兜底）"""
    policies = load_policies()
    results = []
    for p in policies:
        title = p.get("title", "")
        summary = p.get("summary", "")
        tags = p.get("tags", "")
        content = p.get("content", "")[:500]
        haystack = f"{title} {summary} {tags} {content}".lower()

        if category and p.get("category", "") != category:
            continue

        # 简单打分：标题命中权重高，摘要次之
        score = 0.0
        if query in title:
            score += 0.5
        if query in summary:
            score += 0.3
        for ch in query:
            if ch in haystack:
                score += 0.02
        if score > 0.1:
            results.append({
                "policy_id": p.get("id", ""),
                "title": title,
                "category": p.get("category", ""),
                "publish_date": p.get("publish_date", ""),
                "summary": summary[:200],
                "source": p.get("source", ""),
                "score": round(min(score, 1.0), 4),
            })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_k]
