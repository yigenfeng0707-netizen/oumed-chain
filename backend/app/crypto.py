"""敏感字段存储加密（P0-1.2 等保三级：存储加密·敏感字段 AES）。

设计（双模 + 存量兼容 + 零迁移）：
- 算法：AES-256-GCM（cryptography 库，环境已随传递依赖安装）——
  12 字节随机 nonce + 认证标签，密文格式 `enc1:<b64(nonce)>:<b64(ct|tag)>`；
- 密钥：YIBAO_DATA_KEY（32 字节，base64 或 hex）。留空 = 明文兼容模式
  （写入原样、读取原样），供演示/开发零配置；生产必须配置密钥；
- 确定性模式：nonce = HMAC-SHA256(key, 明文)[:12]，同明文同密文——
  用于需要 SQL 等值查询/唯一索引的字段（users.email），代价是泄露相等性；
- 存量兼容：解密时无 `enc1:` 前缀的值原样返回（历史明文行可正常读取，
  下次写回时自动加密），实现"写入即加密、读取透明、存量渐进升级"；
- SQLAlchemy TypeDecorator 透明加解密：模型字段换类型即可，SQLite 列
  DDL 不变（VARCHAR/TEXT），无需迁移脚本；bind 参数在 SQL 端已是密文，
  确定性字段查询 `User.email == "x"` 自动加密后比较，等值查询/唯一索引有效。

密文长度：明文 n 字节 → 密文约 4n/3+30（base64+nonce+tag），列长度已按 500 放宽
（SQLite 不强制；迁移 PostgreSQL 时列宽已预留）。
"""

import base64
import hashlib
import hmac as hmac_mod
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import String, Text
from sqlalchemy.types import TypeDecorator

from app.config import settings

# 密文版本前缀（升级算法时递增，解密按前缀分流）
_PREFIX = "enc1:"

# 已解析密钥缓存（None=未解析，b""=未配置密钥，bytes=可用密钥）
_key_cache: bytes | None = None


def _parse_key(raw: str) -> bytes:
    """解析 32 字节密钥：优先 base64（44 字符），其次 hex（64 字符），再退 UTF-8 原文派生。"""
    raw = raw.strip()
    if not raw:
        return b""
    try:
        key = base64.b64decode(raw, validate=True)
        if len(key) == 32:
            return key
    except Exception:  # noqa: BLE001 —— 非 base64 继续尝试 hex
        pass
    try:
        key = bytes.fromhex(raw)
        if len(key) == 32:
            return key
    except ValueError:
        pass
    # 任意字符串 → PBKDF2 派生 32 字节（便利模式；推荐直接配置 32 字节随机密钥）
    return hashlib.pbkdf2_hmac("sha256", raw.encode("utf-8"), b"oumed-chain-data-key", 200_000)


def parse_key(raw: str) -> bytes:
    """解析 32 字节密钥（公开入口）：base64/hex/口令派生；空串返回 b""。

    存储加密（YIBAO_DATA_KEY）与联邦参数信封（FEDERATION_PARAM_KEY）共用解析规则。
    """
    return _parse_key(raw)


def _get_key() -> bytes:
    global _key_cache
    if _key_cache is None:
        _key_cache = _parse_key(settings.YIBAO_DATA_KEY)
    return _key_cache


def reset_key_cache() -> None:
    """清空密钥缓存（测试用：切换 settings 后调用）。"""
    global _key_cache
    _key_cache = None


def encryption_enabled() -> bool:
    """存储加密是否启用（YIBAO_DATA_KEY 已配置）。"""
    return bool(_get_key())


def encrypt_value(plain: str | None, deterministic: bool = False) -> str | None:
    """加密明文字符串；未配置密钥/空值原样返回。"""
    if plain is None or plain == "" or not encryption_enabled():
        return plain
    key = _get_key()
    if deterministic:
        nonce = hmac_mod.new(key, plain.encode("utf-8"), hashlib.sha256).digest()[:12]
    else:
        nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plain.encode("utf-8"), None)
    return _PREFIX + base64.b64encode(nonce).decode("ascii") + ":" + base64.b64encode(ct).decode("ascii")


def decrypt_value(cipher: str | None) -> str | None:
    """解密密文字符串；无 enc1: 前缀（存量明文）原样返回。

    解密失败（密钥轮换/密文损坏）按空值兜底并告警——医疗字段宁缺勿错，
    不把异常密文当业务数据透传。
    """
    if not cipher or not cipher.startswith(_PREFIX):
        return cipher
    try:
        _, nonce_b64, ct_b64 = cipher.split(":", 2)
        nonce = base64.b64decode(nonce_b64)
        ct = base64.b64decode(ct_b64)
        return AESGCM(_get_key()).decrypt(nonce, ct, None).decode("utf-8")
    except Exception:  # noqa: BLE001 —— 解密失败不透传密文
        import logging

        logging.getLogger(__name__).warning("存储加密字段解密失败（密钥不匹配或数据损坏），返回空值")
        return ""


class EncryptedString(TypeDecorator):
    """透明加密字符串列（随机 nonce；不需要等值查询的敏感字段用）。"""

    impl = String(500)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt_value(value)

    def process_result_value(self, value, dialect):
        return decrypt_value(value)


class EncryptedText(TypeDecorator):
    """透明加密长文本列（随机 nonce；健康自述/解析文本用）。"""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt_value(value)

    def process_result_value(self, value, dialect):
        return decrypt_value(value)


class DeterministicString(TypeDecorator):
    """确定性加密字符串列（同明文同密文；需要 SQL 等值查询/唯一索引的字段用，如 email）。"""

    impl = String(500)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt_value(value, deterministic=True)

    def process_result_value(self, value, dialect):
        return decrypt_value(value)
