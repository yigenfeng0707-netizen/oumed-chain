"""Prometheus 指标埋点（零新依赖，手写 exposition 文本格式）。

指标清单：
- http_requests_total{route,status}          请求计数（按路由模板+状态码）
- http_request_duration_seconds{route}       请求耗时直方图
- auth_denials_total{reason}                 严格模式越权拒绝计数（对接 deps 审计）
- admin_login_failures_total                 管理后台登录失败计数（撞库信号）
- process_start_time_seconds / app_info      进程与运行模式信息

路由标签取 FastAPI 路由模板（如 /api/health/{user_id}/profile），
避免 user_id 等高基数值撑爆标签。/metrics 自身不计数。
"""

import threading
import time

START_TIME = time.time()

_lock = threading.Lock()

# (route, status) -> count
REQUESTS: dict[tuple[str, int], int] = {}

# route -> [各桶计数..., 总次数, 耗时总和]
DURATION_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)
DURATIONS: dict[str, list[float]] = {}

# reason -> count
DENIALS: dict[str, int] = {}

ADMIN_LOGIN_FAILURES = 0


def observe_request(route: str, status: int, duration: float) -> None:
    """中间件在每个请求结束后调用。"""
    with _lock:
        REQUESTS[(route, status)] = REQUESTS.get((route, status), 0) + 1
        row = DURATIONS.setdefault(route, [0] * (len(DURATION_BUCKETS) + 2))
        for i, bound in enumerate(DURATION_BUCKETS):
            if duration <= bound:
                row[i] += 1
        row[-2] += 1
        row[-1] += duration


def observe_denial(reason: str) -> None:
    """严格模式拒绝事件（401/403）计数。"""
    with _lock:
        DENIALS[reason] = DENIALS.get(reason, 0) + 1


def observe_admin_login_failure() -> None:
    global ADMIN_LOGIN_FAILURES
    with _lock:
        ADMIN_LOGIN_FAILURES += 1


def _fmt(v: float) -> str:
    return f"{v:.6f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)


def render_prometheus(demo_mode: bool = True) -> str:
    """生成 Prometheus exposition 文本（text/plain; version=0.0.4）。"""
    lines: list[str] = []
    with _lock:
        lines += [
            "# HELP http_requests_total HTTP requests by route template and status.",
            "# TYPE http_requests_total counter",
        ]
        for (route, status), n in sorted(REQUESTS.items()):
            lines.append(f'http_requests_total{{route="{route}",status="{status}"}} {n}')

        lines += [
            "# HELP http_request_duration_seconds Request duration by route template.",
            "# TYPE http_request_duration_seconds histogram",
        ]
        for route, row in sorted(DURATIONS.items()):
            for i, bound in enumerate(DURATION_BUCKETS):
                # row[i] 在插入时已维护累计语义（≤ 上界的观测数）
                lines.append(
                    f'http_request_duration_seconds_bucket{{route="{route}",le="{bound}"}} {int(row[i])}'
                )
            lines.append(f'http_request_duration_seconds_bucket{{route="{route}",le="+Inf"}} {int(row[-2])}')
            lines.append(f'http_request_duration_seconds_sum{{route="{route}"}} {_fmt(row[-1])}')
            lines.append(f'http_request_duration_seconds_count{{route="{route}"}} {int(row[-2])}')

        lines += [
            "# HELP auth_denials_total Access denials (401/403) in strict mode by reason.",
            "# TYPE auth_denials_total counter",
        ]
        for reason, n in sorted(DENIALS.items()):
            lines.append(f'auth_denials_total{{reason="{reason}"}} {n}')

        lines += [
            "# HELP admin_login_failures_total Admin login failures (brute-force signal).",
            "# TYPE admin_login_failures_total counter",
            f"admin_login_failures_total {ADMIN_LOGIN_FAILURES}",
        ]

        lines += [
            "# HELP process_start_time_seconds Unix start time of the process.",
            "# TYPE process_start_time_seconds gauge",
            f"process_start_time_seconds {int(START_TIME)}",
            "# HELP app_info Application runtime mode.",
            "# TYPE app_info gauge",
            f'app_info{{service="oumed-trust",demo_mode="{str(demo_mode).lower()}"}} 1',
        ]
    return "\n".join(lines) + "\n"
