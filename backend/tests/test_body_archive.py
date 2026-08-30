"""数字人体 3D 查看器适配路由（body_archive）测试 — 全部离线，覆盖：

患者索引 / 患者档案（性别映射 + 记录 + 资料） / 追加记录（日期校验 + 未知部位 400）
/ 登记资料 / 患者不存在 404 / 静态查看器挂载
"""

import io
import os
import sys
import zipfile

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base, User  # noqa: E402
from app.services.body_archive_dossier import seed_demo_archive  # noqa: E402


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(
            User(
                id=1, name="张阿姨", age=62, gender="女", city="杭州", insurance_type="职工医保", employee_status="退休"
            )
        )
        session.add(
            User(
                id=2, name="李大爷", age=70, gender="男", city="杭州", insurance_type="居民医保", employee_status="退休"
            )
        )
        await session.commit()
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def client(db):
    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


# ---------------- 患者索引 ----------------


class TestPatientsIndex:
    async def test_list_patients_returns_viewer_ids(self, client):
        resp = await client.get("/api/body-archive/patients")
        assert resp.status_code == 200
        patients = resp.json()["patients"]
        assert patients[0] == {"id": "user_001", "name": "张阿姨"}
        assert patients[1] == {"id": "user_002", "name": "李大爷"}


# ---------------- 患者档案 ----------------


class TestGetPatient:
    async def test_female_patient_profile(self, client):
        resp = await client.get("/api/body-archive/patients/user_001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["patient_id"] == "user_001"
        assert data["name"] == "张阿姨"
        assert data["sex"] == "f"  # '女' → 女性解剖模型
        assert data["records"] == []
        assert "disclaimer" in data

    async def test_male_patient_sex_mapping(self, client):
        resp = await client.get("/api/body-archive/patients/2")
        assert resp.status_code == 200
        assert resp.json()["sex"] == "m"

    async def test_numeric_user_id_accepted(self, client):
        resp = await client.get("/api/body-archive/patients/001")
        assert resp.status_code == 200
        assert resp.json()["patient_id"] == "user_001"

    async def test_unknown_patient_404(self, client):
        resp = await client.get("/api/body-archive/patients/user_999")
        assert resp.status_code == 404


# ---------------- 追加记录 ----------------


class TestAppendRecord:
    async def test_append_record_roundtrip(self, client, db):
        resp = await client.post(
            "/api/body-archive/patients/user_001/records",
            json={
                "organ": "lungs",
                "event_date": "2026-2",
                "description": "肺部小结节",
                "raw_excerpt": "我查出肺部小结节",
            },
        )
        assert resp.status_code == 200
        record = resp.json()["record"]
        assert record["organ"] == "lungs"
        assert record["event_date"] == "2026-02"  # 月份补零
        assert record["source_type"] == "chat"

        # 只增不删：再次 GET 能看到该记录
        data = (await client.get("/api/body-archive/patients/user_001")).json()
        assert len(data["records"]) == 1
        assert data["records"][0]["raw_excerpt"] == "我查出肺部小结节"

    async def test_full_date_normalized(self, client):
        resp = await client.post(
            "/api/body-archive/patients/1/records",
            json={
                "organ": "shoulder_right",
                "event_date": "2026-07-5",
                "raw_excerpt": "右肩疼",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["record"]["event_date"] == "2026-07-05"

    async def test_bad_month_400(self, client):
        resp = await client.post(
            "/api/body-archive/patients/1/records",
            json={
                "organ": "lungs",
                "event_date": "2026-13",
            },
        )
        assert resp.status_code == 400

    async def test_bad_date_format_400(self, client):
        resp = await client.post(
            "/api/body-archive/patients/1/records",
            json={
                "organ": "lungs",
                "event_date": "去年冬天",
            },
        )
        assert resp.status_code == 400

    async def test_unknown_organ_400(self, client):
        resp = await client.post(
            "/api/body-archive/patients/1/records",
            json={
                "organ": "unicorn",
                "raw_excerpt": "x",
            },
        )
        assert resp.status_code == 400
        assert "未知部位" in resp.json()["detail"]

    async def test_empty_date_allowed(self, client):
        resp = await client.post(
            "/api/body-archive/patients/1/records",
            json={
                "organ": "knee_left",
                "event_date": "",
                "raw_excerpt": "左膝不舒服",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["record"]["event_date"] == ""


# ---------------- 登记资料 ----------------


class TestRegisterMaterial:
    async def test_register_material_shows_in_profile(self, client):
        resp = await client.post(
            "/api/body-archive/patients/user_001/materials",
            json={
                "filename": "胸部CT.pdf",
                "note": "复查资料",
            },
        )
        assert resp.status_code == 200
        doc = resp.json()["document"]
        assert doc["filename"] == "胸部CT.pdf"
        assert doc["note"] == "复查资料"

        data = (await client.get("/api/body-archive/patients/user_001")).json()
        assert data["materials"] == [{"filename": "胸部CT.pdf", "note": "复查资料"}]

    async def test_unknown_patient_404(self, client):
        resp = await client.post(
            "/api/body-archive/patients/user_999/materials",
            json={
                "filename": "x.pdf",
            },
        )
        assert resp.status_code == 404


# ---------------- 静态查看器挂载 ----------------


class TestViewerMounted:
    async def test_digital_body_index_served(self, client):
        resp = await client.get("/digital-body/index.html")
        assert resp.status_code == 200
        assert b"apiBase" in resp.content  # 查看器确实会调用 body-archive API


# ---------------- 完整档案 / 数据库附件 / 导出 ----------------


class TestCompleteDossier:
    async def test_seeded_dossier_and_file_preview(self, client, db):
        result = await seed_demo_archive(db)
        assert result["users"] == 2
        assert result["files_added"] == 8

        dossier = (await client.get("/api/body-archive/patients/user_001/dossier")).json()
        assert dossier["archive_id"] == "DH-TEST-2026-0001"
        assert dossier["profile"]["system_conditions"] == ["糖尿病", "高血压"]
        assert {r["organ"] for r in dossier["records"]} == {"pancreas", "heart"}
        assert len(dossier["materials"]) == 4

        material = dossier["materials"][0]
        response = await client.get(f"/api/body-archive/patients/user_001/files/{material['stored_name']}")
        assert response.status_code == 200
        assert len(response.content) == material["size_bytes"]

    async def test_complete_exports_and_zip(self, client, db):
        await seed_demo_archive(db)
        csv_response = await client.get("/api/body-archive/government-export?format=csv")
        assert csv_response.status_code == 200
        assert "档案编号" in csv_response.content.decode("utf-8-sig")

        ai_response = await client.get("/api/body-archive/ai-table?format=json")
        assert ai_response.status_code == 200
        assert ai_response.json()["patients"] == 2

        archive_response = await client.get("/api/body-archive/archive/all.zip")
        assert archive_response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(archive_response.content)) as archive:
            names = archive.namelist()
            assert "patients/user_001/dossier.json" in names
            assert any("patients/user_002/original-files/" in name for name in names)

    async def test_upload_file_is_stored_per_user(self, client):
        response = await client.post(
            "/api/body-archive/patients/user_001/files",
            files={"file": ("note.txt", b"synthetic test", "text/plain")},
            data={"note": "联调上传", "category": "测试资料"},
        )
        assert response.status_code == 200
        stored_name = response.json()["file"]["stored_name"]
        downloaded = await client.get(f"/api/body-archive/patients/user_001/files/{stored_name}?download=true")
        assert downloaded.content == b"synthetic test"
        assert downloaded.headers["content-disposition"].startswith("attachment")
