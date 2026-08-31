"""存证链外部锚定（P2-3.4）：链尖摘要 → RFC 3161 可信时间戳。

设计要点：
- 链尖 = sha256(联邦任务链尖 | 交易链尖 | 事件总数)，两条审计存证链合并锚定；
- TSA 令牌由第三方时间戳机构私钥签名，平台自身无法伪造历史时间点——
  即使内部作恶重算整条哈希链，锚定令牌中的时间戳签名也无法同步伪造；
- 零新依赖：TimeStampReq 用最小 DER 手工编码（RFC 3161 §2.4.1），HTTP 用 urllib；
- TSA 不可达（网络受限/机构故障）时降级为 offline 留痕，不阻断业务，可事后补锚。
"""

import asyncio
import base64
import hashlib
import logging
import urllib.request
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChainAnchor, DataTransaction, FederationJob

logger = logging.getLogger(__name__)

# sha-256 算法 OID（2.16.840.1.101.3.4.2.1）的 DER 编码
_OID_SHA256 = bytes.fromhex("0609608648016503040201")
_EMPTY_CHAIN_TIP = "0" * 64


# ============================================================
# RFC 3161 TimeStampReq 最小 DER 编码
# ============================================================

def _der_len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(body)]) + body


def _tlv(tag: int, content: bytes) -> bytes:
    return bytes([tag]) + _der_len(len(content)) + content


def build_timestamp_request(digest: bytes, nonce: int | None = None) -> bytes:
    """构造 TimeStampReq ::= SEQUENCE { version, messageImprint, [nonce], certReq }"""
    if len(digest) != 32:
        raise ValueError("digest 必须是 32 字节（sha256）")
    algo = _tlv(0x30, _OID_SHA256 + bytes([0x05, 0x00]))  # AlgorithmIdentifier + NULL
    imprint = _tlv(0x30, algo + _tlv(0x04, digest))
    body = bytes([0x02, 0x01, 0x01]) + imprint  # version = 1
    if nonce is not None:
        body += _tlv(0x02, nonce.to_bytes((nonce.bit_length() + 7) // 8, "big"))
    body += bytes([0x01, 0x01, 0xFF])  # certReq = TRUE（令牌含证书，可独立验证）
    return _tlv(0x30, body)


def parse_response_status(resp: bytes) -> int | None:
    """轻量解析 TimeStampResp 的 PKIStatus：0/1 = 成功（含带修改授予）。

    结构：SEQUENCE { SEQUENCE { INTEGER status, ... }, ... }。
    解析失败返回 None（按失败处理，不做完整 ASN.1 校验）。
    """
    try:
        idx = 0
        if resp[idx] != 0x30:  # TimeStampResp 外层 SEQUENCE
            return None
        idx += 1
        idx += 1 + (resp[idx] & 0x7F) if resp[idx] & 0x80 else 1  # 跳过外层长度（短/长形式）
        if resp[idx] != 0x30:  # PKIStatusInfo SEQUENCE
            return None
        idx += 1
        idx += 1 + (resp[idx] & 0x7F) if resp[idx] & 0x80 else 1  # 跳过内层长度
        if resp[idx] != 0x02 or resp[idx + 1] != 0x01:  # INTEGER status
            return None
        return resp[idx + 2]
    except (IndexError, TypeError):
        return None


# ============================================================
# TSA HTTP 请求（同步，供 asyncio.to_thread 调用 / 测试 monkeypatch）
# ============================================================

def tsa_post(url: str, tsr_body: bytes, timeout: float = 15.0) -> bytes:
    """向 TSA 提交时间戳请求，返回 TimeStampResp 原始字节。"""
    req = urllib.request.Request(
        url,
        data=tsr_body,
        headers={
            "Content-Type": "application/timestamp-query",
            "User-Agent": "Mozilla/5.0 (OuMedTrust chain anchor)",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def request_timestamp(digest: bytes, tsa_url: str, timeout: float = 15.0) -> bytes:
    """请求时间戳令牌；非授权状态抛 ValueError。"""
    nonce = int.from_bytes(digest[:8], "big")  # 确定性 nonce，便于审计复现
    resp = tsa_post(tsa_url, build_timestamp_request(digest, nonce), timeout)
    status = parse_response_status(resp)
    if status not in (0, 1):
        raise ValueError(f"TSA 拒绝请求（PKIStatus={status}）")
    return resp


# ============================================================
# 链尖计算与锚定落库
# ============================================================

async def _chain_tip(db: AsyncSession, model) -> tuple[str, int]:
    """取指定存证链的最新 event_hash 与已存证事件数。"""
    latest = (await db.execute(
        select(model.event_hash)
        .where(model.event_hash.isnot(None))
        .order_by(model.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()
    count = await db.scalar(
        select(func.count()).select_from(model).where(model.event_hash.isnot(None))
    )
    return (latest or _EMPTY_CHAIN_TIP, count or 0)


async def create_anchor(
    db: AsyncSession,
    tsa_url: str | None = None,
    timeout: float = 15.0,
) -> ChainAnchor:
    """计算链尖并向 TSA 锚定；TSA 不可达降级为 offline 留痕。"""
    fed_tip, fed_count = await _chain_tip(db, FederationJob)
    market_tip, market_count = await _chain_tip(db, DataTransaction)
    event_count = fed_count + market_count
    tip_hash = hashlib.sha256(
        f"{fed_tip}|{market_tip}|{event_count}".encode("utf-8")
    ).hexdigest()

    anchor = ChainAnchor(
        created_at=datetime.now(UTC),
        tip_hash=tip_hash,
        fed_tip=fed_tip,
        market_tip=market_tip,
        event_count=event_count,
        tsa_url=tsa_url or "",
        status="pending",
    )

    if not tsa_url:
        anchor.status = "offline"
        anchor.error = "未配置 TSA 地址（CHAIN_ANCHOR_TSA_URL 为空）"
    else:
        try:
            resp = await asyncio.to_thread(
                request_timestamp, bytes.fromhex(tip_hash), tsa_url, timeout
            )
            anchor.status = "anchored"
            anchor.ts_token_b64 = base64.b64encode(resp).decode("ascii")
        except Exception as exc:  # 网络受限/机构故障均降级，不阻断
            logger.warning("链锚定降级为离线留痕：%s", exc)
            anchor.status = "offline"
            anchor.error = str(exc)[:255]

    db.add(anchor)
    await db.commit()
    await db.refresh(anchor)
    return anchor


def anchor_to_dict(a: ChainAnchor, include_token: bool = False) -> dict:
    d = {
        "id": a.id,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "tip_hash": a.tip_hash,
        "fed_tip": a.fed_tip,
        "market_tip": a.market_tip,
        "event_count": a.event_count,
        "tsa_url": a.tsa_url,
        "status": a.status,
        "error": a.error,
    }
    if include_token:
        d["ts_token_b64"] = a.ts_token_b64
    return d
