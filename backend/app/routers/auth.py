"""用户认证 API（邮箱注册/登录）。

提供：
- POST /api/auth/register  邮箱注册（邮箱 + 密码，姓名可选）→ 创建用户并返回 token
- POST /api/auth/login     邮箱登录 → 校验密码，返回 token + 用户画像
- GET  /api/auth/me        携带 X-User-Token 获取当前登录用户画像

设计（与项目"Demo 友好 + 降级"原则一致）：
- 演示用户（user-switcher 切换的 10 个画像）无邮箱/密码，不受影响；
- 注册用户走 users 表同一行（email/password_hash 可空），全站数据联动；
- token 无状态（HMAC 签名，见 app/auth.py），重启后仍有效，换 YIBAO_SESSION_SECRET 即全量失效。
"""

import logging
import re

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from app.auth import (
    USER_TOKEN_TTL,
    hash_password,
    issue_user_token,
    parse_user_token,
    verify_password,
)
from app.database import get_db
from app.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["用户认证"])

# 简单邮箱格式校验（避免引入 email-validator 依赖）
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str = ""

    @field_validator("email")
    @classmethod
    def _check_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL_RE.match(v):
            raise ValueError("邮箱格式不正确")
        return v

    @field_validator("password")
    @classmethod
    def _check_password(cls, v: str) -> str:
        if len(v) < 8 or len(v) > 64:
            raise ValueError("密码长度需为 8-64 位")
        return v

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        return v.strip()[:50]


class LoginRequest(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _check_email(cls, v: str) -> str:
        return v.strip().lower()


def _user_payload(user: User) -> dict:
    """与 /api/users 的用户画像结构对齐，前端可直接切换。"""
    return {
        "id": user.id,
        "public_id": f"user_{user.id:03d}",
        "name": user.name,
        "email": user.email,
        "age": user.age,
        "gender": user.gender,
        "city": user.city,
        "insurance_type": user.insurance_type,
        "employee_status": user.employee_status,
        "conditions": [],
        "registered": bool(user.email and user.password_hash),
    }


async def _get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def _auth_response(db: AsyncSession, user: User) -> dict:
    return {
        "token": issue_user_token(user.id),
        "user": _user_payload(user),
        "expires_in": USER_TOKEN_TTL,
    }


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """邮箱注册：创建用户（演示字段给中性默认值，登录后可在档案中完善）。"""
    if await _get_user_by_email(db, payload.email):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该邮箱已注册，请直接登录")

    user = await crud.create_user(
        db,
        name=payload.name or payload.email.split("@", 1)[0],
        age=0,
        gender="未填写",
        city="未填写",
        insurance_type="未参保",
        employee_status="未填写",
        email=payload.email,
        password_hash=hash_password(payload.password),
    )
    logger.info("新用户注册: %s (uid=%s)", payload.email, user.id)
    return await _auth_response(db, user)


@router.post("/login")
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    """邮箱登录：校验密码（PBKDF2），签发 HMAC token。"""
    user = await _get_user_by_email(db, payload.email)
    # 统一返回"邮箱或密码错误"，不区分账号不存在/密码错误（防枚举）
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="邮箱或密码错误")
    return await _auth_response(db, user)


@router.get("/me")
async def me(
    x_user_token: str | None = Header(None, alias="X-User-Token"),
    db: AsyncSession = Depends(get_db),
):
    """获取当前登录用户画像（X-User-Token 校验）。"""
    user_id = parse_user_token(x_user_token)
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录或登录已过期")
    user = await crud.get_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
    return {"user": _user_payload(user)}
