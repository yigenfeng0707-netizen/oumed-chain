"""支付宝当面付路由（Agent 微支付 / 数据产品结算）。

链路：下单（扫码）→ 回调验签 → 订单完结 →（市场单）自动创建已成交交易
+ 70/20/10 分账 + 存证链。沙箱模式全本地模拟，live 模式走真实当面付。
"""

import logging
import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, metrics
from app.database import get_db
from app.deps import SessionDep
from app.models import DataProduct, DataTransaction, PaymentOrder
from app.services import payment

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/payments", tags=["支付宝当面付"])


class PrecreateRequest(BaseModel):
    kind: str  # marketplace / agent_service
    ref_id: str = ""  # 产品 id 或服务键
    user_id: str = ""


class SandboxCompleteRequest(BaseModel):
    order_no: str


def _new_order_no() -> str:
    return "OM" + datetime.now(UTC).strftime("%y%m%d%H%M%S") + secrets.token_hex(2).upper()


def _order_to_dict(o: PaymentOrder, include_qr: bool = False) -> dict:
    d = {
        "order_no": o.order_no,
        "kind": o.kind,
        "ref_id": o.ref_id,
        "subject": o.subject,
        "amount_cents": o.amount_cents,
        "status": o.status,
        "gateway": o.gateway,
        "trade_no": o.trade_no,
        "pay_proof": o.pay_proof,
        "paid_at": o.paid_at.isoformat() if o.paid_at else None,
        "created_at": o.created_at.isoformat() if o.created_at else None,
    }
    if include_qr:
        d["qr_code"] = o.qr_code
    return d


async def _complete_payment(db: AsyncSession, order: PaymentOrder, trade_no: str) -> dict:
    """订单完结：置已付 + 存证哈希；市场单追加已成交交易与分账。幂等。"""
    if order.status == "paid":
        return {"order_no": order.order_no, "status": "paid", "already": True}

    now = datetime.now(UTC)
    order.status = "paid"
    order.trade_no = trade_no
    order.paid_at = now
    order.pay_proof = payment.pay_proof_of(
        order.order_no, trade_no, order.amount_cents, now.isoformat()
    )

    settle = None
    if order.kind == "marketplace" and order.ref_id:
        product = await db.get(DataProduct, order.ref_id)
        if product is not None:
            amount = order.amount_cents
            tx = DataTransaction(
                id=secrets.token_hex(18),
                product_id=product.id,
                product_name=product.name,
                buyer=f"在线支付（{order.user_id or '匿名'}）",
                amount=amount,
                status="已成交",
                revenue_provider=round(amount * 0.7),
                revenue_platform=round(amount * 0.2),
                revenue_contributor=amount - round(amount * 0.7) - round(amount * 0.2),
                purpose="支付宝当面付在线购买",
                created_at=now,
            )
            db.add(tx)
            await db.flush()
            # 延迟导入避免路由间循环
            from app.routers.marketplace import _append_chain
            await _append_chain(db, tx)
            settle = {
                "transaction_id": tx.id,
                "event_hash": tx.event_hash,
                "revenue": {
                    "provider": tx.revenue_provider,
                    "platform": tx.revenue_platform,
                    "contributor": tx.revenue_contributor,
                },
            }

    await db.commit()
    await db.refresh(order)
    return {"order_no": order.order_no, "status": "paid", "already": False,
            "pay_proof": order.pay_proof, "settle": settle}


@router.post("/precreate")
async def precreate(req: PrecreateRequest, db: AsyncSession = Depends(get_db),
                    _session: str = SessionDep):
    """当面付下单：返回支付二维码（沙箱为模拟载荷）。"""
    if req.kind == "marketplace":
        product = await db.get(DataProduct, req.ref_id)
        if product is None:
            raise HTTPException(status_code=404, detail="数据产品不存在")
        subject, amount_yuan = product.name, max(float(product.price), 0.01)
    elif req.kind == "agent_service":
        svc = payment.AGENT_SERVICE_PRICES.get(req.ref_id)
        if svc is None:
            raise HTTPException(status_code=404, detail=f"未知 Agent 服务：{req.ref_id}")
        subject, amount_yuan = svc["subject"], svc["amount"]
    else:
        raise HTTPException(status_code=400, detail="kind 仅支持 marketplace / agent_service")

    order_no = _new_order_no()
    try:
        qr = payment.create_precreate(order_no, subject, amount_yuan)
    except Exception as exc:  # live 缺配置/网关异常 → 明确报错，不静默
        raise HTTPException(status_code=502, detail=f"支付网关下单失败：{exc}")

    order = PaymentOrder(
        order_no=order_no,
        kind=req.kind,
        ref_id=req.ref_id,
        user_id=req.user_id,
        subject=subject,
        amount_cents=round(amount_yuan * 100),
        status="pending",
        gateway=qr["gateway"],
        qr_code=qr["qr_code"],
        created_at=datetime.now(UTC),
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)
    return _order_to_dict(order, include_qr=True)


@router.get("/order/{order_no}")
async def order_status(order_no: str, db: AsyncSession = Depends(get_db)):
    """订单状态轮询（前端扫码页用；仅含金额与状态，无个人数据）。"""
    order = await crud.get_payment_order(db, order_no)
    if order is None:
        raise HTTPException(status_code=404, detail="订单不存在")
    return _order_to_dict(order)


@router.post("/alipay/notify")
async def alipay_notify(request: Request, db: AsyncSession = Depends(get_db)):
    """支付宝异步回调（公网无鉴权，靠验签保证真实性）。"""
    form = await request.form()
    params = dict(form)
    if not payment.verify_notify(params):
        metrics.observe_denial("payment_notify_invalid_sign")
        return PlainTextResponse("fail")

    order_no = params.get("out_trade_no", "")
    trade_status = params.get("trade_status", "TRADE_SUCCESS")
    order = await crud.get_payment_order(db, order_no)
    if order is None:
        return PlainTextResponse("fail")
    if trade_status not in ("TRADE_SUCCESS", "TRADE_FINISHED"):
        return PlainTextResponse("success")  # 非支付成功事件，确认收到即可

    await _complete_payment(db, order, params.get("trade_no", f"SIM-{order_no}"))
    return PlainTextResponse("success")  # 必须回 success，否则支付宝会重推


@router.post("/sandbox/complete")
async def sandbox_complete(req: SandboxCompleteRequest, db: AsyncSession = Depends(get_db)):
    """沙箱模拟支付成功（仅沙箱模式开放；演示页「模拟扫码支付」按钮）。"""
    if payment.mode() != "sandbox":
        raise HTTPException(status_code=403, detail="live 模式请等待支付宝真实回调")
    order = await crud.get_payment_order(db, req.order_no)
    if order is None:
        raise HTTPException(status_code=404, detail="订单不存在")
    result = await _complete_payment(db, order, f"SIM-{order.order_no}")
    return result
