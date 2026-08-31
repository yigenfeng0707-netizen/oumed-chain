"""支付宝在线支付服务（Agent 微支付 / 数据产品结算）。

双模设计（与平台 DEMO_MODE 哲学一致）：
- sandbox（默认，零配置）：本地确定性模拟——生成伪二维码载荷，
  供演示环境走通「下单→扫码→回调→分账存证」全链路，零外部依赖；
- live：python-alipay-sdk 真实收款。个人账号网页应用开放的是电脑网站支付
  （alipay.trade.page.pay，收银台支持扫码付），故 live 返回支付表单而非二维码；
  notify 异步回调 + 轮询主动查单（alipay.trade.query）双保险，RSA2 验签。
  （当面付 precreate 需商家主体，个人账号不可用——已实证确认。）

私钥安全：live 密钥来自环境变量（.env / 平台 secrets），缺省时兜底读
仓库外 keys/ 目录（仅本地开发便利，部署镜像不打包 .env/keys）。
"""

import hashlib
import hmac
import logging
from pathlib import Path

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


_KEYS_DIR = Path(__file__).resolve().parents[2] / "keys"


def _read_key_file(name: str) -> str:
    """密钥兜底：环境变量为空时读 backend/keys/<name>（文件可不存在）。"""
    p = _KEYS_DIR / name
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _normalize_pem(raw: str) -> str:
    """环境变量 PEM 归一化：单行 \n 转义还原为换行，并剥离 CR 污染。

    Windows CRLF 密钥经 \n 转义后每个换行变成「CR + 字面 \n」（3 字符），
    平台 secrets 存储链路对裸 CR 兼容性不可控——统一剥离真实 CR 与
    字面 \r，再把 \n 还原为 LF，保证送入 SDK 的是纯 LF PEM。"""
    return (
        raw.replace("\\r", "")   # 字面 \r（部分转义工具的副产物）
        .replace("\r", "")      # 真实 CR（CRLF 残留）
        .replace("\\n", "\n")  # 字面 \n → 换行
        .strip()
    )


def _get_client():
    """live 模式懒初始化 AliPay 客户端（缺配置时报错，由调用方降级）。"""
    global _client
    if _client is not None:
        return _client
    if not settings.ALIPAY_APP_ID:
        raise RuntimeError("live 模式需配置 ALIPAY_APP_ID")
    app_private_key = settings.ALIPAY_APP_PRIVATE_KEY or _read_key_file("alipay_app_private.pem")
    alipay_public_key = settings.ALIPAY_PUBLIC_KEY or _read_key_file("alipay_public.pem")
    if not app_private_key:
        raise RuntimeError("live 模式需配置 ALIPAY_APP_PRIVATE_KEY（或 backend/keys/alipay_app_private.pem）")
    if not alipay_public_key:
        raise RuntimeError("live 模式需配置 ALIPAY_PUBLIC_KEY（回调验签必需）")
    from alipay import AliPay  # 延迟导入：沙箱环境无 SDK 也不影响启动

    _client = AliPay(
        appid=settings.ALIPAY_APP_ID,
        app_notify_url=settings.ALIPAY_NOTIFY_URL or None,
        app_private_key_string=_normalize_pem(app_private_key),
        alipay_public_key_string=_normalize_pem(alipay_public_key),
        sign_type="RSA2",
        debug=False,  # 正式网关（个人电脑网站支付）
    )
    return _client


def create_precreate(order_no: str, subject: str, amount_yuan: float) -> dict:
    """下单。沙箱返回 {"qr_code", gateway=sandbox}；live 返回 {"pay_form", gateway=live}
    （电脑网站支付表单，前端新窗口打开即进支付宝收银台，收银台支持扫码付）。"""
    if mode() != "live":
        # 沙箱：确定性伪二维码载荷（前端可据此渲染模拟扫码页）
        sig = _sandbox_sig(order_no)
        qr = f"oumedtrust://sandbox-pay?order={order_no}&amount={amount_yuan:.2f}&sig={sig}"
        return {"qr_code": qr, "pay_form": None, "gateway": "sandbox"}

    client = _get_client()
    form_html = client.api_alipay_trade_page_pay(
        out_trade_no=order_no,
        total_amount=f"{amount_yuan:.2f}",
        subject=subject[:128],
        return_url=(settings.ALIPAY_RETURN_URL or None) or None,
    )
    return {"qr_code": None, "pay_form": form_html, "gateway": "live"}


def query_trade_status(order_no: str) -> dict | None:
    """主动查单（alipay.trade.query）：回调未达时的补偿通道。
    返回 {"paid": bool, "trade_no": str|None}；沙箱或异常返回 None。"""
    if mode() != "live":
        return None
    try:
        result = _get_client().api_alipay_trade_query(out_trade_no=order_no)
    except Exception:  # noqa: BLE001 —— 查单失败不阻断轮询
        logger.exception("支付宝查单异常")
        return None
    if result.get("code") != "10000":
        return {"paid": False, "trade_no": None}
    return {
        "paid": result.get("trade_status") in ("TRADE_SUCCESS", "TRADE_FINISHED"),
        "trade_no": result.get("trade_no"),
    }


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
