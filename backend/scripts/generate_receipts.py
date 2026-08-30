#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MedSignal - 医疗发票图片生成脚本

使用 Pillow 生成3张模拟中国医疗收费票据图片，用于OCR演示。
每张发票包含：医院名称、患者姓名、日期、费用明细、总金额、报销金额。

用法：
    python generate_receipts.py
"""

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ============================================================
# 路径配置
# ============================================================
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RECEIPT_DIR = PROJECT_ROOT / "data" / "receipts"
RECEIPT_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================
# 字体配置
# ============================================================
# Windows 中文字体路径
FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",      # 微软雅黑
    "C:/Windows/Fonts/simhei.ttf",     # 黑体
    "C:/Windows/Fonts/simsun.ttc",     # 宋体
    "C:/Windows/Fonts/simfang.ttf",    # 仿宋
]


def find_font(size: int = 20) -> ImageFont.FreeTypeFont:
    """查找可用的中文字体"""
    for font_path in FONT_CANDIDATES:
        if os.path.exists(font_path):
            try:
                return ImageFont.truetype(font_path, size)
            except Exception:
                continue
    # 回退到默认字体
    print("⚠️ 未找到中文字体，使用默认字体（中文可能显示为方框）")
    return ImageFont.load_default()


# ============================================================
# 发票模板数据
# ============================================================
RECEIPT_TEMPLATES = [
    {
        "filename": "receipt_outpatient_张阿姨.png",
        "title": "浙江省医疗门诊收费票据",
        "hospital": "浙江省人民医院",
        "patient": "张阿姨",
        "insurance_type": "职工医保",
        "date": "2025-03-15",
        "receipt_no": "No.3201062025031500123",
        "items": [
            ("挂号费", 25.00),
            ("诊查费", 35.00),
            ("检验费-血常规", 30.00),
            ("检验费-糖化血红蛋白", 85.00),
            ("检验费-空腹血糖", 20.00),
            ("药品费-二甲双胍片", 38.50),
            ("药品费-氨氯地平片", 28.00),
            ("药品费-复方丹参滴丸", 32.00),
        ],
        "total": 293.50,
        "reimbursed": 235.00,
        "self_pay": 58.50,
    },
    {
        "filename": "receipt_inpatient_李大爷.png",
        "title": "浙江省医疗住院收费票据",
        "hospital": "浙江大学医学院附属第一医院",
        "patient": "李大爷",
        "insurance_type": "居民医保",
        "date": "2025-01-20",
        "receipt_no": "No.3205052025012000045",
        "items": [
            ("床位费(7天)", 1050.00),
            ("诊查费", 280.00),
            ("检查费-心电图", 60.00),
            ("检查费-心脏彩超", 320.00),
            ("检查费-冠脉CT", 1580.00),
            ("化验费-血常规", 45.00),
            ("化验费-肝肾功能", 180.00),
            ("化验费-心肌酶谱", 120.00),
            ("手术费-冠脉造影术", 8500.00),
            ("麻醉费", 1200.00),
            ("材料费-支架", 12000.00),
            ("材料费-导管", 3500.00),
            ("药品费-氯吡格雷片", 450.00),
            ("药品费-阿托伐他汀钙片", 280.00),
            ("药品费-阿司匹林肠溶片", 22.00),
            ("护理费", 350.00),
        ],
        "total": 29937.00,
        "reimbursed": 17962.20,
        "self_pay": 11974.80,
    },
    {
        "filename": "receipt_outpatient_赵女士.png",
        "title": "浙江省医疗门诊收费票据",
        "hospital": "宁波市第一医院",
        "patient": "赵女士",
        "insurance_type": "职工医保",
        "date": "2025-05-08",
        "receipt_no": "No.3202132025050800078",
        "items": [
            ("挂号费", 25.00),
            ("诊查费", 35.00),
            ("检查费-甲状腺彩超", 180.00),
            ("检验费-甲功五项", 260.00),
            ("检验费-血常规", 30.00),
            ("药品费-左甲状腺素钠片", 42.00),
        ],
        "total": 572.00,
        "reimbursed": 458.00,
        "self_pay": 114.00,
    },
]


def draw_receipt(template: dict) -> Image.Image:
    """
    根据模板绘制一张医疗收费票据图片

    票据布局：
    ┌──────────────────────────────────┐
    │          [票据标题]               │
    │     [医院名称]                    │
    │  票据编号: xxx    日期: xxx       │
    │  姓名: xxx       医保类型: xxx   │
    │──────────────────────────────────│
    │  项目           金额             │
    │  ─────────────────────────────── │
    │  挂号费         ¥25.00          │
    │  诊查费         ¥35.00          │
    │  ...                            │
    │──────────────────────────────────│
    │  合计:          ¥293.50         │
    │  医保报销:      ¥235.00         │
    │  个人自付:      ¥58.50          │
    │──────────────────────────────────│
    │  收款单位(盖章):                 │
    └──────────────────────────────────┘
    """
    # 画布尺寸
    width = 800
    line_height = 36
    margin_top = 40
    margin_x = 50

    # 计算高度
    num_items = len(template["items"])
    total_lines = 8 + num_items + 6  # 头部 + 明细 + 底部
    height = margin_top * 2 + total_lines * line_height + 60

    # 创建白色画布
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)

    # 加载字体
    font_title = find_font(28)
    font_subtitle = find_font(20)
    font_body = find_font(18)
    font_small = find_font(14)

    y = margin_top

    # ---- 标题 ----
    title = template["title"]
    bbox = draw.textbbox((0, 0), title, font=font_title)
    title_width = bbox[2] - bbox[0]
    draw.text(((width - title_width) / 2, y), title, fill="#CC0000", font=font_title)
    y += line_height + 10

    # ---- 医院名称 ----
    hospital = template["hospital"]
    bbox = draw.textbbox((0, 0), hospital, font=font_subtitle)
    hosp_width = bbox[2] - bbox[0]
    draw.text(((width - hosp_width) / 2, y), hospital, fill="black", font=font_subtitle)
    y += line_height + 5

    # ---- 分隔线 ----
    draw.line([(margin_x, y), (width - margin_x, y)], fill="#333333", width=2)
    y += 15

    # ---- 票据编号 & 日期 ----
    draw.text(
        (margin_x, y),
        f"票据编号: {template['receipt_no']}",
        fill="#333333",
        font=font_body,
    )
    draw.text(
        (width - margin_x - 200, y),
        f"日期: {template['date']}",
        fill="#333333",
        font=font_body,
    )
    y += line_height

    # ---- 姓名 & 医保类型 ----
    draw.text(
        (margin_x, y),
        f"姓名: {template['patient']}",
        fill="black",
        font=font_body,
    )
    draw.text(
        (width - margin_x - 200, y),
        f"医保类型: {template['insurance_type']}",
        fill="black",
        font=font_body,
    )
    y += line_height + 5

    # ---- 分隔线 ----
    draw.line([(margin_x, y), (width - margin_x, y)], fill="#333333", width=1)
    y += 10

    # ---- 表头 ----
    draw.text((margin_x, y), "项目", fill="#333333", font=font_body)
    draw.text((width - margin_x - 150, y), "金额(元)", fill="#333333", font=font_body)
    y += line_height

    # ---- 虚线 ----
    for x_pos in range(margin_x, width - margin_x, 8):
        draw.line([(x_pos, y), (x_pos + 4, y)], fill="#999999", width=1)
    y += 8

    # ---- 明细项 ----
    for item_name, item_price in template["items"]:
        draw.text((margin_x + 10, y), item_name, fill="black", font=font_body)
        price_str = f"¥{item_price:,.2f}"
        bbox = draw.textbbox((0, 0), price_str, font=font_body)
        price_width = bbox[2] - bbox[0]
        draw.text(
            (width - margin_x - price_width - 10, y),
            price_str,
            fill="black",
            font=font_body,
        )
        y += line_height

    # ---- 分隔线 ----
    y += 5
    draw.line([(margin_x, y), (width - margin_x, y)], fill="#333333", width=2)
    y += 15

    # ---- 合计 ----
    draw.text((margin_x, y), "合计", fill="black", font=font_subtitle)
    total_str = f"¥{template['total']:,.2f}"
    bbox = draw.textbbox((0, 0), total_str, font=font_subtitle)
    total_width = bbox[2] - bbox[0]
    draw.text(
        (width - margin_x - total_width - 10, y),
        total_str,
        fill="#CC0000",
        font=font_subtitle,
    )
    y += line_height + 5

    # ---- 医保报销 ----
    draw.text((margin_x, y), "医保报销", fill="black", font=font_body)
    reimb_str = f"¥{template['reimbursed']:,.2f}"
    bbox = draw.textbbox((0, 0), reimb_str, font=font_body)
    reimb_width = bbox[2] - bbox[0]
    draw.text(
        (width - margin_x - reimb_width - 10, y),
        reimb_str,
        fill="#006600",
        font=font_body,
    )
    y += line_height

    # ---- 个人自付 ----
    draw.text((margin_x, y), "个人自付", fill="black", font=font_body)
    self_str = f"¥{template['self_pay']:,.2f}"
    bbox = draw.textbbox((0, 0), self_str, font=font_body)
    self_width = bbox[2] - bbox[0]
    draw.text(
        (width - margin_x - self_width - 10, y),
        self_str,
        fill="#CC0000",
        font=font_body,
    )
    y += line_height + 10

    # ---- 分隔线 ----
    draw.line([(margin_x, y), (width - margin_x, y)], fill="#333333", width=1)
    y += 15

    # ---- 收款单位 ----
    draw.text(
        (margin_x, y),
        f"收款单位(盖章): {template['hospital']}",
        fill="#666666",
        font=font_small,
    )
    y += line_height - 5

    # ---- 审核人 & 收款人 ----
    draw.text(
        (margin_x, y),
        "审核人: 王某某",
        fill="#666666",
        font=font_small,
    )
    draw.text(
        (width - margin_x - 150, y),
        "收款人: 李某某",
        fill="#666666",
        font=font_small,
    )

    # ---- 外边框 ----
    draw.rectangle(
        [(2, 2), (width - 3, height - 3)],
        outline="#CC0000",
        width=3,
    )

    return img


def generate_all_receipts():
    """生成所有发票图片"""
    print("=" * 60)
    print("MedSignal - 医疗发票图片生成")
    print("=" * 60)

    for i, template in enumerate(RECEIPT_TEMPLATES, 1):
        print(f"\n正在生成第 {i}/{len(RECEIPT_TEMPLATES)} 张发票: {template['filename']}")

        img = draw_receipt(template)
        save_path = RECEIPT_DIR / template["filename"]
        img.save(save_path, "PNG")
        print(f"  ✅ 已保存: {save_path}")
        print(f"     尺寸: {img.size[0]}x{img.size[1]}")
        print(f"     患者: {template['patient']}")
        print(f"     医院: {template['hospital']}")
        print(f"     总金额: ¥{template['total']:,.2f}")
        print(f"     报销金额: ¥{template['reimbursed']:,.2f}")

    print("\n" + "=" * 60)
    print(f"🎉 发票图片生成完成！保存目录: {RECEIPT_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    generate_all_receipts()
