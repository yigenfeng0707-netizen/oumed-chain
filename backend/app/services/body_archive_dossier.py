"""数字人体完整档案归集、测试数据播种与联调导出。

数据库是患者数据唯一运行时来源；本模块仅按已有用户画像确定性生成合成测试数据，
不联网、不诊断，也不修改或删除已经归档的记录。
"""

from __future__ import annotations

import csv
import io
import json
import math
from datetime import UTC, datetime

from PIL import Image, ImageDraw
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BodyArchiveFile,
    BodyRecord,
    EEGRecord,
    MedicationRecord,
    User,
)
from app.services.body.taxonomy import label_of

CONDITIONS = {
    1: ["糖尿病", "高血压"],
    2: ["冠心病"],
    3: [],
    4: ["甲状腺结节"],
    5: [],
    6: ["糖尿病", "骨质疏松"],
    7: ["胃病"],
    8: [],
    9: ["高血压", "关节炎", "白内障"],
    10: ["腰椎间盘突出"],
}
CONDITION_ORGAN = {
    "糖尿病": "pancreas",
    "高血压": "heart",
    "冠心病": "heart",
    "甲状腺结节": "neck",
    "骨质疏松": "spine",
    "胃病": "stomach",
    "关节炎": "knee",
    "白内障": "eyes",
    "腰椎间盘突出": "spine",
}
GOVERNMENT_FIELDS = [
    "档案编号",
    "系统患者ID",
    "数据标记",
    "姓名",
    "性别代码",
    "出生日期",
    "年龄",
    "城市",
    "参保类型",
    "就业状态",
    "主系统疾病标签",
    "联系电话",
    "现住址",
    "建档日期",
    "管理机构",
    "既往史",
    "体检日期",
    "身高cm",
    "体重kg",
    "BMI",
    "血压mmHg",
    "当前用药",
    "病史记录数",
    "检验记录数",
    "脑电记录数",
    "原始附件数",
    "报送模板",
    "报送状态",
]


def patient_ref(user_id: int) -> str:
    return f"user_{user_id:03d}"


def _birth_date(user: User) -> str:
    return f"{2026 - user.age:04d}-{(user.id * 2) % 12 + 1:02d}-{(user.id * 3) % 25 + 1:02d}"


def _exam(user: User, conditions: list[str]) -> dict:
    weight = round(52 + user.id * 1.8 + (7 if user.gender == "男" else 0), 1)
    height = 158 + (user.id % 5) * 2 + (8 if user.gender == "男" else 0)
    bmi = round(weight / ((height / 100) ** 2), 1)
    systolic = 118 + (10 if "高血压" in conditions else 0) + user.id % 5
    return {
        "exam_date": f"2026-08-{12 + user.id:02d}",
        "height_cm": height,
        "weight_kg": weight,
        "bmi": bmi,
        "waist_cm": round(70 + user.id * 1.2, 1),
        "blood_pressure": f"{systolic}/{74 + user.id % 8}",
        "fasting_glucose": 7.1 if "糖尿病" in conditions else round(4.8 + user.id * 0.04, 1),
    }


def _labs(user: User, conditions: list[str]) -> list[dict]:
    rows = [
        {
            "date": f"2026-08-{12 + user.id:02d}",
            "item": "血红蛋白",
            "value": 132 + user.id,
            "unit": "g/L",
            "reference": "测试值",
        },
        {
            "date": f"2026-08-{12 + user.id:02d}",
            "item": "空腹血糖",
            "value": 7.1 if "糖尿病" in conditions else round(4.8 + user.id * 0.04, 1),
            "unit": "mmol/L",
            "reference": "测试值",
        },
    ]
    if conditions:
        rows.append(
            {
                "date": f"2026-08-{12 + user.id:02d}",
                "item": "总胆固醇",
                "value": round(4.1 + user.id * 0.05, 1),
                "unit": "mmol/L",
                "reference": "测试值",
            }
        )
    return rows


