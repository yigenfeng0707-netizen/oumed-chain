"""MedSignal - 档案管家 · 数字人体 3D 查看器适配路由

静态查看器 backend/app/static/digital-body/index.html（main.py 挂载于 /digital-body）
的数据契约，复用 routers/body.py 同一套 BodyRecord/BodyDocument 数据，只增不删：

- GET  /api/body-archive/patients                      患者索引（查看器左上角下拉切换）
- GET  /api/body-archive/patients/{user_id}            患者档案（性别 + 记录 + 资料）
- POST /api/body-archive/patients/{user_id}/records    追加一条档案（Skill ingest.py 用）
- POST /api/body-archive/patients/{user_id}/materials  登记资料文件名（不存文件本体）
"""

import io
import json
import re
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from app.database import get_db
from app.models import BodyArchiveFile
from app.services.body.extractor import DISCLAIMER
from app.services.body.taxonomy import LABELS
from app.services.body_archive_dossier import (
    GOVERNMENT_FIELDS,
    ai_csv_bytes,
    build_dossier,
    csv_bytes,
    government_row,
)

router = APIRouter(prefix="/api/body-archive", tags=["档案管家·数字人体"])

_DATE_RE = re.compile(r"^(\d{4})-(\d{1,2})(?:-(\d{1,2}))?$")


def _user_ref(user_id: int) -> str:
    """查看器/前端通用的 'user_001' 形式患者 id。"""
    return f"user_{user_id:03d}"


def _norm_event_date(value: str) -> str:
    """校验并规范化 event_date：允许空串 / YYYY-MM / YYYY-MM-DD。"""
    value = (value or "").strip()
    if not value:
        return ""
    match = _DATE_RE.match(value)
    if not match or not 1 <= int(match.group(2)) <= 12:
        raise HTTPException(status_code=400, detail=f"event_date 格式不合法: {value!r}，应为 YYYY-MM 或 YYYY-MM-DD")
    out = f"{match.group(1)}-{int(match.group(2)):02d}"
    if match.group(3):
        if not 1 <= int(match.group(3)) <= 31:
            raise HTTPException(status_code=400, detail=f"event_date 日不合法: {value!r}")
        out += f"-{int(match.group(3)):02d}"
    return out


class BodyRecordIn(BaseModel):
    organ: str = Field(..., description="器官/部位 key，见 /api/body/organs")
    event_date: str = ""
    source_type: str = "chat"
    source_label: str = "对话输入"
    source_ref: str = ""
    description: str = ""
    raw_excerpt: str = ""


class MaterialIn(BaseModel):
    filename: str = Field(..., min_length=1, max_length=200)
    note: str = Field(default="", max_length=200)


@router.get("/patients")
async def list_patients(db: AsyncSession = Depends(get_db)):
    """患者索引：查看器下拉切换用。"""
    users = await crud.get_users(db, limit=50)
    return {"patients": [{"id": _user_ref(u.id), "name": u.name} for u in users]}


@router.get("/patients/{user_id}")
async def get_patient(user_id: str, db: AsyncSession = Depends(get_db)):
    """患者档案：性别（决定 3D 解剖模型）+ 档案记录 + 已存资料。"""
    user = await crud.get_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail=f"患者不存在: {user_id}")
    records = [crud.body_record_to_dict(r) for r in await crud.get_body_records(db, user_id)]
    materials = [{"filename": d.filename, "note": d.doc_kind or ""} for d in await crud.get_body_documents(db, user_id)]
    uid = user.id
    files = list((await db.execute(select(BodyArchiveFile).where(BodyArchiveFile.user_id == uid))).scalars())
    materials.extend(
        {
            "filename": f.filename,
            "stored_name": f.stored_name,
            "note": f.note,
            "mime_type": f.mime_type,
            "size_bytes": f.size_bytes,
        }
        for f in files
    )
    return {
        "patient_id": _user_ref(user.id),
        "name": user.name,
        "sex": "f" if user.gender in ("女", "female", "F") else "m",
        "records": records,
        "materials": materials,
        "disclaimer": DISCLAIMER,
    }


@router.post("/patients/{user_id}/records")
async def append_record(user_id: str, payload: BodyRecordIn, db: AsyncSession = Depends(get_db)):
    """追加一条档案记录（只增不删）。调用方（Skill/Agent）须保证内容来自用户原文。"""
    user = await crud.get_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail=f"患者不存在: {user_id}")
    if payload.organ not in LABELS:
        raise HTTPException(status_code=400, detail=f"未知部位: {payload.organ}，可选 {list(LABELS)}")
    event_date = _norm_event_date(payload.event_date)
    rows = await crud.create_body_records(
        db,
        user_id,
        [
            {
                "organ": payload.organ,
                "description": payload.description or payload.raw_excerpt,
                "raw_excerpt": payload.raw_excerpt,
                "event_date": event_date,
            }
        ],
        source_type=(payload.source_type or "chat")[:10],
        source_label=(payload.source_label or "对话输入")[:30],
        source_ref=payload.source_ref,
    )
    return {"record": crud.body_record_to_dict(rows[0])}


@router.post("/patients/{user_id}/materials")
async def register_material(user_id: str, payload: MaterialIn, db: AsyncSession = Depends(get_db)):
    """登记资料文件名与备注（只登记元数据，不接收文件本体）。"""
    user = await crud.get_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail=f"患者不存在: {user_id}")
    doc = await crud.create_body_document(
        db,
        user_id,
        payload.filename,
        "",
        (payload.note or "其他")[:30],
        "",
    )
    return {"document": {"id": doc.id, "filename": doc.filename, "note": doc.doc_kind}}


