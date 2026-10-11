"""登录失败账号级锁定（P0-1.2 等保三级：入侵防范·爆破防护）。

策略（内存态，零新依赖；与既有撞库滑窗告警互补）：
- 同一账号连续失败达阈值（默认 5 次）触发指数退避锁定：
  第 5 次锁 30s，之后每次失败翻倍（60s/120s/…），封顶 15 分钟；
- 登录成功即清零；锁定期间**即使密码正确也拒绝**（423）——防在线爆破；
- 双模：DEMO_MODE=true（演示）跳过锁定，保持演示零摩擦；
  生产模式（DEMO_MODE=false）调用方检查并计数；
- 账号不存在同样计数（key 统一为提交的邮箱/用户名，防枚举语义不变：
  响应仍为统一"邮箱或密码错误"，仅锁定后明确提示等待时间）；
- 内存态重启即清（可接受：锁定是即时防护，持久化字典反而成攻击面；
  键数量级受控，超上限自动清理已过期条目防内存膨胀）。
"""

import logging
import time

logger = logging.getLogger(__name__)

LOCK_THRESHOLD = 5        # 连续失败多少次触发锁定
BASE_LOCK_SECONDS = 30    # 首次锁定时长（指数退避基值）
MAX_LOCK_SECONDS = 900    # 锁定上限 15 分钟
_MAX_KEYS = 10_000        # 内存防膨胀：键数上限（超限清理已过期条目）

# key → [失败次数, 锁定截止时间（monotonic 秒）]
_attempts: dict[str, list] = {}


def _now() -> float:
    """时钟源（测试可 monkeypatch 加速时间流逝）。"""
    return time.monotonic()


def _evict_expired() -> None:
    """清理已过期条目（键数超上限时触发，防内存无限膨胀）。"""
    if len(_attempts) <= _MAX_KEYS:
        return
    now = _now()
    for key in [k for k, v in _attempts.items() if v[1] <= now]:
        _attempts.pop(key, None)


def is_locked(key: str) -> int:
    """返回剩余锁定秒数；0 = 未锁定。"""
    entry = _attempts.get(key)
    if not entry:
        return 0
    remain = int(entry[1] - _now())
    return max(remain, 0)


def record_failure(key: str) -> int:
    """记录一次失败；达到阈值则按指数退避延长锁定。返回本次新触发的锁定秒数。

    上次锁定已过期时重新计数（标准指数退避语义：解除后给正常重试机会，
    再次连续失败到阈值才会重新锁定）。
    """
    now = _now()
    entry = _attempts.get(key)
    if entry and entry[1] and entry[1] <= now:
        _attempts.pop(key, None)  # 锁定已过期 → 重新计数
        entry = None
    entry = entry or _attempts.setdefault(key, [0, 0.0])
    entry[0] += 1
    new_lock = 0
    if entry[0] >= LOCK_THRESHOLD:
        shift = entry[0] - LOCK_THRESHOLD
        new_lock = min(BASE_LOCK_SECONDS * (2 ** shift), MAX_LOCK_SECONDS)
        entry[1] = now + new_lock
        logger.warning("账号登录失败锁定：%s 连续失败 %d 次，锁定 %ds", key[:64], entry[0], new_lock)
    _evict_expired()
    return new_lock


def record_success(key: str) -> None:
    """登录成功：清零该账号计数与锁定。"""
    _attempts.pop(key, None)


def clear_all() -> None:
    """清空全部状态（测试用）。"""
    _attempts.clear()
