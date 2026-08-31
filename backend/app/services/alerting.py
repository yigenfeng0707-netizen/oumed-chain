"""告警触达通道（钉钉/飞书群机器人 Webhook，零新依赖）。

与 Prometheus 告警规则互补：后者面向指标趋势，本模块面向实时安全事件
（如管理端撞库）。设计约束：
- 未配置 ALERT_WEBHOOK_URL 时只记日志不推送（沙箱零配置）；
- 冷却窗口防刷屏（同类告警 10 分钟内最多一次）；
- 发送失败只记日志，绝不影响主流程。
"""

import json
import logging
import threading
import time
import urllib.request

from app.config import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_last_sent: dict[str, float] = {}  # alert_key -> 上次推送时间戳
COOLDOWN_SECONDS = 600

# 管理端登录失败滑窗（撞库信号）：失败时间戳列表，窗口 10 分钟
_LOGIN_FAILURE_WINDOW = 600
_login_failures: list[float] = []


def send_alert(key: str, title: str, detail: str, cooldown: float = COOLDOWN_SECONDS) -> bool:
    """推送一条告警。返回是否真实推送（冷却中/未配置返回 False）。"""
    now = time.time()
    with _lock:
        if now - _last_sent.get(key, 0) < cooldown:
            logger.info("告警冷却中，跳过推送：%s", key)
            return False
        _last_sent[key] = now

    logger.warning("[ALERT] %s | %s", title, detail)
    url = settings.ALERT_WEBHOOK_URL
    if not url:
        return False  # 未配置通道：仅日志留痕

    # 钉钉/飞书群机器人均兼容 {msgtype, text.content} 格式
    body = json.dumps({
        "msgtype": "text",
        "text": {"content": f"[瓯医数链告警] {title}\n{detail}"},
    }).encode("utf-8")
    try:
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status < 300
    except Exception as exc:
        logger.warning("告警推送失败（%s）：%s", key, exc)
        return False


def record_admin_login_failure(username: str) -> bool:
    """记录管理端登录失败；滑窗内达到阈值时触发撞库告警。返回是否推送。"""
    now = time.time()
    with _lock:
        _login_failures.append(now)
        # 只保留窗口内的记录
        cutoff = now - _LOGIN_FAILURE_WINDOW
        while _login_failures and _login_failures[0] < cutoff:
            _login_failures.pop(0)
        count = len(_login_failures)

    if count < settings.ALERT_LOGIN_FAILURE_THRESHOLD:
        return False
    return send_alert(
        "admin_brute_force",
        "管理后台疑似撞库",
        f"近 10 分钟登录失败 {count} 次（最近尝试账号：{username}）。"
        "请核查访问来源并考虑更换管理密码。",
    )


def reset_state() -> None:
    """清空内部状态（测试用）。"""
    with _lock:
        _last_sent.clear()
        _login_failures.clear()
