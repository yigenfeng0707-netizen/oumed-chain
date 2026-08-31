"""
瓯医数链 - 鉴权层

设计原则：Demo 友好 + 安全叙事自洽
- 默认开放（无 token 也能访问，保证 Demo 流畅）
- 配置了 API_KEY 环境变量时，要求 X-API-Key 头校验
- 提供 get_current_user 依赖（基于 user_id 的简单会话）
- 邮箱注册登录（auth 路由）：PBKDF2 密码哈希 + HMAC 签名 token

这样"安全守门 Agent"名副其实，路演时可演示"未授权访问被拦截"。
"""

import hashlib
import hmac
import logging
import os
import secrets
import time

from fastapi import Header, HTTPException, status

logger = logging.getLogger(__name__)

# 从环境变量读取 API Key（未配置则不启用鉴权，保证 Demo 流畅）
API_KEY = os.getenv("YIBAO_API_KEY", "")

# 用户 token 有效期（秒）：24h，与前端 localStorage 生命周期对齐
USER_TOKEN_TTL = 86400

# PBKDF2 迭代次数（OWASP 2023 建议 ≥600k，Demo 取平衡值）
_PBKDF2_ITERATIONS = 260_000


def require_api_key(x_api_key: str | None = Header(None, alias="X-API-Key")) -> str:
    """API Key 校验依赖。

    - 未配置 YIBAO_API_KEY 环境变量时：跳过校验（Demo 模式）
    - 配置后：要求请求头 X-API-Key 匹配
    """
    if not API_KEY:
        return "anonymous"  # Demo 模式

    if not x_api_key or x_api_key != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的 API Key，请在请求头携带 X-API-Key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return "authenticated"


def get_current_user(
    user_id: str | None = None,
    x_user_token: str | None = Header(None, alias="X-User-Token"),
) -> dict:
    """获取当前用户（基于 token 的简易会话）。

    Demo 阶段：直接信任 user_id 参数（来自路径）
    生产阶段：解析 JWT token 提取 user_id
    """
    # Demo 模式：返回用户标识
    return {
        "user_id": user_id,
        "authenticated": bool(x_user_token),
        "demo_mode": True,
    }


# ------------------------------------------------------------------
# 密码哈希（PBKDF2-SHA256，标准库实现，无额外依赖）
# ------------------------------------------------------------------

def hash_password(password: str) -> str:
    """哈希密码，返回格式：pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>"""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ITERATIONS
    )
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    """校验密码与存储哈希是否匹配（空哈希/格式非法一律拒绝）"""
    if not stored:
        return False
    try:
        algo, iterations, salt_hex, hash_hex = stored.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


# ------------------------------------------------------------------
# 用户会话 token（HMAC 签名，无状态：uid|exp|sig）
# ------------------------------------------------------------------

def _session_secret() -> str:
    return os.getenv("YIBAO_SESSION_SECRET", "demo")


def issue_user_token(user_id: int, ttl: int = USER_TOKEN_TTL) -> str:
    """签发用户 token：payload=uid|exp，sig=HMAC-SHA256(payload)[:32]。"""
    exp = int(time.time()) + ttl
    payload = f"{user_id}|{exp}"
    sig = hmac.new(_session_secret().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    return f"{payload}|{sig}"


def parse_user_token(token: str | None) -> int | None:
    """解析并验证 token，返回 user_id；无效/过期返回 None。"""
    if not token:
        return None
    parts = token.split("|")
    if len(parts) != 3:
        return None
    uid_s, exp_s, sig = parts
    payload = f"{uid_s}|{exp_s}"
    expected = hmac.new(_session_secret().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        if int(exp_s) < time.time():
            return None  # 已过期
        return int(uid_s)
    except ValueError:
        return None


# ------------------------------------------------------------------
# 兼容旧接口（main.py Demo 登录等历史调用）
# ------------------------------------------------------------------

def generate_session_token(user_id: str) -> str:
    """生成会话 token（简易版：user_id + 时间戳的哈希）"""
    raw = f"{user_id}|{time.time()}|{_session_secret()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def verify_session_token(token: str) -> bool:
    """验证会话 token（Demo 阶段：非空即通过）"""
    return bool(token) and len(token) >= 16
