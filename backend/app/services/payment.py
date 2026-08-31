"""支付宝当面付服务（个人免资质；Agent 微支付 / 数据产品结算）。

双模设计（与平台 DEMO_MODE 哲学一致）：
- sandbox（默认，零配置）：本地确定性模拟——生成伪二维码载荷，
  供演示环境走通「下单→扫码→回调→分账存证」全链路，零外部依赖；
- live：python-alipay-sdk 真实当面付（alipay.trade.precreate 扫码 +
  notify 异步回调 RSA2 验签）。个人身份即可开通，额度低但契合微支付场景。

私钥安全：live 密钥来自环境变量（.env / 平台 secrets），不进仓库不进聊天。
"""

import hashlib
import hmac
import logging

from app.config import settings

logger = logging.getLogger(__name__)

_client = None  # live 模式 AliPay 实例缓存


# ============================================================
# Agent 按次付费价目表（元）——"智能体微支付"演示锚点
# ============================================================
AGENT_SERVICE_PRICES = {
    "cancer_predict": {"subject": "泛癌卫士 · 单次风险预测", "amount": 9.9},
    "imaging_report": {"subject": "影像引擎 · 单份 AI 报告", "amount": 5.0},
    "body_archive_month": {"subject": "档案管家 · 月度会员", "amount": 19.9},
    "health_profile": {"subject": "健康卫士 · 深度画像", "amount": 3.9},
}


def mode() -> str:
    return (settings.ALIPAY_MODE or "sandbox").lower()


def _get_client():
    """live 模式懒初始化 AliPay 客户端（缺配置时报错，由调用方降级）。"""
    global _client
    if _client is not None:
        return _client
    if not settings.ALIPAY_APP_ID or not settings.ALIPAY_APP_PRIVATE_KEY:
        raise RuntimeError("live 模式需配置 ALIPAY_APP_ID / ALIPAY_APP_PRIVATE_KEY")
    if not settings.ALIPAY_PUBLIC_KEY:
        raise RuntimeError("live 模式需配置 ALIPAY_PUBLIC_KEY（回调验签必需）")
    from alipay import AliPay  # 延迟导入：沙箱环境无 SDK 也不影响启动

    _client = AliPay(
        appid=settings.ALIPAY_APP_ID,
        app_notify_url=settings.ALIPAY_NOTIFY_URL or None,
        app_private_key_string=settings.ALIPAY_APP_PRIVATE_KEY.replace("\\n", "\n"),
        alipay_public_key_string=settings.ALIPAY_PUBLIC_KEY.replace("\\n", "\n"),
        sign_type="RSA2",
        debug=False,  # 个人当面付走正式网关；沙箱应用另配 debug 环境
    )
    return _client


def create_precreate(order_no: str, subject: str, amount_yuan: float) -> dict:
    """下单（当面付预下单 → 二维码）。返回 {"qr_code", "gateway"}。"""
    if mode() != "live":
        # 沙箱：确定性伪二维码载荷（前端可据此渲染模拟扫码页）
        sig = _sandbox_sig(order_no)
        qr = f"oumedtrust://sandbox-pay?order={order_no}&amount={amount_yuan:.2f}&sig={sig}"
        return {"qr_code": qr, "gateway": "sandbox"}

    client = _get_client()
    result = client.api_alipay_trade_precreate(
        out_trade_no=order_no,
        total_amount=f"{amount_yuan:.2f}",
        subject=subject[:128],
    )
    if result.get("code") != "10000":
        raise RuntimeError(f"支付宝下单失败：{result.get('sub_msg') or result}")
    return {"qr_code": result["qr_code"], "gateway": "live"}


def verify_notify(params: dict) -> bool:
    """回调验签：live 用支付宝公钥 RSA2；sandbox 校验模拟签名。"""
    if mode() != "live":
        order_no = params.get("out_trade_no", "")
        return bool(order_no) and hmac.compare_digest(
            params.get("sandbox_sig", ""), _sandbox_sig(order_no)
        )
    sig = params.pop("sign", None)
    params.pop("sign_type", None)
    if not sig:
        return False
    try:
        return _get_client().verify(params, sig)
    except Exception:  # noqa: BLE001 —— 验签异常一律拒绝
        logger.exception("支付宝回调验签异常")
        return False


def _sandbox_sig(order_no: str) -> str:
    """沙箱模拟支付凭证：HMAC(会话密钥, 订单号)，防随意完结订单。"""
    return hmac.new(
        settings.YIBAO_SESSION_SECRET.encode("utf-8"),
        order_no.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:32]


def pay_proof_of(order_no: str, trade_no: str, amount_cents: int, paid_at_iso: str) -> str:
    """支付存证哈希（接入可信数据空间存证叙事）。"""
    raw = f"pay|{order_no}|{trade_no}|{amount_cents}|{paid_at_iso}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