@router.get("/patients/{user_id}/dossier")
async def get_dossier(user_id: str, db: AsyncSession = Depends(get_db)):
    user = await crud.get_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail=f"患者不存在: {user_id}")
    return await build_dossier(db, user)


async def _all_dossiers(db: AsyncSession, requested: str = "") -> list[dict]:
    if requested:
        user = await crud.get_user(db, requested)
        if user is None:
            raise HTTPException(status_code=404, detail=f"患者不存在: {requested}")
        return [await build_dossier(db, user)]
    return [await build_dossier(db, u) for u in await crud.get_users(db, limit=50)]


@router.get("/government-export")
async def government_export(
    patient: str = "", format: str = Query("json", pattern="^(csv|json)$"), db: AsyncSession = Depends(get_db)
):
    dossiers = await _all_dossiers(db, patient)
    rows = [government_row(d) for d in dossiers]
    if format == "csv":
        return Response(
            csv_bytes(rows, GOVERNMENT_FIELDS),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=government-mock.csv"},
        )
    return {"template": "模拟居民健康档案联调表", "patients": len(rows), "rows": rows}


@router.get("/ai-table")
async def ai_table(
    patient: str = "", format: str = Query("json", pattern="^(csv|json)$"), db: AsyncSession = Depends(get_db)
):
    dossiers = await _all_dossiers(db, patient)
    if format == "csv":
        return Response(
            ai_csv_bytes(dossiers),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=ai-organized-table.csv"},
        )
    return {
        "patients": len(dossiers),
        "rows": [
            {"patient_id": d["patient_id"], "archive_id": d["archive_id"], "name": d["name"], **row}
            for d in dossiers
            for row in d["ai_organized_table"]
        ],
    }


@router.get("/patients/{user_id}/files/{stored_name}")
async def get_file(user_id: str, stored_name: str, download: bool = False, db: AsyncSession = Depends(get_db)):
    user = await crud.get_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="患者不存在")
    file = (
        await db.execute(
            select(BodyArchiveFile).where(
                BodyArchiveFile.user_id == user.id, BodyArchiveFile.stored_name == stored_name
            )
        )
    ).scalar_one_or_none()
    if file is None:
        raise HTTPException(status_code=404, detail="附件不存在")
    disposition = "attachment" if download else "inline"
    return Response(
        file.content,
        media_type=file.mime_type,
        headers={
            "Content-Disposition": f'{disposition}; filename="{file.stored_name}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/patients/{user_id}/files")
async def upload_file(
    user_id: str,
    file: UploadFile = File(...),
    note: str = Form(""),
    category: str = Form("用户上传"),
    db: AsyncSession = Depends(get_db),
):
    user = await crud.get_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="患者不存在")
    content = await file.read(25 * 1024 * 1024 + 1)
    if len(content) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="单个文件最多 25 MB")
    allowed = {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/svg+xml",
        "text/csv",
        "application/json",
        "text/plain",
    }
    mime = file.content_type or "application/octet-stream"
    if mime not in allowed:
        raise HTTPException(status_code=400, detail="仅支持 PDF、PNG、JPG、SVG、CSV、JSON、TXT")
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", file.filename or "upload.bin")[:100]
    stored = f"user_{user.id:03d}-{int(__import__('time').time())}-{safe}"
    row = BodyArchiveFile(
        user_id=user.id,
        stored_name=stored,
        filename=(file.filename or safe)[:200],
        mime_type=mime,
        category=category[:40],
        note=note[:200],
        size_bytes=len(content),
        content=content,
    )
    db.add(row)
    await db.commit()
    return {"file": {"stored_name": stored, "filename": row.filename, "size_bytes": len(content)}}


async def _archive_bytes(db: AsyncSession, dossiers: list[dict]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("README.txt", "嘀嗒医合成测试档案包\n仅用于系统联调，不用于诊疗或正式政务报送。\n")
        archive.writestr("government-mock.csv", csv_bytes([government_row(d) for d in dossiers], GOVERNMENT_FIELDS))
        archive.writestr("ai-organized-table.csv", ai_csv_bytes(dossiers))
        public_dir = Path(__file__).resolve().parent.parent / "static" / "digital-body" / "data" / "public-assets"
        if public_dir.is_dir():
            for path in sorted(public_dir.iterdir()):
                if path.is_file():
                    archive.write(path, f"public-reference-assets/{path.name}")
        for d in dossiers:
            archive.writestr(f"patients/{d['patient_id']}/dossier.json", json.dumps(d, ensure_ascii=False, indent=2))
            user = await crud.get_user(db, d["patient_id"])
            files = list(
                (await db.execute(select(BodyArchiveFile).where(BodyArchiveFile.user_id == user.id))).scalars()
            )
            for f in files:
                archive.writestr(f"patients/{d['patient_id']}/original-files/{f.stored_name}", f.content)
    return output.getvalue()


@router.get("/patients/{user_id}/archive.zip")
async def patient_archive(user_id: str, db: AsyncSession = Depends(get_db)):
    dossiers = await _all_dossiers(db, user_id)
    return Response(
        await _archive_bytes(db, dossiers),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{user_id}-health-archive.zip"'},
    )


@router.get("/archive/all.zip")
async def all_archive(db: AsyncSession = Depends(get_db)):
    dossiers = await _all_dossiers(db)
    return Response(
        await _archive_bytes(db, dossiers),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="digital-health-archive-all.zip"'},
    )