def _ai_table(d: dict) -> list[dict]:
    rows: list[dict] = []

    def add(category, item, value, date="", source="患者结构化档案", status="已归档"):
        rows.append(
            {"category": category, "item": item, "value": str(value), "date": date, "source": source, "status": status}
        )

    add("身份", "档案编号", d["archive_id"], d["profile"]["registered_at"])
    add("主系统同步", "系统用户ID", d["patient_id"], d["source_system"]["synced_at"], "oumed-chain")
    add("基本信息", "参保与就业", f"{d['profile']['insurance_type']} / {d['profile']['employee_status']}")
    for x in d["lab_results"]:
        add("检验", x["item"], f"{x['value']} {x['unit']}", x["date"], "数据库检验测试数据")
    for x in d["medications"]:
        add("用药", x["name"], x["dose"], x["date"], "数据库购药记录")
    for x in d["eeg_sessions"]:
        add("脑电", "采集会话", x["summary"], x["recorded_at"], "数据库EEG记录")
    for x in d["records"]:
        add("3D病史", label_of(x["organ"]), x["description"], x["event_date"], x["source_ref"])
    for x in d["materials"]:
        add("原始资料", x["category"], x["filename"], x["uploaded_at"], "数据库附件")
    return rows


async def build_dossier(db: AsyncSession, user: User) -> dict:
    uid = user.id
    conditions = CONDITIONS.get(uid, [])
    records = list(
        (
            await db.execute(select(BodyRecord).where(BodyRecord.user_id == uid).order_by(BodyRecord.event_date.desc()))
        ).scalars()
    )
    meds = list(
        (
            await db.execute(
                select(MedicationRecord)
                .where(MedicationRecord.user_id == uid)
                .order_by(MedicationRecord.date.desc())
                .limit(6)
            )
        ).scalars()
    )
    eegs = list(
        (
            await db.execute(select(EEGRecord).where(EEGRecord.user_id == uid).order_by(EEGRecord.recorded_at.desc()))
        ).scalars()
    )
    files = list(
        (
            await db.execute(select(BodyArchiveFile).where(BodyArchiveFile.user_id == uid).order_by(BodyArchiveFile.id))
        ).scalars()
    )
    profile = {
        "birth_date": _birth_date(user),
        "id_type": "测试证件",
        "id_number": f"TEST-{uid:04d}",
        "city": user.city,
        "insurance_type": user.insurance_type,
        "employee_status": user.employee_status,
        "system_conditions": conditions,
        "phone": f"1380000{uid:04d}",
        "address": f"{user.city}测试地址{uid}号",
        "registered_at": "2026-01-14",
        "managing_org": "嘀嗒医测试健康档案中心",
    }
    materials = [
        {
            "filename": f.filename,
            "stored_name": f.stored_name,
            "mime_type": f.mime_type,
            "size_bytes": f.size_bytes,
            "category": f.category,
            "note": f.note,
            "uploaded_at": f.uploaded_at.isoformat(timespec="minutes"),
            "previewable": True,
            "data_source": "database_blob",
        }
        for f in files
    ]
    dossier = {
        "patient_id": patient_ref(uid),
        "archive_id": f"DH-TEST-2026-{uid:04d}",
        "data_classification": "synthetic_test_data",
        "name": user.name,
        "sex": "f" if user.gender == "女" else "m",
        "age": user.age,
        "source_system": {
            "system": "oumed-chain",
            "system_user_id": patient_ref(uid),
            "synced_at": "2026-08-28 14:00",
        },
        "data_lineage": ["主系统用户", "一人一档", "3D病史", "多模态数据", "AI归整", "CSV/JSON/ZIP导出"],
        "profile": profile,
        "health_summary": {
            "past_history": conditions,
            "allergies": ["未记录"],
            "family_history": ["测试数据未记录"],
            "risk_tags": conditions,
        },
        "lifestyle": {
            "smoking": "不吸烟",
            "alcohol": "偶尔",
            "exercise": "每周3次",
            "diet": "一般",
            "sleep": "约7小时",
        },
        "latest_exam": _exam(user, conditions),
        "lab_results": _labs(user, conditions),
        "medications": [
            {"name": m.medication_name, "dose": f"{m.quantity}盒", "date": m.date.date().isoformat(), "status": "在档"}
            for m in meds
        ],
        "eeg_sessions": [
            {
                "session_id": e.session_id,
                "recorded_at": e.recorded_at.isoformat(timespec="minutes"),
                "duration_seconds": e.duration_seconds,
                "sampling_rate_hz": 256,
                "summary": e.summary or e.mental_state_label,
                "metrics": json.loads(e.metrics),
            }
            for e in eegs
        ],
        "followups": [{"date": "2026-08-25", "type": "测试随访", "result": "资料已归集", "status": "已完成"}],
        "records": [
            {
                "id": r.id,
                "organ": r.organ,
                "event_date": r.event_date,
                "source_type": r.source_type,
                "source_label": r.source_label,
                "source_ref": r.source_ref,
                "description": r.description,
                "raw_excerpt": r.raw_excerpt,
                "created_at": r.created_at.isoformat(timespec="minutes"),
            }
            for r in records
        ],
        "materials": materials,
        "submission": {"template": "居民健康档案模拟联调表", "status": "可导出（未接正式政务接口）"},
        "ai_summary": {
            "title": f"{user.name}全量档案归整",
            "overview": "已按档案号归集主系统、3D病史、检验、用药、脑电和原始附件。",
            "disclaimer": "合成测试数据；AI仅归整，不作诊断。",
        },
        "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    dossier["ai_organized_table"] = _ai_table(dossier)
    dossier["completeness"] = {
        "ready_for_mock_export": all([records, eegs, files]),
        "missing_sections": [],
        "record_count": len(records),
        "lab_count": len(dossier["lab_results"]),
        "followup_count": 1,
    }
    dossier["integration_coverage"] = {
        "profile": True,
        "records": bool(records),
        "labs": True,
        "eeg": bool(eegs),
        "files": bool(files),
        "ai_table": True,
        "mock_export": dossier["completeness"]["ready_for_mock_export"],
    }
    return dossier


def government_row(d: dict) -> dict:
    p, e = d["profile"], d["latest_exam"]
    return {
        "档案编号": d["archive_id"],
        "系统患者ID": d["patient_id"],
        "数据标记": d["data_classification"],
        "姓名": d["name"],
        "性别代码": "2" if d["sex"] == "f" else "1",
        "出生日期": p["birth_date"],
        "年龄": d["age"],
        "城市": p["city"],
        "参保类型": p["insurance_type"],
        "就业状态": p["employee_status"],
        "主系统疾病标签": "；".join(p["system_conditions"]),
        "联系电话": p["phone"],
        "现住址": p["address"],
        "建档日期": p["registered_at"],
        "管理机构": p["managing_org"],
        "既往史": "；".join(d["health_summary"]["past_history"]),
        "体检日期": e["exam_date"],
        "身高cm": e["height_cm"],
        "体重kg": e["weight_kg"],
        "BMI": e["bmi"],
        "血压mmHg": e["blood_pressure"],
        "当前用药": "；".join(x["name"] for x in d["medications"]),
        "病史记录数": len(d["records"]),
        "检验记录数": len(d["lab_results"]),
        "脑电记录数": len(d["eeg_sessions"]),
        "原始附件数": len(d["materials"]),
        "报送模板": d["submission"]["template"],
        "报送状态": d["submission"]["status"],
    }


def csv_bytes(rows: list[dict], fields: list[str]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return ("\ufeff" + stream.getvalue()).encode("utf-8")


def ai_csv_bytes(dossiers: list[dict]) -> bytes:
    fields = ["档案编号", "系统患者ID", "姓名", "分类", "项目", "值", "日期", "来源", "状态"]
    rows = [
        {
            "档案编号": d["archive_id"],
            "系统患者ID": d["patient_id"],
            "姓名": d["name"],
            **{
                "分类": x["category"],
                "项目": x["item"],
                "值": x["value"],
                "日期": x["date"],
                "来源": x["source"],
                "状态": x["status"],
            },
        }
        for d in dossiers
        for x in d["ai_organized_table"]
    ]
    return csv_bytes(rows, fields)


def _eeg_csv(uid: int) -> bytes:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["time_s", "Fp1", "Fp2", "C3", "C4"])
    for i in range(256):
        t = i / 64
        w.writerow([f"{t:.4f}"] + [f"{math.sin(t * (7 + c) * math.pi * 2 + uid):.5f}" for c in range(4)])
    return ("\ufeff" + out.getvalue()).encode("utf-8")


def _wave_png(uid: int) -> bytes:
    image = Image.new("RGB", (960, 360), "white")
    draw = ImageDraw.Draw(image)
    draw.text((28, 20), f"Synthetic EEG preview - {patient_ref(uid)}", fill=(20, 61, 91))
    colors = [(0, 145, 190), (50, 120, 220), (40, 180, 135), (230, 125, 55)]
    for c in range(4):
        y0 = 85 + c * 70
        pts = [(x, y0 + int(24 * math.sin((x / 960 * 9 + c + uid) * math.pi * 2))) for x in range(30, 930)]
        draw.line(pts, fill=colors[c], width=2)
        draw.text((8, y0 - 8), f"Ch{c + 1}", fill=colors[c])
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return buf.getvalue()


def _pdf(uid: int) -> bytes:
    text = f"Synthetic health archive report - {patient_ref(uid)} - TEST DATA ONLY"
    stream = f"BT /F1 14 Tf 60 760 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n",
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n",
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>endobj\n",
        b"4 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n",
        b"5 0 obj<</Length " + str(len(stream)).encode() + b">>stream\n" + stream + b"\nendstream endobj\n",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(out))
        out.extend(obj)
    xref = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        out.extend(f"{offset:010d} 00000 n \n".encode())
    out.extend(f"trailer<</Size {len(objects) + 1}/Root 1 0 R>>\nstartxref\n{xref}\n%%EOF".encode())
    return bytes(out)


