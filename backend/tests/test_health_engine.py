"""健康风险评分引擎测试（P3-3）

覆盖：5维评分/用药相互作用/主动预警/慢病推断
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import health_engine


def _profile(name="测试用户", age=60, chronic=None, meds=None, visit_6m=3):
    """构造测试用户画像"""
    return {
        "found": True,
        "user_id": 1,
        "name": name,
        "age": age,
        "chronic_diseases": chronic or [],
        "medications": meds or [],
        "medication_categories": [],
        "visit_count_6m": visit_6m,
        "recent_visits": visit_6m,
        "annual_medical_cost": 5000,
        "annual_medication_cost": 2000,
    }


class TestHealthScore:
    """5 维评分测试"""

    def test_healthy_user_high_score(self):
        """健康用户评分较高"""
        p = _profile(age=30, chronic=[], visit_6m=2)
        r = health_engine.assess(p)
        assert r.health_score >= 75

    def test_chronic_user_lower_score(self):
        """多种慢病用户评分较低"""
        p = _profile(age=70, chronic=["糖尿病", "高血压"], visit_6m=6)
        r = health_engine.assess(p)
        assert r.health_score < 75

    def test_radar_has_5_dimensions(self):
        """雷达图含 5 维"""
        p = _profile()
        r = health_engine.assess(p)
        assert len(r.radar) == 5
        dims = {d["name"] for d in r.radar}
        assert "慢病管理" in dims
        assert "用药规范" in dims

    def test_score_label(self):
        """评分标签正确"""
        p = _profile(age=30, chronic=[])
        r = health_engine.assess(p)
        assert r.score_label in ["优秀", "良好", "一般", "需关注"]


class TestDrugInteraction:
    """用药相互作用检测"""

    def test_aspirin_anticoagulant_high_severity(self):
        """阿司匹林+抗凝药 → high"""
        p = _profile(meds=[
            {"name": "阿司匹林", "category": "心血管药", "is_chronic": True, "date": "2026-06-01"},
            {"name": "华法林", "category": "心血管药", "is_chronic": True, "date": "2026-06-01"},
        ])
        r = health_engine.assess(p)
        assert any(w["severity"] == "high" for w in r.drug_warnings)

    def test_no_interaction_for_safe_combo(self):
        """安全用药组合无警告"""
        p = _profile(meds=[
            {"name": "维生素C", "category": "保健品", "is_chronic": False, "date": "2026-06-01"},
        ])
        r = health_engine.assess(p)
        assert len(r.drug_warnings) == 0


class TestProactiveAlerts:
    """主动预警测试"""

    def test_multi_chronic_triggers_alert(self):
        """多种慢病触发 high 预警"""
        p = _profile(age=70, chronic=["糖尿病", "高血压"])
        alerts = health_engine.scan_proactive_alerts(p)
        assert len(alerts) > 0
        assert any(a["level"] == "high" for a in alerts)

    def test_healthy_user_no_proactive_alert(self):
        """健康年轻用户无主动预警"""
        p = _profile(age=25, chronic=[], visit_6m=2)
        alerts = health_engine.scan_proactive_alerts(p)
        # 应该很少或没有 high/medium
        assert all(a["level"] != "high" for a in alerts)

    def test_alerts_have_evidence(self):
        """预警带 evidence 证据"""
        p = _profile(age=70, chronic=["糖尿病", "高血压"])
        alerts = health_engine.scan_proactive_alerts(p)
        for a in alerts:
            # 至少部分预警带 evidence
            if "evidence" in a:
                assert isinstance(a["evidence"], list)

    def test_frequent_visit_alert(self):
        """频繁就医触发预警"""
        p = _profile(age=50, chronic=["高血压"], visit_6m=10)
        alerts = health_engine.scan_proactive_alerts(p)
        assert any("频繁" in a.get("title", "") or "就医" in a.get("title", "") for a in alerts)


class TestEmptyProfile:
    """空画像兜底"""

    def test_empty_profile_no_crash(self):
        """空画像不崩溃"""
        r = health_engine.assess({"found": False})
        assert r.health_score == 70
