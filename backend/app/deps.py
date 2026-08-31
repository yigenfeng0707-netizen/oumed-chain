"""全站身份作用域依赖（P0：移除“信任 user_id 路径参数”Demo 模式）。

双模设计（演示与生产同构）：
- DEMO_MODE=true（默认，演示/路演环境）：原样放行，user-switcher 零摩擦；
- DEMO_MODE=false（生产环境必须设置）：
  * 用户作用域端点必须携带有效 X-User-Token 且与路径 user_id 匹配，否则 401/403；
  * X-Admin-Token（超级管理员）可代查任意用户（RBAC 角色判断）；
  * 列表型端点要求任意有效会话（require_session）。

token 校验复用 app.auth.parse_user_token（HMAC 无状态）与
app.routers.admin 的管理员 token 计算，不引入新依赖。

越权审计：生产模式下所有 401/403 拒绝均落库 access_denial_logs
（方法/路径/目标用户/原因/来源 IP），管理后台 /api/admin/security/denials 可查。
"""

import logging

from fastapi import Depends, Header, HTTPException, Request, status

from app.auth import parse_user_token
from app.config import settings

logger = logging.getLogger(__name__)


def _valid_admin_token(token: str | None) -> bool:
    """管理员 token 校验（与 admin.require_admin 同口径）。"""
    if not token:
        return False
    # 延迟导入避免模块加载顺序问题（admin 不依赖 deps，无环）
    from app.routers.admin import _admin_credentials, _issue_admin_token

    username, _ = _admin_credentials()
    return token == _issue_admin_token(username)


async def _log_denial(
    request: Request | None,
    status_code: int,
    reason: str,
    target_user_id: str | None,
    token_present: bool,
) -> None:
    """越权拒绝审计落库（best-effort：写库失败仅记日志，不影响拒绝响应）。"""
    from app.database import async_session
    from app.metrics import observe_denial
    from app.models import AccessDenialLog

    observe_denial(reason)  # 监控告警：拒绝突增信号

    try:
        async with async_session() as session:
            session.add(AccessDenialLog(
                method=request.method if request else "-",
                path=request.url.path if request else "-",
                target_user_id=target_user_id or "",
                status_code=status_code,
                reason=reason,
                client_ip=(request.client.host if request and request.client else ""),
                token_present=token_present,
            ))
            await session.commit()
    except Exception:  # noqa: BLE001 —— 审计失败不能阻断正常拒绝流程
        logger.warning("越权审计落库失败：%s %s → %s", status_code, reason,
                       request.url.path if request else "-")


def check_scope(
    user_id: str,
    user_token: str | None,
    admin_token: str | None,
) -> str:
    """核心作用域校验（同步，可在任意端点手动调用；不落审计）。

    返回放行的 user_id；不满足时抛 401（无/无效会话）或 403（越权）。
    """
    if settings.DEMO_MODE:
        return user_id
    if _valid_admin_token(admin_token):
        return user_id  # 管理员代查
    uid = parse_user_token(user_token)
    if uid is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录或登录已过期，请携带有效 X-User-Token",
            headers={"WWW-Authenticate": "UserToken"},
        )
    if f"user_{uid:03d}" != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权访问其他用户的数据",
        )
    return user_id


async def scoped_user_id(
    request: Request,
    user_id: str,
    x_user_token: str | None = Header(None, alias="X-User-Token"),
    x_admin_token: str | None = Header(None, alias="X-Admin-Token"),
) -> str:
    """用户作用域依赖：路径 {user_id} 端点直接挂载（拒绝时落审计）。"""
    if settings.DEMO_MODE:
        return user_id
    if _valid_admin_token(x_admin_token):
        return user_id  # 管理员代查
    uid = parse_user_token(x_user_token)
    if uid is None:
        await _log_denial(request, 401, "missing_or_invalid_user_token",
                          user_id, bool(x_user_token))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录或登录已过期，请携带有效 X-User-Token",
            headers={"WWW-Authenticate": "UserToken"},
        )
    if f"user_{uid:03d}" != user_id:
        await _log_denial(request, 403, "cross_user_access", user_id, True)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权访问其他用户的数据",
        )
    return user_id


async def require_session(
    request: Request,
    x_user_token: str | None = Header(None, alias="X-User-Token"),
    x_admin_token: str | None = Header(None, alias="X-Admin-Token"),
) -> str:
    """列表型端点依赖：严格模式要求任意有效会话（用户或管理员）。"""
    if settings.DEMO_MODE:
        return "anonymous"
    if _valid_admin_token(x_admin_token) or parse_user_token(x_user_token) is not None:
        return "session"
    await _log_denial(request, 401, "missing_session", None, bool(x_user_token))
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="未登录或登录已过期，请携带有效 X-User-Token",
        headers={"WWW-Authenticate": "UserToken"},
    )


# 便捷组合：路径参数端点一行挂载
ScopeDep = Depends(scoped_user_id)
SessionDep = Depends(require_session)
