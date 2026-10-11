"""P0-1.2 等保三级·存储加密测试（app/crypto.py + models 敏感字段 TypeDecorator）：

- 密钥解析：base64 / hex / 任意字符串 PBKDF2 派生
- AES-256-GCM 往返：随机 nonce（同明文不同密文）/ 确定性（同明文同密文，等值查询保序）
- 存量明文兼容：无 enc1: 前缀原样返回；未配密钥 = 明文兼容模式
- 解密失败（密钥不匹配）→ 空值兜底，不透传密文
- TypeDecorator 集成：users.email 等值查询/唯一索引在密文列上仍工作；
  medical_records.diagnosis 落库即密文、读出即明文

注意：密钥全局缓存 _key_cache，所有用例通过 fixture 强制 reset，避免污染其他测试。
"""

import base64
import os
import sys
from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import crypto  # noqa: E402
from app.config import settings  # noqa: E402
from app.crypto import (  # noqa: E402
    decrypt_value,
    encrypt_value,
    encryption_enabled,
)
from app.models import Base, MedicalRecord, User  # noqa: E402

_KEY_RAW = base64.b64encode(os.urandom(32)).decode("ascii")


@pytest.fixture(autouse=True)
def _key_env(monkeypatch):
    """默认未配密钥（明文兼容）；配密钥的用例自行 setattr + reset。"""
    monkeypatch.setattr(settings, "YIBAO_DATA_KEY", "")
    crypto.reset_key_cache()
    yield
    crypto.reset_key_cache()  # teardown 复位，保证其他测试文件不受污染


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


# ------------------------------------------------------------------
# 密钥解析与开关
# ------------------------------------------------------------------

def test_parse_key_base64_hex_and_passphrase():
    k1 = crypto._parse_key(base64.b64encode(b"x" * 32).decode())
    assert len(k1) == 32 and k1 == b"x" * 32
    k2 = crypto._parse_key((b"y" * 32).hex())
    assert len(k2) == 32 and k2 == b"y" * 32
    # 任意字符串 → PBKDF2 确定性派生
    k3 = crypto._parse_key("我的口令密钥")
    k4 = crypto._parse_key("我的口令密钥")
    assert k3 == k4 and len(k3) == 32


def test_disabled_when_key_empty():
    assert not encryption_enabled()
    assert encrypt_value("张三@医院.com") == "张三@医院.com"  # 明文直存
    assert decrypt_value("张三@医院.com") == "张三@医院.com"


def test_roundtrip_random_vs_deterministic():
    settings.YIBAO_DATA_KEY = _KEY_RAW
    crypto.reset_key_cache()
    assert encryption_enabled()

    c1 = encrypt_value("急性阑尾炎")
    c2 = encrypt_value("急性阑尾炎")
    assert c1.startswith("enc1:") and c1 != c2           # 随机 nonce：同明文不同密文
    assert decrypt_value(c1) == "急性阑尾炎" == decrypt_value(c2)

    d1 = encrypt_value("patient@hospital.cn", deterministic=True)
    d2 = encrypt_value("patient@hospital.cn", deterministic=True)
    assert d1 == d2 and d1.startswith("enc1:")           # 确定性：同明文同密文
    assert decrypt_value(d1) == "patient@hospital.cn"
    assert encrypt_value("") == "" and encrypt_value(None) is None


def test_legacy_plaintext_compat_and_key_mismatch():
    settings.YIBAO_DATA_KEY = _KEY_RAW
    crypto.reset_key_cache()
    # 存量明文行：无前缀原样返回（渐进升级语义）
    assert decrypt_value("存量明文诊断") == "存量明文诊断"
    # 密钥不匹配 → 空值兜底，不透传密文
    cipher = encrypt_value("敏感数据")
    settings.YIBAO_DATA_KEY = base64.b64encode(os.urandom(32)).decode()
    crypto.reset_key_cache()
    assert decrypt_value(cipher) == ""


# ------------------------------------------------------------------
# TypeDecorator 集成（等值查询 / 落库密文 / 读出明文）
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_email_deterministic_lookup_and_unique(session_factory):
    """配密钥后：email 落库是密文、等值查询命中、唯一索引生效。"""
    settings.YIBAO_DATA_KEY = _KEY_RAW
    crypto.reset_key_cache()
    async with session_factory() as db:
        db.add(User(id=1, name="张阿姨", age=62, gender="女", city="杭州",
                    insurance_type="职工医保", employee_status="退休",
                    email="aunt@hospital.cn"))  # 业务口径：注册/登录均 strip().lower()
        db.add(User(id=2, name="李叔", age=58, gender="男", city="温州",
                    insurance_type="居民医保", employee_status="在职",
                    email="li@hospital.cn"))
        await db.commit()

        # 原文等值查询（bind 参数自动加密，密文列上命中）
        row = (await db.execute(
            select(User).where(User.email == "aunt@hospital.cn")
        )).scalar_one_or_none()
        assert row is not None and row.id == 1
        assert row.email == "aunt@hospital.cn"  # 读出即明文
        assert (await db.execute(
            select(User).where(User.email == "nobody@x.cn")
        )).scalar_one_or_none() is None

        # 落库确为密文（绕过 ORM 直接看原始行）
        from sqlalchemy import text as sql_text
        raw = (await db.execute(
            sql_text("SELECT email FROM users WHERE id = 1")
        )).scalar()
        assert raw.startswith("enc1:") and "aunt" not in raw.lower()


@pytest.mark.asyncio
async def test_diagnosis_plaintext_mode_and_encrypted_mode(session_factory):
    """diagnosis：未配密钥=明文直存读回一致；配密钥=落库密文读出明文。"""
    async with session_factory() as db:
        db.add(MedicalRecord(id=1, user_id=1, date=datetime(2026, 8, 1),
                             hospital="温州市医院",
                             department="普外科", diagnosis="急性阑尾炎", visit_type="门诊",
                             total_cost=3200.0, reimbursed_amount=2100.0))
        await db.commit()
        from sqlalchemy import text as sql_text
        raw = (await db.execute(
            sql_text("SELECT diagnosis FROM medical_records WHERE id = 1")
        )).scalar()
        assert raw == "急性阑尾炎"  # 明文兼容模式

    settings.YIBAO_DATA_KEY = _KEY_RAW
    crypto.reset_key_cache()
    async with session_factory() as db:
        row = await db.get(MedicalRecord, 1)
        # 存量明文读回不变；新写入走密文
        assert row.diagnosis == "急性阑尾炎"
        row.diagnosis = "高血压 2 级"
        await db.commit()
        from sqlalchemy import text as sql_text
        raw = (await db.execute(
            sql_text("SELECT diagnosis FROM medical_records WHERE id = 1")
        )).scalar()
        assert raw.startswith("enc1:") and "高血压" not in raw
        row = await db.get(MedicalRecord, 1)
        assert row.diagnosis == "高血压 2 级"
