"""政策精准匹配引擎测试（P3-3）

覆盖：慢病匹配/省钱计算/年龄差异化/知识库关键词匹配
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import policy_matcher


def _profile(name="测试用户", age=60, chronic=None, ins_type="职工医保",
             emp="在职", annual_med=5000, annual_drug=2000):
    return {
        "found": True,
        "user_id": 1,
        "name": name,
        "age": age,
        "chronic_diseases": chronic or [],
        "insurance_type": ins_type,
        "employee_status": emp,
        "annual_medical_cost": annual_med,
        "annual_medication_cost": annual_drug,
    }


class TestChronicMatching:
    """慢病政策匹配"""

    def test_diabetes_matches_chronic_policy(self):
        """糖尿病用户匹配门诊慢病待遇"""
        p = _profile(chronic=["糖尿病"])
        r = policy_matcher.match(p)
        titles = [m.title for m in r.policies]
        assert any("糖尿病" in t for t in titles)

    def test_hypertension_matches(self):
        """高血压用户匹配"""
        p = _profile(chronic=["高血压"])
        r = policy_matcher.match(p)
        titles = [m.title for m in r.policies]
        assert any("高血压" in t for t in titles)

    def test_multi_chronic_more_matches(self):
        """多种慢病匹配更多政策"""
        r1 = policy_matcher.match(_profile(chronic=["糖尿病"]))
        r2 = policy_matcher.match(_profile(chronic=["糖尿病", "高血压", "冠心病"]))
        assert r2.matched_count >= r1.matched_count


class TestSavingsCalculation:
    """省钱计算"""

    def test_savings_positive(self):
        """省钱金额 > 0"""
        p = _profile(chronic=["糖尿病"])
        r = policy_matcher.match(p)
        assert r.total_savings > 0

    def test_high_drug_cost_higher_savings(self):
        """高药费用户省钱更多"""
        r_low = policy_matcher.match(_profile(chronic=["糖尿病"], annual_drug=1000))
        r_high = policy_matcher.match(_profile(chronic=["糖尿病"], annual_drug=20000))
        assert r_high.total_savings >= r_low.total_savings


class TestAgeBased:
    """年龄差异化匹配"""

    def test_elderly_gets_checkup_policy(self):
        """65+ 岁匹配老年人体检"""
        p = _profile(age=70)
        r = policy_matcher.match(p)
        titles = [m.title for m in r.policies]
        assert any("体检" in t for t in titles)

    def test_young_no_checkup_policy(self):
        """35 岁不匹配老年人体检"""
        p = _profile(age=35, chronic=[])
        r = policy_matcher.match(p)
        titles = [m.title for m in r.policies]
        assert not any("老年" in t for t in titles)


class TestBigInsurance:
    """大病保险匹配"""

    def test_high_cost_triggers_big_insurance(self):
        """高支出触发大病保险"""
        p = _profile(chronic=[], annual_med=50000)
        r = policy_matcher.match(p)
        titles = [m.title for m in r.policies]
        assert any("大病" in t for t in titles)


class TestRetiree:
    """退休人员"""

    def test_retiree_gets_benefit(self):
        """退休人员匹配待遇提升"""
        p = _profile(emp="退休")
        r = policy_matcher.match(p)
        titles = [m.title for m in r.policies]
        assert any("退休" in t for t in titles)


class TestOutput:
    """输出格式"""

    def test_evidence_present(self):
        """匹配结果含 evidence"""
        p = _profile(chronic=["糖尿病"])
        r = policy_matcher.match(p)
        for m in r.policies:
            assert isinstance(m.evidence, list)
            assert len(m.evidence) > 0

    def test_match_reason_human_readable(self):
        """匹配理由是人话"""
        p = _profile(name="张阿姨", chronic=["糖尿病"])
        r = policy_matcher.match(p)
        for m in r.policies:
            assert "张阿姨" in m.match_reason or len(m.match_reason) > 20

    def test_to_dict_serializable(self):
        """to_dict 可序列化"""
        import json
        p = _profile(chronic=["糖尿病"])
        r = policy_matcher.match(p)
        json.dumps(r.to_dict())  # 不抛异常
