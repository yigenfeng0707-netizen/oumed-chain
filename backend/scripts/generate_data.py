#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MedSignal - 合成数据生成脚本

为10个典型用户画像生成逼真的医保数据，包括：
- 用户基本信息
- 医保缴费记录（3-5年，按月）
- 就诊记录（门诊/住院，含DRG编码）
- 购药记录（含慢性病标记）

输出：
- mock_data.json: 完整JSON数据
- users.csv / insurance_records.csv / medical_records.csv / medication_records.csv: 分类CSV

用法：
    python generate_data.py
"""

import csv
import json
import os
import random
from datetime import datetime, timedelta
from pathlib import Path

# ============================================================
# 项目路径配置
# ============================================================
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # yibao-zhinao/
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================
# 随机种子（保证可复现）
# ============================================================
random.seed(42)

# ============================================================
# 10个典型用户画像
# ============================================================
USER_PROFILES = [
    {
        "name": "张阿姨", "age": 58, "gender": "女", "city": "杭州",
        "insurance_type": "职工医保", "employee_status": "退休",
        "conditions": ["糖尿病", "高血压"],
    },
    {
        "name": "李大爷", "age": 72, "gender": "男", "city": "苏州",
        "insurance_type": "居民医保", "employee_status": "退休",
        "conditions": ["冠心病"],
    },
    {
        "name": "王先生", "age": 35, "gender": "男", "city": "杭州",
        "insurance_type": "职工医保", "employee_status": "在职",
        "conditions": [],
    },
    {
        "name": "赵女士", "age": 42, "gender": "女", "city": "无锡",
        "insurance_type": "职工医保", "employee_status": "在职",
        "conditions": ["甲状腺结节"],
    },
    {
        "name": "陈同学", "age": 22, "gender": "男", "city": "杭州",
        "insurance_type": "居民医保", "employee_status": "学生",
        "conditions": [],
    },
    {
        "name": "刘阿姨", "age": 65, "gender": "女", "city": "常州",
        "insurance_type": "职工医保", "employee_status": "退休",
        "conditions": ["糖尿病", "骨质疏松"],
    },
    {
        "name": "周先生", "age": 50, "gender": "男", "city": "杭州",
        "insurance_type": "职工医保", "employee_status": "在职",
        "conditions": ["胃病"],
    },
    {
        "name": "吴女士", "age": 28, "gender": "女", "city": "苏州",
        "insurance_type": "职工医保", "employee_status": "在职",
        "conditions": [],
    },
    {
        "name": "孙大爷", "age": 78, "gender": "男", "city": "南通",
        "insurance_type": "居民医保", "employee_status": "退休",
        "conditions": ["高血压", "关节炎", "白内障"],
    },
    {
        "name": "郑先生", "age": 45, "gender": "男", "city": "杭州",
        "insurance_type": "灵活就业医保", "employee_status": "灵活就业",
        "conditions": ["腰椎间盘突出"],
    },
]

# ============================================================
# 医院名称库（按城市分组）
# ============================================================
HOSPITALS = {
    "杭州": [
        "浙江大学医学院附属第一医院", "浙江大学医学院附属第二医院", "浙江省人民医院",
        "杭州市第一人民医院", "浙江省中医院", "杭州市中医院",
        "浙江大学医学院附属邵逸夫医院", "杭州市红十字会医院",
    ],
    "宁波": [
        "宁波大学附属第一医院", "宁波市第二医院", "宁波市医疗中心李惠利医院",
        "宁波市中医院", "中国科学院大学宁波华美医院",
    ],
    "温州": [
        "温州医科大学附属第一医院", "温州医科大学附属第二医院", "温州市人民医院",
        "温州市中医院",
    ],
    "嘉兴": [
        "嘉兴市第一医院", "嘉兴市第二医院", "嘉兴市中医医院",
    ],
    "绍兴": [
        "绍兴市人民医院", "绍兴文理学院附属医院", "绍兴市中医院",
    ],
}

# ============================================================
# 科室名称库
# ============================================================
DEPARTMENTS = {
    "糖尿病": ["内分泌科", "内科", "中医科"],
    "高血压": ["心内科", "内科", "中医科"],
    "冠心病": ["心内科", "内科", "心血管内科"],
    "甲状腺结节": ["内分泌科", "甲状腺外科", "普外科"],
    "骨质疏松": ["骨科", "内分泌科", "老年科"],
    "胃病": ["消化内科", "内科", "中医脾胃科"],
    "关节炎": ["骨科", "风湿免疫科", "中医科"],
    "白内障": ["眼科"],
    "腰椎间盘突出": ["骨科", "脊柱外科", "康复科", "疼痛科"],
    "感冒": ["呼吸内科", "内科", "急诊科"],
    "体检": ["体检中心", "全科"],
}

GENERAL_DEPARTMENTS = ["内科", "外科", "急诊科", "全科", "中医科"]

# ============================================================
# 诊断名称库
# ============================================================
DIAGNOSES = {
    "糖尿病": ["2型糖尿病", "2型糖尿病伴周围神经病变", "2型糖尿病伴肾病", "糖尿病酮症"],
    "高血压": ["高血压病2级", "高血压病3级", "高血压性心脏病", "高血压伴肾损害"],
    "冠心病": ["冠状动脉粥样硬化性心脏病", "不稳定型心绞痛", "急性心肌梗死", "冠心病PCI术后"],
    "甲状腺结节": ["甲状腺结节", "结节性甲状腺肿", "甲状腺腺瘤"],
    "骨质疏松": ["骨质疏松症", "绝经后骨质疏松", "老年性骨质疏松"],
    "胃病": ["慢性胃炎", "胃溃疡", "十二指肠溃疡", "浅表性胃炎", "萎缩性胃炎"],
    "关节炎": ["骨关节炎", "类风湿性关节炎", "退行性骨关节病"],
    "白内障": ["老年性白内障", "年龄相关性白内障"],
    "腰椎间盘突出": ["腰椎间盘突出症", "腰椎管狭窄症", "腰椎滑脱症"],
}

# 偶发疾病（用于健康人群的偶发就诊）
INCIDENTAL_DIAGNOSES = [
    "上呼吸道感染", "急性支气管炎", "急性胃肠炎", "过敏性鼻炎",
    "急性扁桃体炎", "尿路感染", "皮肤湿疹", "荨麻疹",
    "软组织损伤", "结膜炎", "牙周炎", "颈椎病",
]

# ============================================================
# DRG编码库（住院记录适用）
# ============================================================
DRG_CODES = {
    "2型糖尿病": "K11", "2型糖尿病伴周围神经病变": "K11",
    "2型糖尿病伴肾病": "K11", "高血压病2级": "K12",
    "高血压病3级": "K12", "冠状动脉粥样硬化性心脏病": "F11",
    "不稳定型心绞痛": "F11", "急性心肌梗死": "F11",
    "冠心病PCI术后": "F11", "甲状腺结节": "J11",
    "结节性甲状腺肿": "J11", "骨质疏松症": "I79",
    "老年性骨质疏松": "I79", "慢性胃炎": "K11",
    "胃溃疡": "K11", "骨关节炎": "I79",
    "类风湿性关节炎": "I79", "老年性白内障": "L11",
    "年龄相关性白内障": "L11", "腰椎间盘突出症": "I79",
    "腰椎管狭窄症": "I79", "急性胃肠炎": "K11",
    "上呼吸道感染": "K11",
}

# ============================================================
# 药品名称库（按类别分组）
# ============================================================
MEDICATIONS = {
    "降糖药": [
        {"name": "二甲双胍片", "price_range": (15, 45), "spec": "0.5g×20片"},
        {"name": "格列美脲片", "price_range": (25, 60), "spec": "2mg×30片"},
        {"name": "阿卡波糖片", "price_range": (60, 120), "spec": "50mg×30片"},
        {"name": "达格列净片", "price_range": (150, 280), "spec": "10mg×14片"},
        {"name": "胰岛素注射液", "price_range": (50, 120), "spec": "300IU/支"},
        {"name": "利拉鲁肽注射液", "price_range": (300, 500), "spec": "3ml:18mg"},
    ],
    "降压药": [
        {"name": "氨氯地平片", "price_range": (15, 40), "spec": "5mg×28片"},
        {"name": "缬沙坦胶囊", "price_range": (30, 70), "spec": "80mg×7粒"},
        {"name": "厄贝沙坦片", "price_range": (25, 55), "spec": "150mg×7片"},
        {"name": "硝苯地平控释片", "price_range": (30, 65), "spec": "30mg×7片"},
        {"name": "替米沙坦片", "price_range": (20, 50), "spec": "80mg×7片"},
    ],
    "心血管药": [
        {"name": "阿托伐他汀钙片", "price_range": (30, 80), "spec": "20mg×7片"},
        {"name": "瑞舒伐他汀钙片", "price_range": (40, 90), "spec": "10mg×7片"},
        {"name": "氯吡格雷片", "price_range": (80, 150), "spec": "75mg×7片"},
        {"name": "阿司匹林肠溶片", "price_range": (10, 25), "spec": "100mg×30片"},
        {"name": "单硝酸异山梨酯片", "price_range": (15, 35), "spec": "20mg×48片"},
    ],
    "骨科药": [
        {"name": "碳酸钙D3片", "price_range": (25, 55), "spec": "600mg×60片"},
        {"name": "阿仑膦酸钠片", "price_range": (40, 90), "spec": "70mg×4片"},
        {"name": "塞来昔布胶囊", "price_range": (35, 75), "spec": "200mg×6粒"},
        {"name": "双氯芬酸钠缓释片", "price_range": (10, 30), "spec": "75mg×20片"},
        {"name": "硫酸氨基葡萄糖胶囊", "price_range": (60, 120), "spec": "0.25g×20粒"},
    ],
    "眼科药": [
        {"name": "吡诺克辛钠滴眼液", "price_range": (30, 60), "spec": "15ml:0.8mg"},
        {"name": "玻璃酸钠滴眼液", "price_range": (20, 50), "spec": "5ml:5mg"},
        {"name": "左氧氟沙星滴眼液", "price_range": (10, 25), "spec": "5ml:15mg"},
    ],
    "胃药": [
        {"name": "奥美拉唑肠溶胶囊", "price_range": (15, 40), "spec": "20mg×14粒"},
        {"name": "雷贝拉唑钠肠溶片", "price_range": (30, 65), "spec": "10mg×7片"},
        {"name": "铝碳酸镁片", "price_range": (20, 45), "spec": "0.5g×30片"},
        {"name": "莫沙必利片", "price_range": (20, 45), "spec": "5mg×20片"},
    ],
    "甲状腺药": [
        {"name": "左甲状腺素钠片", "price_range": (25, 55), "spec": "50μg×100片"},
        {"name": "甲巯咪唑片", "price_range": (10, 25), "spec": "5mg×100片"},
    ],
    "抗生素": [
        {"name": "阿莫西林胶囊", "price_range": (8, 20), "spec": "0.5g×24粒"},
        {"name": "头孢克洛缓释片", "price_range": (20, 45), "spec": "0.375g×6片"},
        {"name": "左氧氟沙星片", "price_range": (10, 30), "spec": "0.5g×6片"},
        {"name": "阿奇霉素片", "price_range": (15, 35), "spec": "0.25g×6片"},
    ],
    "中成药": [
        {"name": "复方丹参滴丸", "price_range": (20, 45), "spec": "27mg×180丸"},
        {"name": "速效救心丸", "price_range": (20, 40), "spec": "40mg×120丸"},
        {"name": "六味地黄丸", "price_range": (10, 25), "spec": "360丸/瓶"},
        {"name": "血塞通胶囊", "price_range": (25, 55), "spec": "50mg×30粒"},
        {"name": "银杏叶片", "price_range": (15, 35), "spec": "19.2mg×24片"},
        {"name": "活血止痛胶囊", "price_range": (15, 35), "spec": "0.25g×40粒"},
        {"name": "胃苏颗粒", "price_range": (20, 40), "spec": "5g×3袋"},
    ],
    "感冒药": [
        {"name": "连花清瘟胶囊", "price_range": (12, 28), "spec": "0.35g×48粒"},
        {"name": "感冒灵颗粒", "price_range": (8, 18), "spec": "10g×9袋"},
        {"name": "布洛芬缓释胶囊", "price_range": (10, 22), "spec": "0.3g×20粒"},
    ],
}

# 疾病对应的常用药类别映射
CONDITION_MEDICATION_MAP = {
    "糖尿病": ["降糖药", "中成药"],
    "高血压": ["降压药", "心血管药", "中成药"],
    "冠心病": ["心血管药", "中成药"],
    "甲状腺结节": ["甲状腺药"],
    "骨质疏松": ["骨科药"],
    "胃病": ["胃药", "中成药"],
    "关节炎": ["骨科药", "中成药"],
    "白内障": ["眼科药"],
    "腰椎间盘突出": ["骨科药", "中成药"],
}

# ============================================================
# 医保缴费基数范围（按类型）
# ============================================================
INSURANCE_BASE = {
    "职工医保": {"base": (4000, 8000), "personal_rate": 0.02, "company_rate": 0.08},
    "居民医保": {"base": (500, 800), "personal_rate": 1.0, "company_rate": 0.0},
    "灵活就业医保": {"base": (4000, 8000), "personal_rate": 0.10, "company_rate": 0.0},
}

# 报销比例范围
REIMBURSEMENT_RATE = {
    "职工医保": {"outpatient": (0.70, 0.90), "inpatient": (0.80, 0.95)},
    "居民医保": {"outpatient": (0.50, 0.70), "inpatient": (0.60, 0.80)},
    "灵活就业医保": {"outpatient": (0.65, 0.85), "inpatient": (0.75, 0.90)},
}


# ============================================================
# 工具函数
# ============================================================
def random_date(start: datetime, end: datetime) -> datetime:
    """在给定范围内生成随机日期"""
    delta = end - start
    random_days = random.randint(0, delta.days)
    return start + timedelta(days=random_days)


def round_to_2(val: float) -> float:
    """保留两位小数"""
    return round(val, 2)


def generate_insurance_records(user_id: int, profile: dict) -> list:
    """
    生成医保缴费记录
    - 职工/灵活就业：3-5年按月缴费
    - 居民医保：3-5年按年缴费
    """
    records = []
    insurance_type = profile["insurance_type"]
    config = INSURANCE_BASE[insurance_type]

    # 缴费年限：3-5年
    years = random.randint(3, 5)
    end_year = 2025
    start_year = end_year - years + 1

    base_amount = random.uniform(*config["base"])
    # 每年略有调整（3%-8%涨幅）
    yearly_bases = {}
    for y in range(start_year, end_year + 1):
        growth = 1 + random.uniform(0.03, 0.08) * (y - start_year)
        yearly_bases[y] = round_to_2(base_amount * growth)

    if insurance_type == "居民医保":
        # 居民医保按年缴费
        for y in range(start_year, end_year + 1):
            base = yearly_bases[y]
            personal = round_to_2(base * config["personal_rate"])
            records.append({
                "user_id": user_id,
                "year": y,
                "month": 1,  # 居民医保每年1月缴
                "base_amount": base,
                "personal_amount": personal,
                "company_amount": 0.0,
            })
    else:
        # 职工/灵活就业按月缴费
        for y in range(start_year, end_year + 1):
            base = yearly_bases[y]
            for m in range(1, 13):
                personal = round_to_2(base * config["personal_rate"])
                company = round_to_2(base * config["company_rate"])
                records.append({
                    "user_id": user_id,
                    "year": y,
                    "month": m,
                    "base_amount": base,
                    "personal_amount": personal,
                    "company_amount": company,
                })

    return records


def generate_medical_records(user_id: int, profile: dict) -> list:
    """
    生成就诊记录
    - 慢性病患者：5-20条，含定期复查
    - 健康人群：2-5条，偶发就诊
    """
    records = []
    conditions = profile["conditions"]
    city = profile["city"]
    insurance_type = profile["insurance_type"]
    hospitals = HOSPITALS.get(city, HOSPITALS["杭州"])
    reimb_config = REIMBURSEMENT_RATE[insurance_type]

    # 时间范围：2022-01-01 到 2025-12-31
    date_start = datetime(2022, 1, 1)
    date_end = datetime(2025, 12, 31)

    if conditions:
        # 慢性病患者：5-20条记录
        num_records = random.randint(5, 20)

        for _ in range(num_records):
            # 70%概率为慢性病相关就诊，30%为其他偶发就诊
            if random.random() < 0.7:
                condition = random.choice(conditions)
                diagnosis = random.choice(DIAGNOSES.get(condition, [condition]))
                departments = DEPARTMENTS.get(condition, GENERAL_DEPARTMENTS)
                department = random.choice(departments)
            else:
                diagnosis = random.choice(INCIDENTAL_DIAGNOSES)
                department = random.choice(GENERAL_DEPARTMENTS)

            hospital = random.choice(hospitals)
            visit_date = random_date(date_start, date_end)

            # 住院/门诊比例：慢性病患者约20%住院
            if random.random() < 0.2:
                visit_type = "住院"
                total_cost = round_to_2(random.uniform(5000, 80000))
                rate = random.uniform(*reimb_config["inpatient"])
            else:
                visit_type = "门诊"
                total_cost = round_to_2(random.uniform(100, 2000))
                rate = random.uniform(*reimb_config["outpatient"])

            reimbursed = round_to_2(total_cost * rate)

            record = {
                "user_id": user_id,
                "date": visit_date.strftime("%Y-%m-%d %H:%M:%S"),
                "hospital": hospital,
                "department": department,
                "diagnosis": diagnosis,
                "visit_type": visit_type,
                "total_cost": total_cost,
                "reimbursed_amount": reimbursed,
            }

            # 住院记录附加DRG编码
            if visit_type == "住院" and diagnosis in DRG_CODES:
                record["drg_code"] = DRG_CODES[diagnosis]

            records.append(record)
    else:
        # 健康人群：2-5条偶发就诊
        num_records = random.randint(2, 5)

        for _ in range(num_records):
            diagnosis = random.choice(INCIDENTAL_DIAGNOSES)
            department = random.choice(GENERAL_DEPARTMENTS)
            hospital = random.choice(hospitals)
            visit_date = random_date(date_start, date_end)

            # 健康人群偶尔住院（5%概率）
            if random.random() < 0.05:
                visit_type = "住院"
                total_cost = round_to_2(random.uniform(5000, 30000))
                rate = random.uniform(*reimb_config["inpatient"])
            else:
                visit_type = "门诊"
                total_cost = round_to_2(random.uniform(100, 1500))
                rate = random.uniform(*reimb_config["outpatient"])

            reimbursed = round_to_2(total_cost * rate)

            record = {
                "user_id": user_id,
                "date": visit_date.strftime("%Y-%m-%d %H:%M:%S"),
                "hospital": hospital,
                "department": department,
                "diagnosis": diagnosis,
                "visit_type": visit_type,
                "total_cost": total_cost,
                "reimbursed_amount": reimbursed,
            }

            if visit_type == "住院" and diagnosis in DRG_CODES:
                record["drg_code"] = DRG_CODES[diagnosis]

            records.append(record)

    # 按日期排序
    records.sort(key=lambda x: x["date"])
    return records


def generate_medication_records(user_id: int, profile: dict) -> list:
    """
    生成购药记录
    - 慢性病患者：10-30条，含定期购药模式
    - 健康人群：2-8条，偶发购药
    """
    records = []
    conditions = profile["conditions"]
    insurance_type = profile["insurance_type"]

    date_start = datetime(2022, 1, 1)
    date_end = datetime(2025, 12, 31)

    if conditions:
        # 慢性病患者：10-30条
        # 先生成定期购药（每月/每两月一次），再补充偶发购药
        chronic_meds = []
        for condition in conditions:
            categories = CONDITION_MEDICATION_MAP.get(condition, [])
            for cat in categories:
                if cat in MEDICATIONS:
                    # 每个类别选1-2种常用药
                    meds = random.sample(
                        MEDICATIONS[cat],
                        min(random.randint(1, 2), len(MEDICATIONS[cat]))
                    )
                    chronic_meds.extend(meds)

        # 生成定期购药记录（每1-2个月一次）
        current_date = date_start
        while current_date <= date_end:
            for med in chronic_meds:
                # 每种药每1-2个月购买一次
                if random.random() < 0.7:  # 70%概率该月购买
                    price = round_to_2(random.uniform(*med["price_range"]))
                    quantity = random.randint(1, 3)
                    record = {
                        "user_id": user_id,
                        "date": current_date.strftime("%Y-%m-%d"),
                        "medication_name": med["name"],
                        "category": next(
                            (cat for cat, meds_list in MEDICATIONS.items() if med in meds_list),
                            "其他"
                        ),
                        "quantity": quantity,
                        "unit_price": price,
                        "is_chronic": True,
                    }
                    records.append(record)

            # 推进1-2个月
            advance = random.choice([30, 45, 60])
            current_date += timedelta(days=advance)

        # 补充偶发购药（感冒药、抗生素等）
        num_incidental = random.randint(2, 5)
        for _ in range(num_incidental):
            cat = random.choice(["感冒药", "抗生素", "中成药"])
            med = random.choice(MEDICATIONS[cat])
            price = round_to_2(random.uniform(*med["price_range"]))
            visit_date = random_date(date_start, date_end)
            record = {
                "user_id": user_id,
                "date": visit_date.strftime("%Y-%m-%d"),
                "medication_name": med["name"],
                "category": cat,
                "quantity": random.randint(1, 2),
                "unit_price": price,
                "is_chronic": False,
            }
            records.append(record)
    else:
        # 健康人群：2-8条偶发购药
        num_records = random.randint(2, 8)
        for _ in range(num_records):
            cat = random.choice(["感冒药", "抗生素", "中成药"])
            med = random.choice(MEDICATIONS[cat])
            price = round_to_2(random.uniform(*med["price_range"]))
            visit_date = random_date(date_start, date_end)
            record = {
                "user_id": user_id,
                "date": visit_date.strftime("%Y-%m-%d"),
                "medication_name": med["name"],
                "category": cat,
                "quantity": random.randint(1, 2),
                "unit_price": price,
                "is_chronic": False,
            }
            records.append(record)

    # 按日期排序
    records.sort(key=lambda x: x["date"])
    return records


# ============================================================
# 主函数：生成所有数据
# ============================================================
def generate_all_data():
    """生成全部合成数据并保存为JSON和CSV"""
    print("=" * 60)
    print("MedSignal - 合成数据生成")
    print("=" * 60)

    all_data = {
        "users": [],
        "insurance_records": [],
        "medical_records": [],
        "medication_records": [],
    }

    for idx, profile in enumerate(USER_PROFILES, start=1):
        print(f"\n正在生成第 {idx}/10 个用户数据: {profile['name']}")

        # 用户基本信息
        user = {
            "id": idx,
            "name": profile["name"],
            "age": profile["age"],
            "gender": profile["gender"],
            "city": profile["city"],
            "insurance_type": profile["insurance_type"],
            "employee_status": profile["employee_status"],
            "conditions": profile["conditions"],
        }
        all_data["users"].append(user)

        # 医保缴费记录
        ins_records = generate_insurance_records(idx, profile)
        all_data["insurance_records"].extend(ins_records)
        print(f"  缴费记录: {len(ins_records)} 条")

        # 就诊记录
        med_records = generate_medical_records(idx, profile)
        all_data["medical_records"].extend(med_records)
        print(f"  就诊记录: {len(med_records)} 条")

        # 购药记录
        drug_records = generate_medication_records(idx, profile)
        all_data["medication_records"].extend(drug_records)
        print(f"  购药记录: {len(drug_records)} 条")

    # ============================================================
    # 保存 JSON
    # ============================================================
    json_path = DATA_DIR / "mock_data.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)
    print(f"\n✅ JSON数据已保存: {json_path}")

    # ============================================================
    # 保存 CSV
    # ============================================================
    # users.csv
    users_csv = DATA_DIR / "users.csv"
    with open(users_csv, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "id", "name", "age", "gender", "city",
            "insurance_type", "employee_status", "conditions",
        ])
        writer.writeheader()
        for u in all_data["users"]:
            row = u.copy()
            row["conditions"] = "|".join(row["conditions"])
            writer.writerow(row)
    print(f"✅ 用户CSV已保存: {users_csv}")

    # insurance_records.csv
    ins_csv = DATA_DIR / "insurance_records.csv"
    with open(ins_csv, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "user_id", "year", "month", "base_amount",
            "personal_amount", "company_amount",
        ])
        writer.writeheader()
        writer.writerows(all_data["insurance_records"])
    print(f"✅ 缴费记录CSV已保存: {ins_csv}")

    # medical_records.csv
    med_csv = DATA_DIR / "medical_records.csv"
    with open(med_csv, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "user_id", "date", "hospital", "department",
            "diagnosis", "visit_type", "total_cost",
            "reimbursed_amount", "drg_code",
        ])
        writer.writeheader()
        for r in all_data["medical_records"]:
            row = r.copy()
            if "drg_code" not in row:
                row["drg_code"] = ""
            writer.writerow(row)
    print(f"✅ 就诊记录CSV已保存: {med_csv}")

    # medication_records.csv
    drug_csv = DATA_DIR / "medication_records.csv"
    with open(drug_csv, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "user_id", "date", "medication_name", "category",
            "quantity", "unit_price", "is_chronic",
        ])
        writer.writeheader()
        writer.writerows(all_data["medication_records"])
    print(f"✅ 购药记录CSV已保存: {drug_csv}")

    # ============================================================
    # 统计摘要
    # ============================================================
    print("\n" + "=" * 60)
    print("数据统计摘要")
    print("=" * 60)
    print(f"用户总数: {len(all_data['users'])}")
    print(f"缴费记录总数: {len(all_data['insurance_records'])}")
    print(f"就诊记录总数: {len(all_data['medical_records'])}")
    print(f"购药记录总数: {len(all_data['medication_records'])}")

    # 就诊类型分布
    outpatient = sum(1 for r in all_data["medical_records"] if r["visit_type"] == "门诊")
    inpatient = sum(1 for r in all_data["medical_records"] if r["visit_type"] == "住院")
    print(f"  门诊: {outpatient} 条, 住院: {inpatient} 条")

    # 慢性病用药占比
    chronic_drugs = sum(1 for r in all_data["medication_records"] if r["is_chronic"])
    total_drugs = len(all_data["medication_records"])
    print(f"  慢性病用药: {chronic_drugs}/{total_drugs} ({chronic_drugs/total_drugs*100:.1f}%)")

    # 总费用统计
    total_medical_cost = sum(r["total_cost"] for r in all_data["medical_records"])
    total_reimbursed = sum(r["reimbursed_amount"] for r in all_data["medical_records"])
    print(f"  总医疗费用: ¥{total_medical_cost:,.2f}")
    print(f"  总报销金额: ¥{total_reimbursed:,.2f}")
    print(f"  平均报销比例: {total_reimbursed/total_medical_cost*100:.1f}%")

    print("\n🎉 数据生成完成！")


if __name__ == "__main__":
    generate_all_data()
