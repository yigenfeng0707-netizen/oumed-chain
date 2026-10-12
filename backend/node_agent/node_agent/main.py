"""节点 Agent 主循环：与平台 coordinator 握手完成一次完整联邦训练。

协议（全部上行均为 AES-GCM 信封或联调明文 JSON，原始数据零传输）：
1. 启动自检：凭证/数据文件就绪；
2. 发现任务（NODE_JOB_ID 或 /my-jobs 自动指派）；
3. collecting 阶段：上传联邦统计 (n, sum, sum_sq) → 平台聚合全局标准化器；
4. running 阶段：每轮拉取全局模型 → 本地训练（DP 可选）→ 上传加密参数 + 本地 AUC；
5. done：打印联邦结果退出；failed/异常：非零退出码。

容错：全部 API 调用经指数退避重试（医院内网瞬时抖动/平台短暂重启不杀节点，
POST 靠平台 409 幂等语义保证重试安全）；凭证无效（401）类错误不重试直接退出。

HTTP 用标准库 urllib（Agent 零第三方 HTTP 依赖）。
"""

from __future__ import annotations

import http.client
import json
import ssl
import sys
import time
import urllib.error
import urllib.request

import numpy as np

from node_agent import config, tls
from node_agent.crypto import seal_envelope
from node_agent.trainer import (FEATURES, local_auc, load_csv, moments,
                                split_train_test, standardize, train_round)
from node_agent.model import dp_sanitize

# Agent 出站恒直连：禁用系统代理（医院内网直通地址，经代理会被劫持/拒绝）
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _opener(context: ssl.SSLContext | None) -> urllib.request.OpenerDirector:
    """无代理 opener；mTLS 时挂载自定义 SSLContext（OpenerDirector.open
    不支持 context 参数，必须经 HTTPSHandler 注入）。"""
    if context is None:
        return _OPENER
    return urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                       urllib.request.HTTPSHandler(context=context))


