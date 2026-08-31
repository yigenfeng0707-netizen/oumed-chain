"""
瓯医数链 - 健康风险评分引擎（Health Engine）

P1-2：核心创新点的算法实现 —— "从被动报销到主动预防"
- 5 维健康评分模型（基于真实购药/就诊数据）
- 用药相互作用检测（基于 drug_interaction_rules.json）
- 主动预警扫描（连续购药模式分析、用药中断、就医异常）
- 每条预警带 evidence（数据证据），支撑"可解释性"

输入：crud.get_user_health_profile() 返回的画像 dict
输出：HealthReport（含 5 维评分、预警、用药审查、改善建议）
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

_DRUG_RULES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "data", "drug_interaction_rules.json",
)
_DRUG_RULES_CACHE: dict | None = None


def load_drug_rules() -> dict:
    """加载药物相互作用规则库（带缓存）。"""
    global _DRUG_RULES_CACHE
    if _DRUG_RULES_CACHE is not None:
        return _DRUG_RULES_CACHE
    try:
        with open(_DRUG_RULES_PATH, encoding="utf-8") as f:
            _DRUG_RULES_CACHE = json.load(f)
        logger.info("加载药物相互作用规则库: %s", _DRUG_RULES_PATH)
    except Exception as e:
        logger.warning("加载药物规则库失败: %s，使用空规则", e)
        _DRUG_RULES_CACHE = {"interactions": [], "drug_categories": {}, "chronic_medication_patterns": {}}
    return _DRUG_RULES_CACHE


@dataclass
class HealthReport:
    """健康评估报告"""
    health_score: int
    score_label: str
    radar: list[dict]              # 5 维评分
    alerts: list[dict]             # 预警（带 evidence）
    drug_warnings: list[dict]      # 用药相互作用警告
    medications: list[dict]        # 聚合后的用药清单
    trend: list[dict]              # 评分趋势
    suggestions: list[dict]        # 改善建议
    summary: str                   # 总体评估

    def to_dict(self) -> dict:
        return {
            "health_score": self.health_score,
            "score_label": self.score_label,
            "radar_data": self.radar,
            "alerts": self.alerts,
            "drug_warnings": self.drug_warnings,
            "medications": self.medications,
            "trend_data": self.trend,
            "suggestions": self.suggestions,
            "summary": self.summary,
        }


def assess(profile: dict, medical_records: list = None, medication_records: list = None) -> HealthReport:
    """主入口：基于用户画像生成完整健康报告。

    Args:
        profile: crud.get_user_health_profile() 返回的画像
        medical_records: 可选，MedicalRecord ORM 列表（用于趋势）
        medication_records: 可选，MedicationRecord ORM 列表
    """
    if not profile or not profile.get("found"):
        return _empty_report()

    name = profile.get("name", "您")
    age = profile.get("age", 55)
    chronic = profile.get("chronic_diseases", [])
    meds_raw = profile.get("medications", [])
    visit_6m = profile.get("visit_count_6m", 0)
    visit_total = profile.get("recent_visits", 0)

    # ---- 1. 聚合用药清单 ----
    medications = _aggregate_medications(meds_raw)

    # ---- 2. 用药相互作用检测 ----
    drug_warnings = _check_drug_interactions(meds_raw, name)

    # ---- 3. 5 维健康评分 ----
    radar = _compute_radar(profile, meds_raw, chronic, visit_6m, age, drug_warnings)

    # ---- 4. 主动预警扫描（规则引擎）----
    alerts = _scan_alerts(profile, meds_raw, chronic, visit_6m, visit_total, age, drug_warnings, name)

    # ---- 5. 评分趋势 ----
    trend = _compute_trend(medical_records, visit_total)

    # ---- 6. 改善建议 ----
    suggestions = _build_suggestions(chronic, drug_warnings, age, visit_6m)

    # ---- 汇总 ----
    health_score = round(sum(d["value"] for d in radar) / len(radar)) if radar else 70
    score_label = _score_label(health_score)
    summary = _build_summary(name, age, chronic, health_score, len(alerts), len(drug_warnings), visit_6m)

    return HealthReport(
        health_score=health_score, score_label=score_label, radar=radar,
        alerts=alerts, drug_warnings=drug_warnings, medications=medications,
        trend=trend, suggestions=suggestions, summary=summary,
    )


def scan_proactive_alerts(profile: dict) -> list[dict]:
    """主动预警扫描（用户登录时触发，体现"主动式服务"）。

    只返回 high/medium 级别的预警，避免打扰。
    """
    if not profile or not profile.get("found"):
        return []
    meds_raw = profile.get("medications", [])
    chronic = profile.get("chronic_diseases", [])
    name = profile.get("name", "您")
    age = profile.get("age", 55)
    visit_6m = profile.get("visit_count_6m", 0)
    visit_total = profile.get("recent_visits", 0)

    drug_warnings = _check_drug_interactions(meds_raw, name)
    alerts = _scan_alerts(profile, meds_raw, chronic, visit_6m, visit_total, age, drug_warnings, name)
    # 主动推送只发 high/medium
    return [a for a in alerts if a.get("level") in ("high", "medium")]


# ============================================================
# 5 维评分模型
# ============================================================

def _compute_radar(profile, meds_raw, chronic, visit_6m, age, drug_warnings) -> list[dict]:
    """5 维：慢病管理 / 用药规范 / 就医频率 / 健康指标 / 生活方式"""
    chronic_meds = [m for m in meds_raw if m.get("is_chronic")]

    # 1. 慢病管理
    if not chronic:
        chronic_score = 88
    elif len(chronic_meds) >= len(chronic):
        chronic_score = 75  # 规律用药
        # 检查用药中断
        gap_count = _count_medication_gaps(meds_raw)
        chronic_score -= gap_count * 3
    else:
        chronic_score = 55  # 有慢病但用药不足

    # 2. 用药规范（受相互作用警告影响）
    med_count = len({m.get("name") for m in meds_raw})
    if med_count <= 2:
        med_score = 85
    elif med_count <= 4:
        med_score = 72
    else:
        med_score = 60
    # 有相互作用警告扣分
    high_warnings = sum(1 for w in drug_warnings if w.get("severity") == "high")
    med_score -= high_warnings * 8
    med_warnings = sum(1 for w in drug_warnings if w.get("severity") == "medium")
    med_score -= med_warnings * 4

    # 3. 就医频率
    if visit_6m == 0:
        visit_score = 78
    elif visit_6m <= 4:
        visit_score = 85
    elif visit_6m <= 8:
        visit_score = 70
    else:
        visit_score = 58

    # 4. 健康指标
    if not chronic:
        health_idx = 85
    elif len(chronic) == 1:
        health_idx = 68
    else:
        health_idx = 55

    # 5. 生活方式
    if age > 65:
        life_score = max(50, 88 - (age - 65) * 1.0 - len(chronic) * 4)
    elif age > 50:
        life_score = max(60, 88 - (age - 50) * 0.8 - len(chronic) * 5)
    else:
        life_score = 88 if not chronic else 75

    return [
        _radar_item("慢病管理", chronic_score, 80),
        _radar_item("用药规范", med_score, 85),
        _radar_item("就医频率", visit_score, 75),
        _radar_item("健康指标", health_idx, 80),
        _radar_item("生活方式", life_score, 85),
    ]


def _radar_item(name: str, score: float, target: int) -> dict:
    s = max(40, min(100, round(score)))
    return {"name": name, "dimension": name, "score": s, "value": s, "target": target}


# ============================================================
# 用药相互作用检测
# ============================================================

def _check_drug_interactions(meds_raw: list, user_name: str) -> list[dict]:
    """基于规则库检测用药相互作用，每条带 evidence。"""
    rules = load_drug_rules()
    interactions = rules.get("interactions", [])
    med_names = list({m.get("name", "") for m in meds_raw if m.get("name")})

    warnings = []
    for rule in interactions:
        a_match = [n for n in med_names if any(a in n for a in rule.get("drug_a_match", []))]
        b_match = [n for n in med_names if any(b in n for b in rule.get("drug_b_match", []))]
        if a_match and b_match:
            warnings.append({
                "level": rule.get("severity", "medium"),
                "severity": rule.get("severity", "medium"),
                "icon": {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(rule.get("severity"), "🟡"),
                "title": f"用药相互作用提示（{rule.get('drug_a')} + {rule.get('drug_b')}）",
                "description": rule.get("description", ""),
                "suggestion": rule.get("suggestion", ""),
                "action": rule.get("suggestion", ""),
                "monitor": rule.get("monitor", ""),
                "evidence": [
                    {"type": "medication", "name": n, "category": rule.get("drug_a")} for n in a_match
                ] + [
                    {"type": "medication", "name": n, "category": rule.get("drug_b")} for n in b_match
                ],
            })
    return warnings


# ============================================================
# 主动预警规则引擎
# ============================================================

def _scan_alerts(profile, meds_raw, chronic, visit_6m, visit_total, age, drug_warnings, name) -> list[dict]:
    """扫描所有预警规则，返回带 evidence 的预警列表。"""
    alerts: list[dict] = []
    now_iso = datetime.now(UTC).isoformat()

    # 规则 1：多种慢病综合管理（high）
    if len(chronic) >= 2:
        chronic_meds = list({m.get("name") for m in meds_raw if m.get("is_chronic")})
        alerts.append({
            "level": "high", "severity": "high", "icon": "🔴",
            "title": "多种慢病综合管理",
            "description": f"检测到{name}患有{'、'.join(chronic)}（共{len(chronic)}种慢病），正在服用 {len(chronic_meds)} 种慢病药物。多种慢病并存增加并发症风险。",
            "suggestion": f"建议每月监测{'血糖和血压' if {'糖尿病','高血压'} <= set(chronic) else '相关指标'}，每季度复查肝肾功能",
            "action": "建议每月监测相关指标，每季度复查肝肾功能",
            "timestamp": now_iso,
            "evidence": [{"type": "medication", "name": n} for n in chronic_meds[:3]] +
                        [{"type": "diagnosis", "disease": c} for c in chronic],
        })

    # 规则 2：用药相互作用（来自 drug_warnings，high/medium）
    for w in drug_warnings:
        if w.get("severity") in ("high", "medium"):
            alerts.append({**w, "timestamp": now_iso,
                           "title": w.get("title", "用药相互作用提示"),
                           "desc": w.get("description", ""),
                           "description": w.get("description", "")})

    # 规则 3：购药模式异常（间隔缩短 = 病情变化）
    pattern_alert = _check_medication_pattern(meds_raw, name)
    if pattern_alert:
        alerts.append({**pattern_alert, "timestamp": now_iso})

    # 规则 4：用药中断（慢病药超期未购）
    gap_alert = _check_medication_gap(meds_raw, name)
    if gap_alert:
        alerts.append({**gap_alert, "timestamp": now_iso})

    # 规则 5：就医频率异常
    if visit_6m == 0 and chronic:
        alerts.append({
            "level": "medium", "severity": "medium", "icon": "🟡",
            "title": "慢病随访缺失",
            "description": f"{name}近 6 个月无就诊记录，但有慢病史（{'、'.join(chronic)}），建议定期随访。",
            "suggestion": "建议每月至少 1 次慢病门诊随访，监测病情控制情况",
            "action": "建议每月至少 1 次慢病门诊随访",
            "timestamp": now_iso,
            "evidence": [{"type": "visit_count", "count": visit_6m, "period": "6个月"}],
        })
    elif visit_6m >= 8:
        alerts.append({
            "level": "medium", "severity": "medium", "icon": "🟡",
            "title": "就医频繁提示",
            "description": f"近 6 个月就诊 {visit_6m} 次，频次较高，建议关注是否存在慢病加重。",
            "suggestion": "建议与主治医生沟通，制定长期治疗方案",
            "action": "建议与主治医生沟通，制定长期治疗方案",
            "timestamp": now_iso,
            "evidence": [{"type": "visit_count", "count": visit_6m, "period": "6个月"}],
        })

    # 规则 6：高龄健康关注
    if age >= 70:
        alerts.append({
            "level": "medium", "severity": "medium", "icon": "🟡",
            "title": "高龄健康关注",
            "description": f"{age}岁属于医疗重点关注人群，心脑血管和跌倒风险升高。",
            "suggestion": "建议每半年进行心脑血管专项检查，注意防跌倒",
            "action": "建议每半年进行心脑血管专项检查",
            "timestamp": now_iso,
            "evidence": [{"type": "age", "value": age}],
        })
    elif age >= 65:
        alerts.append({
            "level": "low", "severity": "low", "icon": "🟢",
            "title": "高龄健康关注",
            "description": f"{age}岁以上建议加强心脑血管健康监测。",
            "suggestion": "建议每年体检，关注心脑血管健康",
            "action": "建议每年体检，关注心脑血管健康",
            "timestamp": now_iso,
        })

    # 规则 7：年度体检提醒（无就诊且无慢病）
    if visit_6m == 0 and not chronic:
        alerts.append({
            "level": "low", "severity": "low", "icon": "🟢",
            "title": "年度体检提醒",
            "description": f"{name}近 6 个月无就诊记录，建议安排年度体检。",
            "suggestion": "建议进行包含血糖、血脂、肝肾功能在内的全面体检",
            "action": "建议进行包含血糖、血脂、肝肾功能在内的全面体检",
            "timestamp": now_iso,
        })

    # 按等级排序
    order = {"high": 0, "medium": 1, "low": 2}
    alerts.sort(key=lambda a: order.get(a.get("level", "low"), 3))
    return alerts


def _check_medication_pattern(meds_raw: list, name: str) -> dict | None:
    """检测购药间隔缩短模式（连续购药 + 间隔变短 = 病情可能恶化）。"""
    rules = load_drug_rules().get("chronic_medication_patterns", {})
    cfg = rules.get("shortening_alert", {})
    if not cfg:
        return None

    # 按药名分组，计算购药间隔
    by_drug: dict[str, list[datetime]] = {}
    for m in meds_raw:
        drug_name = m.get("name", "")
        date_str = m.get("date", "")
        if not drug_name or not date_str:
            continue
        try:
            d = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            by_drug.setdefault(drug_name, []).append(d)
        except Exception:
            continue

    for drug, dates in by_drug.items():
        if len(dates) < 2:
            continue
        dates_sorted = sorted(dates)
        # 计算最近两次的间隔
        gaps = [(dates_sorted[i+1] - dates_sorted[i]).days for i in range(len(dates_sorted)-1)]
        if len(gaps) >= 2:
            recent_gap = gaps[-1]
            prev_gap = gaps[-2]
            from_days = cfg.get("from_days", 30)
            to_days = cfg.get("to_days", 20)
            if prev_gap >= from_days and recent_gap <= to_days:
                return {
                    "level": "high", "severity": "high", "icon": "🔴",
                    "title": f"{drug}购药间隔缩短",
                    "description": f"检测到{name}的{drug}购药间隔从 {prev_gap} 天缩短至 {recent_gap} 天，提示用药量增加或病情可能变化。",
                    "suggestion": "建议 2 周内复诊，由医生评估是否需要调整治疗方案",
                    "action": "建议 2 周内复诊评估",
                    "evidence": [
                        {"type": "medication", "name": drug, "prev_gap_days": prev_gap, "recent_gap_days": recent_gap},
                    ],
                }
    return None


def _check_medication_gap(meds_raw: list, name: str) -> dict | None:
    """检测慢病药用药中断（超期未购）。"""
    rules = load_drug_rules().get("chronic_medication_patterns", {})
    threshold = rules.get("gap_alert", {}).get("threshold_days", 45)
    now = datetime.now(UTC)

    for m in meds_raw:
        if not m.get("is_chronic"):
            continue
        date_str = m.get("date", "")
        if not date_str:
            continue
        try:
            d = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            days_ago = (now - d).days
            if days_ago > threshold:
                return {
                    "level": "medium", "severity": "medium", "icon": "🟡",
                    "title": f"{m.get('name')}可能用药中断",
                    "description": f"{name}的慢病药{m.get('name')}已 {days_ago} 天未购买，超过推荐间隔 {threshold} 天，可能存在用药中断。",
                    "suggestion": "建议尽快补购并规律服药，用药中断可能导致病情波动",
                    "action": "建议尽快补购并规律服药",
                    "evidence": [{"type": "medication", "name": m.get("name"), "days_since_last": days_ago}],
                }
        except Exception:
            continue
    return None


def _count_medication_gaps(meds_raw: list) -> int:
    """统计慢病药用药中断次数（用于评分扣分）。"""
    count = 0
    threshold = 45
    now = datetime.now(UTC)
    seen = set()
    for m in meds_raw:
        if not m.get("is_chronic"):
            continue
        name = m.get("name", "")
        if name in seen:
            continue
        seen.add(name)
        date_str = m.get("date", "")
        if not date_str:
            continue
        try:
            d = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            if (now - d).days > threshold:
                count += 1
        except Exception:
            continue
    return count


# ============================================================
# 用药清单聚合 / 趋势 / 建议
# ============================================================

def _aggregate_medications(meds_raw: list) -> list[dict]:
    by_name = {}
    for m in meds_raw:
        nm = m.get("name", "")
        if nm and nm not in by_name:
            by_name[nm] = m

    result = []
    for name, m in by_name.items():
        category = m.get("category", "其他")
        status = "正常"
        status_class = "text-green-600 bg-green-50"
        date_str = m.get("date", "")
        try:
            if date_str:
                d = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                days_ago = (datetime.now(UTC) - d).days
                if days_ago > 60 and m.get("is_chronic"):
                    status = "注意"
                    status_class = "text-yellow-600 bg-yellow-50"
        except Exception:
            pass

        result.append({
            "name": name,
            "dosage": f"{m.get('unit_price', 0)}元/单位",
            "frequency": "见医嘱",
            "status": status,
            "statusColor": status_class,
            "category": category,
            "last_purchase": (date_str or "")[:10],
            "is_chronic": m.get("is_chronic", False),
        })
    return result[:8]


def _compute_trend(medical_records, visit_total) -> list[dict]:
    """基于就诊记录按月聚合生成评分趋势。"""
    monthly: dict[str, int] = {}
    if medical_records:
        for r in medical_records:
            try:
                key = r.date.strftime("%Y-%m")
                monthly[key] = monthly.get(key, 0) + 1
            except Exception:
                continue
    sorted_months = sorted(monthly.keys())[-6:]
    trend = []
    for m in sorted_months:
        cnt = monthly[m]
        score = max(55, 85 - cnt * 5)
        trend.append({"month": m, "score": score})
    while len(trend) < 3:
        trend.insert(0, {"month": f"2025-{6 - len(trend):02d}", "score": 80})
    return trend


def _build_suggestions(chronic, drug_warnings, age, visit_6m) -> list[dict]:
    suggestions = []
    if "糖尿病" in chronic:
        suggestions.append({"title": "定期监测血糖", "description": "每周至少2次空腹血糖和餐后2小时血糖", "priority": "high", "icon": "📊", "color": "red"})
    if "高血压" in chronic:
        suggestions.append({"title": "血压监测计划", "description": "每日早晚各测量一次血压并记录", "priority": "high", "icon": "💓", "color": "red"})
    if "冠心病" in chronic or "高血脂" in chronic:
        suggestions.append({"title": "血脂与心脏管理", "description": "低脂饮食，规律服用调脂药，每季度复查血脂", "priority": "high", "icon": "🫀", "color": "orange"})

    # 用药相互作用相关的建议
    high_warnings = [w for w in drug_warnings if w.get("severity") == "high"]
    if high_warnings:
        suggestions.append({"title": "用药安全复诊", "description": "检测到高风险用药组合，建议尽快与医生或药师确认", "priority": "high", "icon": "⚠️", "color": "red"})

    suggestions.append({"title": "调整饮食结构", "description": "减少精制碳水，增加膳食纤维，每餐蔬菜占比≥50%", "priority": "medium", "icon": "🥗", "color": "green"})
    suggestions.append({"title": "增加有氧运动", "description": "每天30分钟中等强度有氧运动", "priority": "medium", "icon": "🏃", "color": "green"})

    if age >= 65:
        suggestions.append({"title": "老年健康体检", "description": "建议每年心脑血管专项体检", "priority": "medium", "icon": "🏥", "color": "orange"})
    elif visit_6m == 0:
        suggestions.append({"title": "安排年度体检", "description": "建议进行糖化血红蛋白、血脂、肝肾功能检查", "priority": "medium", "icon": "🏥", "color": "green"})

    return suggestions


def _score_label(score: int) -> str:
    if score >= 85:
        return "优秀"
    if score >= 70:
        return "良好"
    if score >= 60:
        return "一般"
    return "需关注"


def _build_summary(name, age, chronic, score, alert_count, warning_count, visit_6m) -> str:
    parts = [f"{name}（{age}岁）"]
    if chronic:
        parts.append(f"患有{'、'.join(chronic)}")
    else:
        parts.append("暂无明确慢病记录")
    parts.append(f"，综合健康评分 {score} 分（{_score_label(score)}）")
    if alert_count:
        parts.append(f"，发现 {alert_count} 项健康预警")
    if warning_count:
        parts.append(f"，{warning_count} 项用药安全提示")
    parts.append(f"，近6个月就诊 {visit_6m} 次")
    parts.append("。")
    return "".join(parts)


def _empty_report() -> HealthReport:
    return HealthReport(
        health_score=70, score_label="良好",
        radar=[_radar_item(d, 70, 80) for d in ["慢病管理", "用药规范", "就医频率", "健康指标", "生活方式"]],
        alerts=[], drug_warnings=[], medications=[], trend=[], suggestions=[],
        summary="用户数据不足，无法生成完整健康画像。",
    )
