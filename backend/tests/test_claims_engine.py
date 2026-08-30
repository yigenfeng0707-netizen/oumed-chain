"""报销计算引擎单元测试（P3-3）

覆盖：起付线/报销比例/封顶线/乙类自付/大病保险/调整因子/多场景对比
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import claims_engine as ce


class TestClaimsEngineBasic:
    """基础计算测试"""

    def test_simple_outpatient_employee(self):
        """职工医保门诊 1000 元（全部甲类，二级医院）"""
        r = ce.calculate(ce.ClaimsInput(
            total_amount=1000, visit_type="门诊",
            insurance_type="职工医保", hospital_level="二级",
        ))
        # 起付线 800，(1000-800)*0.75 = 150
        assert r.reimbursed_basic == 150.0
        assert r.out_of_pocket == 850.0
        assert r.deductible == 800
        assert r.rate == 0.75

    def test_inpatient_employee_third_tier(self):
        """职工医保住院 20000 元（三级医院）"""
        r = ce.calculate(ce.ClaimsInput(
            total_amount=20000, visit_type="住院",
            insurance_type="职工医保", hospital_level="三级",
            employee_status="在职",
        ))
        # 起付线 1000，(20000-1000)*0.85 = 16150
        assert r.reimbursed_basic == 16150.0
        assert r.out_of_pocket == 3850.0

    def test_resident_vs_employee(self):
        """居民医保报销比例低于职工医保"""
        r_emp = ce.calculate(ce.ClaimsInput(
            total_amount=10000, visit_type="住院",
            insurance_type="职工医保", hospital_level="二级",
        ))
        r_res = ce.calculate(ce.ClaimsInput(
            total_amount=10000, visit_type="住院",
            insurance_type="居民医保", hospital_level="二级",
        ))
        assert r_emp.reimbursed_basic > r_res.reimbursed_basic


class TestClaimsEngineAdjustments:
    """调整因子测试"""

    def test_chronic_disease_boost(self):
        """慢病待遇加成 +10%"""
        r_normal = ce.calculate(ce.ClaimsInput(
            total_amount=10000, visit_type="住院",
            insurance_type="职工医保", hospital_level="二级",
        ))
        r_chronic = ce.calculate(ce.ClaimsInput(
            total_amount=10000, visit_type="住院",
            insurance_type="职工医保", hospital_level="二级",
            chronic_disease=True,
        ))
        assert r_chronic.reimbursed_basic > r_normal.reimbursed_basic
        assert r_chronic.rate > r_normal.rate

    def test_cross_region_penalty(self):
        """异地就医扣减 -5%"""
        r_normal = ce.calculate(ce.ClaimsInput(
            total_amount=10000, visit_type="住院",
            insurance_type="职工医保", hospital_level="二级",
        ))
        r_cross = ce.calculate(ce.ClaimsInput(
            total_amount=10000, visit_type="住院",
            insurance_type="职工医保", hospital_level="二级",
            cross_region=True,
        ))
        assert r_cross.reimbursed_basic < r_normal.reimbursed_basic

    def test_retiree_boost(self):
        """退休人员加成 +5%"""
        r_active = ce.calculate(ce.ClaimsInput(
            total_amount=10000, visit_type="住院",
            insurance_type="职工医保", hospital_level="二级",
            employee_status="在职",
        ))
        r_retired = ce.calculate(ce.ClaimsInput(
            total_amount=10000, visit_type="住院",
            insurance_type="职工医保", hospital_level="二级",
            employee_status="退休",
        ))
        assert r_retired.reimbursed_basic > r_active.reimbursed_basic

    def test_rate_ceiling(self):
        """报销比例上限不超过 96%"""
        r = ce.calculate(ce.ClaimsInput(
            total_amount=10000, visit_type="住院",
            insurance_type="职工医保", hospital_level="社区",
            employee_status="退休", chronic_disease=True,
        ))
        assert r.rate <= 0.96


class TestClaimsEngineClassB:
    """乙类自付测试"""

    def test_class_b_deduction(self):
        """乙类药品先自付 10%"""
        r = ce.calculate(ce.ClaimsInput(
            total_amount=1000, visit_type="门诊",
            insurance_type="职工医保", hospital_level="二级",
            items=[
                ce.FeeItem(name="检查", amount=800, category="甲类"),
                ce.FeeItem(name="CT", amount=200, category="乙类"),
            ],
        ))
        # 乙类 200 × 10% = 20 自付
        assert r.class_b_deduction == 20.0
        assert r.class_b == 200
        assert r.class_a == 800


class TestClaimsEngineBigInsurance:
    """大病保险测试"""

    def test_big_insurance_triggered(self):
        """高额住院触发大病保险"""
        r = ce.calculate(ce.ClaimsInput(
            total_amount=200000, visit_type="住院",
            insurance_type="居民医保", hospital_level="三级",
            employee_status="退休",
        ))
        # 居民三甲住院：起付1000，(200000-1000)*0.65 = 129350
        # 个人自付 = 200000 - 129350 = 70650 > 15000，触发大病
        assert r.reimbursed_basic > 0
        assert r.big_insurance > 0
        assert len(r.big_insurance_tiers) >= 1
        assert r.total_reimbursed > r.reimbursed_basic

    def test_big_insurance_not_triggered(self):
        """小额住院不触发大病保险"""
        r = ce.calculate(ce.ClaimsInput(
            total_amount=5000, visit_type="住院",
            insurance_type="职工医保", hospital_level="二级",
        ))
        assert r.big_insurance == 0


class TestClaimsEngineOutput:
    """输出格式测试"""

    def test_steps_present(self):
        """分步推导存在且数量正确"""
        r = ce.calculate(ce.ClaimsInput(
            total_amount=1000, visit_type="门诊",
            insurance_type="职工医保",
        ))
        assert len(r.steps) >= 6  # 至少 6 步
        for step in r.steps:
            assert "name" in step
            assert "detail" in step

    def test_explanation_present(self):
        """自然语言解释存在"""
        r = ce.calculate(ce.ClaimsInput(
            total_amount=1000, visit_type="门诊",
            insurance_type="职工医保",
        ))
        assert r.explanation
        assert "职工医保" in r.explanation

    def test_to_dict_serializable(self):
        """to_dict 可序列化"""
        import json
        r = ce.calculate(ce.ClaimsInput(total_amount=1000))
        d = r.to_dict()
        json.dumps(d)  # 不抛异常即可

    def test_compare_scenarios(self):
        """多场景对比返回多个场景"""
        inp = ce.ClaimsInput(
            total_amount=10000, visit_type="住院",
            insurance_type="职工医保",
        )
        scenarios = ce.compare_scenarios(inp)
        assert len(scenarios) >= 3  # 至少社区/二级/三级三档