def _api(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    """节点侧 API 调用：恒携带 X-Node-Id / X-Node-Token 机器凭证。

    TLS/mTLS：配置了 TLS_CA_CERT/TLS_CLIENT_CERT 时通过自定义 SSLContext 发起
    （CA 验证服务端 + 客户端证书双向认证）；默认走系统 CA 的单向 TLS。
    恒直连不走系统代理（coordinator 多为内网直通地址，经代理转发会被劫持/拒绝）。
    """
    url = config.COORDINATOR_URL.rstrip("/") + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Node-Id", config.NODE_ID)
    req.add_header("X-Node-Token", config.NODE_TOKEN)
    context = tls.ssl_context() if url.startswith("https://") else None
    try:
        with _opener(context).open(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return e.code, {}
    except (urllib.error.URLError, ConnectionError, OSError,
            http.client.HTTPException) as e:
        # 瞬时网络故障（拒连/重置/超时/半途断开）统一归一为 ConnectionError，
        # 交由上层 _api_retry 指数退避重试——单次抖动不应杀死医院内网节点
        raise ConnectionError(f"无法连接 coordinator {url}: {e}") from e


def discover_job() -> str | None:
    """自动发现本节点被指派的进行中任务（collecting/running 优先）。"""
    _, jobs = _api("GET", "/api/federation/nodes/my-jobs")
    active = [j for j in jobs if j.get("status") in ("collecting", "running")]
    return active[0]["id"] if active else None


def _api_retry(method: str, path: str, body: dict | None = None,
               max_wait: float = 900.0) -> tuple[int, dict]:
    """瞬时网络故障指数退避重试（1s 起步、翻倍封顶 15s，默认最长等待 15 分钟）。

    医院内网抖动/平台检修窗口不应让节点退出——断点续训语义下节点只需持续
    重试到 coordinator 恢复。重试对 POST 安全：moments/update 重复提交均被
    平台按 409 幂等语义处理，业务等价成功。
    """
    delay, waited = 1.0, 0.0
    while True:
        try:
            return _api(method, path, body)
        except ConnectionError as e:
            if waited >= max_wait:
                raise
            print(f"[节点] coordinator 暂不可达（{e}），{delay:.0f}s 后重试"
                  f"（已等待 {waited:.0f}s）", file=sys.stderr, flush=True)
            time.sleep(delay)
            waited += delay
            delay = min(delay * 2, 15.0)


def run(job_id: str | None = None) -> int:
    """参与一次联邦训练直到任务完成。返回进程退出码。"""
    missing = config.validate()
    if missing:
        print(f"[节点] 缺少必填环境变量: {missing}", file=sys.stderr)
        return 2
    if not job_id:
        job_id = config.NODE_JOB_ID or discover_job()
    if not job_id:
        print("[节点] 未发现被指派的联邦任务（/my-jobs 为空）", file=sys.stderr)
        return 2

    print(f"[节点] 加载本院数据 {config.DATA_PATH}（仅本地内存，永不上行）")
    X, y = load_csv(config.DATA_PATH)
    X_tr, y_tr, X_te, y_te = split_train_test(X, y, test_ratio=config.TEST_RATIO)
    print(f"[节点] 训练集 {len(y_tr)} 行 / 验证集 {len(y_te)} 行 / {len(FEATURES)} 特征")

    moments_sent = False
    submitted_rounds: set[int] = set()

    while True:
        status, asg = _api_retry("GET", f"/api/federation/nodes/jobs/{job_id}/assignment")
        if status == 401:
            print(f"[节点] 凭证无效: {asg.get('detail')}", file=sys.stderr)
            return 2
        if status == 404:
            print(f"[节点] 任务 {job_id} 不存在", file=sys.stderr)
            return 2

        job_status = asg.get("status")
        if job_status == "done":
            result = asg.get("result") or {}
            print(f"[节点] 联邦训练完成：加权 AUC={result.get('final_auc')} "
                  f"(rounds={asg.get('rounds')}, 存证={str(asg.get('event_hash'))[:16]}…)")
            return 0
        if job_status == "failed":
            print(f"[节点] 任务失败: {result_err}" if (result_err := asg.get("result")) else "[节点] 任务失败",
                  file=sys.stderr)
            return 1

        round_no = int(asg.get("round") or 0)

        if job_status == "collecting":
            if not moments_sent:
                payload = seal_envelope(moments(X))
                code, resp = _api_retry("POST", f"/api/federation/nodes/jobs/{job_id}/moments",
                                        {"payload": payload})
                if code in (200, 409):  # 409 = 已提交过（断点重连幂等）
                    moments_sent = True
                    print(f"[节点] 已上传联邦统计（n={len(y)}，仅聚合量）")
                else:
                    print(f"[节点] 统计上传被拒({code}): {resp.get('detail')}", file=sys.stderr)
                    return 1
            time.sleep(config.POLL_INTERVAL)
            continue

        if job_status == "running" and round_no not in submitted_rounds:
            coef = np.array(asg["global_coef"], dtype=float)
            mean = np.array(asg["scaler"]["mean"], dtype=float)
            std = np.array(asg["scaler"]["std"], dtype=float)
            Xs_tr = standardize(X_tr, mean, std)
            Xs_te = standardize(X_te, mean, std)

            new_coef = train_round(Xs_tr, y_tr, coef, epochs=int(asg.get("local_epochs", 3)))
            update = new_coef - coef
            sigma = float(asg.get("dp_sigma") or 0.0)
            if sigma > 0:
                update = dp_sanitize(update, float(asg.get("clip_norm", 1.0)), sigma,
                                     np.random.default_rng(config.DP_SEED + round_no))
            state = (coef + update).tolist()

            payload = seal_envelope({
                "coef": state,
                "n": int(len(y_tr)),
                "local_auc": round(local_auc(Xs_te, y_te, np.array(state)), 6),
            })
            code, resp = _api_retry("POST", f"/api/federation/nodes/jobs/{job_id}/rounds/{round_no}/update",
                                    {"payload": payload})
            if code == 200:
                submitted_rounds.add(round_no)
                print(f"[节点] 第 {round_no}/{asg.get('rounds')} 轮参数已加密上传"
                      f"（信封 {len(payload)} 字节，无原始数据）")
            elif code == 409:  # 断点重连：本轮已提交过，幂等跳过
                submitted_rounds.add(round_no)
                print(f"[节点] 第 {round_no} 轮参数此前已提交，跳过")
            else:
                print(f"[节点] 参数上传被拒({code}): {resp.get('detail')}", file=sys.stderr)
                return 1

        time.sleep(config.POLL_INTERVAL)


if __name__ == "__main__":
    sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else None))