async def seed_demo_archive(db: AsyncSession) -> dict:
    users = list((await db.execute(select(User).order_by(User.id))).scalars())
    added_records = added_files = 0
    for user in users:
        existing_records = (
            await db.execute(select(BodyRecord.id).where(BodyRecord.user_id == user.id).limit(1))
        ).first()
        if not existing_records:
            conditions = CONDITIONS.get(user.id, []) or ["年度健康体检"]
            for i, condition in enumerate(conditions):
                db.add(
                    BodyRecord(
                        user_id=user.id,
                        organ=CONDITION_ORGAN.get(condition, "chest"),
                        description=f"主系统测试档案记录：{condition}",
                        raw_excerpt=condition,
                        event_date=f"2026-{max(1, 8 - i):02d}",
                        source_type="upload",
                        source_label="主系统记录",
                        source_ref="oumed-chain",
                        batch_id="synthetic-demo-2026",
                    )
                )
                added_records += 1
        existing_files = (
            await db.execute(select(BodyArchiveFile.id).where(BodyArchiveFile.user_id == user.id).limit(1))
        ).first()
        if not existing_files:
            blobs = [
                ("eeg-waveform.csv", "脑电原始数据.csv", "text/csv", "脑电", _eeg_csv(user.id)),
                ("eeg-preview.png", "脑电波形预览.png", "image/png", "脑电", _wave_png(user.id)),
                ("archive-report.pdf", "健康档案报告.pdf", "application/pdf", "报告", _pdf(user.id)),
                (
                    "source-profile.json",
                    "主系统原始数据.json",
                    "application/json",
                    "主系统",
                    json.dumps(
                        {
                            "user_id": patient_ref(user.id),
                            "name": user.name,
                            "data_classification": "synthetic_test_data",
                        },
                        ensure_ascii=False,
                        indent=2,
                    ).encode(),
                ),
            ]
            for prefix, filename, mime, category, content in blobs:
                db.add(
                    BodyArchiveFile(
                        user_id=user.id,
                        stored_name=f"{patient_ref(user.id)}-{prefix}",
                        filename=filename,
                        mime_type=mime,
                        category=category,
                        note="合成测试原始资料",
                        size_bytes=len(content),
                        content=content,
                    )
                )
                added_files += 1
    await db.commit()
    return {"users": len(users), "records_added": added_records, "files_added": added_files}
