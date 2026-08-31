"""
瓯医数链 - 医保待遇路由

P0-2 升级：从数据库查询真实数据，支持多用户切换
- get_coverage_summary: 查 User + InsuranceRecord 聚合
- estimate_reimbursement: 接入报销测算（P1-1 引擎接入前的过渡实现，含起付线/比例/封顶线）
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from app.database import get_db
from app.services import claims_engine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/coverage", tags=["医保待遇"])


@router.get("/{user_id}")
async def get_coverage_summary(user_id: str, db: AsyncSession = Depends(get_db)):
    """获取用户医保权益全景（基于真实数据库数据）"""
    user = await crud.get_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail=f"用户 {user_id} 不存在")

    records = await crud.get_insurance_records(db, user_id, limit=24)
    if not records:
        # 无缴费记录时返回基础信息
        return _empty_coverage(user)

    # 按时间正序（前端柱状图需要）
    records_asc = sorted(records, key=lambda r: (r.year, r.month))

    # 账户余额估算：个人缴费累计 × 入账比例（演示用，简化模型）
    personal_total = sum(r.personal_amount for r in records)
    account_balance = round(personal_total * 0.4, 2)  # 假设 40% 划入个人账户

    # 缴费年限
    months_count = await crud.get_payment_years(db, user_id)
    payment_years = f"{months_count // 12}年{months_count % 12}个月"

    # 报销比例（按参保类型/在职退休）
    outpatient_ratio, inpatient_ratio = _get_ratios(user.insurance_type, user.employee_status)

    # 最近活动（缴费 + 报销）
    recent_activities = _build_activities(records)
    return {
        "user": {
            "id": f"user_{user.id:03d}",
            "name": user.name,
            "age": user.age,
            "gender": user.gender,
            "city": user.city,
            "insurance_type": user.insurance_type,
            "employee_status": user.employee_status,
        },
        "payment_years": payment_years,
        "payment_months": months_count,
        "account_balance": account_balance,
        "outpatient_ratio": outpatient_ratio,
        "inpatient_ratio": inpatient_ratio,
        # 前端契约：payment_history 为对象数组
        "payment_history": [
            {
                "year": r.year,
                "month": r.month,
                "personal_amount": round(r.personal_amount, 2),
                "company_amount": round(r.company_amount, 2),
                "base_amount": round(r.base_amount, 2),
            }
            for r in records_asc
        ],
        # 兼容前端 number[] 柱状图（个人缴费额）
        "payment_history_values": [round(r.personal_amount, 2) for r in records_asc],
        "recent_activities": [
            {
                "date": a["date"],
                "type": a["type"],
                "desc": a["desc"],
                "amount": f"+¥{a['amount']:.2f}" if a["type"] == "缴费" else f"-¥{a['amount']:.2f}",
            }
            for a in recent_activities
        ],
    }


@router.get("/{user_id}/estimate")
async def estimate_reimbursement(
    user_id: str,
    total_cost: float = 10000.0,
    visit_type: str = "inpatient",
    hospital_level: str = "二级",
    chronic_disease: bool = False,
    cross_region: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """报销测算（基于完整 claims_engine + 多场景对比）

    P1-4：从内联计算升级为 claims_engine，支持大病保险/乙类自付/调整因子。
    visit_type 兼容 'inpatient'/'outpatient'/'住院'/'门诊'。
    """
    user = await crud.get_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail=f"用户 {user_id} 不存在")

    # 兼容 visit_type 中英文
    vt = "住院" if visit_type in ("inpatient", "住院") else "门诊"

    # 当前场景测算
    inp = claims_engine.ClaimsInput(
        total_amount=total_cost,
        visit_type=vt,
        insurance_type=user.insurance_type,
        hospital_level=hospital_level,
        employee_status=user.employee_status,
        chronic_disease=chronic_disease,
        cross_region=cross_region,
    )
    result = claims_engine.calculate(inp)

    # 多场景对比（数据赋能核心价值）
    comparison = claims_engine.compare_scenarios(inp)

    return {
        "user_id": user_id,
        "user_name": user.name,
        "insurance_type": user.insurance_type,
        "total_cost": total_cost,
        "visit_type": vt,
        "hospital_level": hospital_level,
        "chronic_disease": chronic_disease,
        "cross_region": cross_region,
        **result.to_dict(),
        "comparison": comparison,
    }


# ============================================================
# 内部工具
# ============================================================

def _get_ratios(insurance_type: str, employee_status: str) -> tuple[float, float]:
    """简化报销比例表（演示用，精确规则见 data/reimbursement_rules.json）"""
    if "职工" in insurance_type:
        if "退休" in employee_status:
            return 0.90, 0.95
        return 0.85, 0.90
    # 居民医保
    return 0.65, 0.75


def _get_reimbursement_rule(insurance_type: str, employee_status: str,
                            visit_type: str, hospital_level: str) -> dict:
    """获取报销规则（起付线/比例/封顶线）"""
    base = {
        "职工": {
            ("门诊", "社区"): {"deductible": 500, "rate": 0.80, "cap": 5000},
            ("门诊", "二级"): {"deductible": 800, "rate": 0.75, "cap": 5000},
            ("门诊", "三级"): {"deductible": 1000, "rate": 0.65, "cap": 5000},
            ("住院", "社区"): {"deductible": 300, "rate": 0.92, "cap": 300000},
            ("住院", "二级"): {"deductible": 600, "rate": 0.88, "cap": 300000},
            ("住院", "三级"): {"deductible": 1000, "rate": 0.85, "cap": 300000},
        },
        "居民": {
            ("门诊", "社区"): {"deductible": 200, "rate": 0.60, "cap": 3000},
            ("门诊", "二级"): {"deductible": 400, "rate": 0.50, "cap": 3000},
            ("门诊", "三级"): {"deductible": 800, "rate": 0.45, "cap": 3000},
            ("住院", "社区"): {"deductible": 200, "rate": 0.85, "cap": 200000},
            ("住院", "二级"): {"deductible": 500, "rate": 0.70, "cap": 200000},
            ("住院", "三级"): {"deductible": 1000, "rate": 0.60, "cap": 200000},
        },
    }
    ins_key = "职工" if "职工" in insurance_type else "居民"
    rules_table = base[ins_key]
    rule = rules_table.get((visit_type, hospital_level), rules_table[("住院", "二级")])

    # 退休加成
    if "退休" in employee_status:
        rule = {**rule, "rate": min(rule["rate"] + 0.05, 0.96)}
    return rule


def _build_activities(records) -> list[dict]:
    """把缴费记录转成最近活动列表（兼容前端 Activity 结构）"""
    activities = []
    for r in records[:8]:
        activities.append({
            "date": f"{r.year}-{r.month:02d}-15",
            "type": "缴费",
            "desc": f"{r.month}月医保缴费到账",
            "amount": round(r.personal_amount + r.company_amount, 2),
        })
    return activities


def _empty_coverage(user) -> dict:
    return {
        "user": {
            "id": f"user_{user.id:03d}",
            "name": user.name,
            "age": user.age,
            "gender": user.gender,
            "city": user.city,
            "insurance_type": user.insurance_type,
            "employee_status": user.employee_status,
        },
        "payment_years": "0年0个月",
        "payment_months": 0,
        "account_balance": 0.0,
        "outpatient_ratio": 0.65,
        "inpatient_ratio": 0.75,
        "payment_history": [],
        "recent_activities": [],
    }
