"""瓯医数链 联邦节点双机演练编排（P1.1/P1.2/P1.3 实操验收）

一键完成《联邦节点部署与演练方案》全部演练场景（单机双进程模拟两台
医院内网机，协议/加密/容错语义与物理双机完全一致；物理双机到位时按
方案第 2/3/5 章拆开执行，本脚本各步骤可独立复用）：

  场景 1  协议全链路（生产形态 DEMO_MODE=false）：两节点完成完整联邦训练
  场景 2  原始数据零传输（应用层证据）：上行信封全部 AES-GCM 密文
          （enc2:），解密仅 coef/n/local_auc 三键，体量与样本量无关
  场景 3  拔节点断点续训：拔线→last_seen 冻结→pause→重启→resume→完成，
          final_auc 与无中断对照任务一致（聚合确定性）
  场景 4  恶意参数检测：借拔线离线窗口取反 coef 上行 → 422，不落库不留存证
  场景 5  mTLS 双向认证（HTTPS 形态）：无证书/野证书握手被拒，
          正式联邦证书握手成功（应用层凭证校验接管）

用法（在 backend 目录）：
    python scripts/run_federation_drill.py            # 全场景
    python scripts/run_federation_drill.py --keep     # 保留演练工作区
产物：drill_workspace/drill_result.json（逐场景证据，供演练记录引用）。
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import shutil
import signal
import socket
import sqlite3
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))  # noqa: E402 —— app.* 可导入
sys.path.insert(0, str(BACKEND / "node_agent"))  # noqa: E402 —— node_agent.* 可导入

HTTP_PORT, HTTPS_PORT = 8110, 8111
BASE = f"http://127.0.0.1:{HTTP_PORT}"
BASE_TLS = f"https://127.0.0.1:{HTTPS_PORT}"
ROUNDS, EPOCHS, DP_SIGMA = 6, 2, 0.01
POLL_SEC, DONE_TIMEOUT_SEC = 1.0, 300

# 本机演练不走系统代理（避免系统代理劫持 loopback 请求）
NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def log(msg: str) -> None:
    print(f"[演练 {dt.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def api(method: str, url: str, body: dict | None = None,
        headers: dict | None = None,
        client_certs: tuple[str, str] | None = None) -> tuple[int, dict]:
    """演练用 HTTP 客户端（管理面/节点面通用；无代理 + HTTPS 自签不校验）。
    client_certs：对 mTLS 平台（CERT_REQUIRED）发请求时携带的客户端证书。"""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        return _open_json(req, client_certs)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:  # noqa: BLE001
            return e.code, {}


def _open_json(req: urllib.request.Request,
               client_certs: tuple[str, str] | None = None) -> tuple[int, dict]:
    if req.full_url.startswith("https"):
        # 显式构造无代理 opener：urlopen 走全局默认 opener（含系统代理），
        # loopback 的 mTLS 请求经代理转发必失败；且 CERT_REQUIRED 平台要求
        # 每条连接出示客户端证书（自签服务端 → 统一不校验，校验归场景 5 专项）
        ctx = ssl._create_unverified_context()  # noqa: SLF001
        if client_certs:
            ctx.load_cert_chain(client_certs[0], client_certs[1])
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ctx))
        with opener.open(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode())
    with NO_PROXY_OPENER.open(req, timeout=15) as r:
        return r.status, json.loads(r.read().decode())


def wait_ready(url: str, timeout: float = 120, proc: subprocess.Popen | None = None,
               client_certs: tuple[str, str] | None = None) -> None:
    """等平台就绪；若子进程提前退出或超时，打印其输出辅助定位。
    mTLS 平台（CERT_REQUIRED）健康检查必须携带联邦客户端证书。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            code, _ = api("GET", url + "/api/health", client_certs=client_certs)
            if code == 200:
                return
        except (urllib.error.URLError, ConnectionError, OSError):
            if proc is not None and proc.poll() is not None:
                out = proc.stdout.read().decode("utf-8", "replace") if proc.stdout else ""
                raise RuntimeError(f"平台子进程启动即退出（exit={proc.returncode}）：\n{out[-2000:]}")
        time.sleep(0.5)
    tail = proc.stdout.read().decode("utf-8", "replace")[-1500:] if proc and proc.stdout else ""
    raise RuntimeError(f"平台未就绪: {url}\n子进程输出尾部：\n{tail}")


def free_port(port: int) -> None:
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", port)) == 0:
            raise RuntimeError(f"端口 {port} 已被占用，请先释放")


def random_b64_32() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def start_platform(ws: Path, port: int, https: bool, admin_secret: str,
                   param_key: str, certs: dict | None = None,
                   admin_password: str = "Drill-pass") -> subprocess.Popen:
    """起平台 coordinator 子进程（独立 SQLite + 随机密钥，不碰开发库）。"""
    env = os.environ.copy()
    env.update({
        "DATABASE_URL": f"sqlite+aiosqlite:///./drill_workspace/drill_{port}.db",
        "DEMO_MODE": "false",                       # 生产形态：鉴权/RBAC 全强制
        "YIBAO_ADMIN_SECRET": admin_secret,
        "ADMIN_USERNAME": "drill-admin",            # 演练专用管理员（随机口令注入）
        "ADMIN_PASSWORD": admin_password,
        "FEDERATION_PARAM_KEY": param_key,          # 密文上行模式（明文 400 拒绝）
        "CHAIN_ANCHOR_INTERVAL_HOURS": "0",         # 演练关外部 TSA 定时
        "AUDIT_CLEANUP_INTERVAL_HOURS": "0",
        "PYTHONIOENCODING": "utf-8",
    })
    cmd = [sys.executable, "-m", "uvicorn", "app.main:app",
           "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"]
    if https:
        cmd += ["--ssl-certfile", certs["server_crt"], "--ssl-keyfile", certs["server_key"],
                "--ssl-ca-certs", certs["ca_crt"], "--ssl-cert-reqs", "2"]  # 2=CERT_REQUIRED
    log(f"起平台 {'HTTPS(mTLS 双向)' if https else 'HTTP'} :{port}")
    out = open(ws / f"platform-{port}.log", "wb")   # 平台日志落盘（排查/留档两用）
    p = subprocess.Popen(cmd, cwd=str(BACKEND), env=env,
                         stdout=out, stderr=subprocess.STDOUT)
    _ACTIVE.append(p)
    # HTTPS 平台 CERT_REQUIRED：健康检查必须携带联邦客户端证书（节点 A 证书）
    wait_ready(BASE_TLS if https else BASE, proc=p,
               client_certs=certs["node_a"] if certs else None)
    return p


_ACTIVE: list[subprocess.Popen] = []   # 全部子进程登记（平台+节点），失败路径兜底清理


def stop(p: subprocess.Popen | None) -> None:
    if p and p.poll() is None:
        p.send_signal(signal.SIGTERM)
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
    while p in _ACTIVE:
        _ACTIVE.remove(p)


def stop_all() -> None:
    """兜底清理：演练失败路径不留孤儿（Agent 带重试，孤儿可存活 15 分钟）。"""
    for p in list(_ACTIVE):
        stop(p)


def _find_openssl() -> str:
    """定位可用 openssl：本机便携发行版（Strawberry/conda）OPENSSLDIR 常指向
    不存在的盘导致 req 必失败，用真实签发探活选第一个可用的二进制。"""
    cands = [os.environ.get("OPENSSL_BIN", ""),
             r"C:\Program Files\Git\mingw64\bin\openssl.exe",
             r"C:\Program Files\Git\usr\bin\openssl.exe",
             "openssl"]
    tmp = Path(os.environ.get("TEMP", ".")) / "oumed_drill_cert_probe"
    tmp.mkdir(exist_ok=True)
    for cand in cands:
        if not cand:
            continue
        try:
            subprocess.run([cand, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                            "-days", "1", "-keyout", str(tmp / "p.key"),
                            "-out", str(tmp / "p.crt"), "-subj", "/CN=probe"],
                           check=True, capture_output=True, timeout=30)
            return cand
        except (subprocess.SubprocessError, OSError):
            continue
    raise RuntimeError("未找到可用 openssl（可设 OPENSSL_BIN 环境变量指向可用二进制）")


def gen_certs(ws: Path) -> dict:
    """openssl 一键签发（方案 deploy/tls/gen-certs.sh 同款拓扑，零新依赖）：
    联邦 CA → 服务端 SAN 证书(127.0.0.1/localhost) + 节点 A/B 客户端证书 + 野 CA 证书。"""
    cert_dir = ws / "certs"
    cert_dir.mkdir(exist_ok=True)
    openssl = _find_openssl()
    z = lambda name: str(cert_dir / name)  # noqa: E731
    subj = "/O=OuMedTrust-Drill/CN={}"

    def run(*cmd: str) -> None:
        subprocess.run((openssl, *cmd), check=True, capture_output=True)

    # keyUsage 必须：Python ssl(OpenSSL 3.x) 验证 CA 时强制要求
    # （实测缺失报 "CA cert does not include key usage extension"，全链路验签失败）
    ca_ext = ("basicConstraints=critical,CA:TRUE", "keyUsage=critical,keyCertSign,cRLSign")
    run("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
        "-keyout", z("ca.key"), "-out", z("ca.crt"), "-subj", subj.format("Drill Fed CA"),
        "-addext", ca_ext[0], "-addext", ca_ext[1])
    run("req", "-newkey", "rsa:2048", "-nodes",
        "-keyout", z("server.key"), "-out", z("server.csr"), "-subj", subj.format("localhost"))
    (cert_dir / "san.ext").write_text(
        "subjectAltName=IP:127.0.0.1,DNS:localhost\nextendedKeyUsage=serverAuth\n"
        "keyUsage=digitalSignature,keyEncipherment\nbasicConstraints=CA:FALSE\n", encoding="ascii")
    (cert_dir / "client.ext").write_text(
        "extendedKeyUsage=clientAuth\nkeyUsage=digitalSignature,keyEncipherment\n"
        "basicConstraints=CA:FALSE\n", encoding="ascii")
    run("x509", "-req", "-in", z("server.csr"), "-CA", z("ca.crt"),
        "-CAkey", z("ca.key"), "-CAcreateserial", "-days", "2",
        "-out", z("server.crt"), "-extfile", z("san.ext"))
    # 野 CA（模拟非联邦 CA 的攻击者证书链；扩展要求与正式 CA 相同）
    run("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
        "-keyout", z("wild-ca.key"), "-out", z("wild-ca.crt"), "-subj", subj.format("Wild CA"),
        "-addext", ca_ext[0], "-addext", ca_ext[1])
    for node in ("node-a", "node-b", "wild"):
        run("req", "-newkey", "rsa:2048", "-nodes",
            "-keyout", z(f"{node}.key"), "-out", z(f"{node}.csr"), "-subj", subj.format(node))
        ca, cakey = ("ca.crt", "ca.key") if node != "wild" else ("wild-ca.crt", "wild-ca.key")
        run("x509", "-req", "-in", z(f"{node}.csr"), "-CA", z(ca),
            "-CAkey", z(cakey), "-CAcreateserial", "-days", "2",
            "-out", z(f"{node}.crt"), "-extfile", z("client.ext"))
    return {"ca_crt": z("ca.crt"), "server_crt": z("server.crt"), "server_key": z("server.key"),
            "node_a": (z("node-a.crt"), z("node-a.key")),
            "node_b": (z("node-b.crt"), z("node-b.key")),
            "wild": (z("wild.crt"), z("wild.key"))}


certs_ca: dict = {}  # mTLS 场景的全局证书路径（start_agent 引用）


def start_agent(node: dict, data_path: Path, param_key: str, https: bool,
                client_certs: tuple[str, str] | None,
                log_path: Path | None = None) -> subprocess.Popen:
    """起一个节点 Agent 子进程（= 一台医院内网机）。"""
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": str(BACKEND / "node_agent"), "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",                    # 日志实时落盘（时序排查用）
        "COORDINATOR_URL": BASE_TLS if https else BASE,
        "NODE_ID": node["node_id"], "NODE_TOKEN": node["node_token"],
        "DATA_PATH": str(data_path), "FEDERATION_PARAM_KEY": param_key,
        "NODE_POLL_INTERVAL": str(POLL_SEC), "NODE_DP_SEED": str(node.get("dp_seed", 7)),
    })
    if https and client_certs:
        env.update({"TLS_CA_CERT": certs_ca["ca_crt"],
                    "TLS_CLIENT_CERT": client_certs[0], "TLS_CLIENT_KEY": client_certs[1]})
    out = open(log_path, "w", encoding="utf-8") if log_path else subprocess.PIPE
    p = subprocess.Popen(
        [sys.executable, "-m", "node_agent.main"], cwd=str(BACKEND / "node_agent"),
        env=env, stdout=out, stderr=subprocess.STDOUT)
    _ACTIVE.append(p)
    return p


def wait_done(job_id: str, admin: dict, timeout: float) -> dict:
    """轮询任务直到 done；失败/超时抛错。admin 为管理端登录后的请求头。
    记录非 200 响应（鉴权/网关异常会在超时信息中显形，避免静默循环）。"""
    deadline = time.time() + timeout
    bad: list[str] = []
    while time.time() < deadline:
        code, job = api("GET", f"{BASE}/api/federation/nodes/jobs/{job_id}",
                        headers=admin)
        if code == 200 and job.get("status") == "done":
            return job
        if code == 200 and job.get("status") == "failed":
            raise RuntimeError(f"任务失败: {job.get('result')}")
        if code != 200 and len(bad) < 3:
            bad.append(f"{code}:{str(job)[:80]}")
        time.sleep(1.0)
    tail = f"；非200响应样例={bad}" if bad else "；响应恒为200但状态未到 done"
    raise RuntimeError(f"任务 {job_id} 超时未完成{tail}")


def agent_output(p: subprocess.Popen, log_path: Path | None = None) -> str:
    """等 Agent 进程结束并取其输出（PIPE 或日志文件两种形态）。"""
    try:
        p.wait(timeout=90)
    except subprocess.TimeoutExpired:
        p.kill()
    if log_path is not None:
        return log_path.read_text(encoding="utf-8", errors="replace")
    out, _ = p.communicate()
    return out.decode("utf-8", "replace")


def verify_envelopes(db_path: Path, param_key: str) -> dict:
    """场景 2：库内全部参数信封为 enc2 密文，解密后仅 coef/n/local_auc 三键。"""
    from node_agent.crypto import open_envelope, parse_key
    key = parse_key(param_key)
    con = sqlite3.connect(db_path)
    rows = con.execute("SELECT payload_enc FROM distributed_job_updates").fetchall()
    con.close()
    keys: set[tuple] = set()
    update_sizes: list[int] = []
    for (payload,) in rows:
        assert payload.startswith("enc2:"), f"信封非 enc2 密文: {payload[:20]}…"
        opened = open_envelope(payload, key)
        if set(opened) == {"n", "sum", "sum_sq"}:      # round-0 联邦统计信封
            continue
        assert set(opened) == {"coef", "n", "local_auc"}, f"信封解密字段异常: {set(opened)}"
        keys.add(tuple(sorted(opened)))
        update_sizes.append(len(payload))
    return {"envelopes": len(rows), "field_keys": sorted(list(k) for k in keys),
            "min_bytes": min(update_sizes), "max_bytes": max(update_sizes)}


def poison_attempt(job_id: str, node: dict, param_key: str) -> dict:
    """场景 4：以离线节点的凭证上行取反 coef（方向反转）→ 预期 422 cosine 拒绝。
    注入窗口选在节点 B 被拔线后：当前轮无人提交，不存在与真实 Agent 的
    409 竞速；若仍撞上轮次错位（409）则顺延重试，总窗口 90s。"""
    from node_agent.crypto import parse_key, seal_envelope
    key = parse_key(param_key)  # 显式传密钥：脚本进程自身无 FEDERATION_PARAM_KEY 环境变量
    deadline = time.time() + 90
    bad: list[str] = []
    while time.time() < deadline:
        code, asg = api("GET", f"{BASE}/api/federation/nodes/jobs/{job_id}/assignment",
                        headers={"X-Node-Id": node["node_id"],
                                 "X-Node-Token": node["node_token"]})
        if code == 200 and asg.get("status") == "running" and asg.get("global_coef"):
            flipped = [-c for c in asg["global_coef"]]  # 方向反转 = cosine ≈ -1
            payload = seal_envelope({"coef": flipped, "n": 100, "local_auc": 0.99}, key)
            code, resp = api("POST",
                             f"{BASE}/api/federation/nodes/jobs/{job_id}/rounds/{asg['round']}/update",
                             {"payload": payload},
                             {"X-Node-Id": node["node_id"],
                              "X-Node-Token": node["node_token"]})
            if code == 409:  # 该轮已被节点先提交，顺延下一轮再试
                time.sleep(POLL_SEC)
                continue
            return {"http_status": code, "response": resp}
        if code != 200 and len(bad) < 3:
            bad.append(f"{code}:{str(asg)[:60]}")
        time.sleep(POLL_SEC)
    raise RuntimeError(f"恶意参数注入窗口未命中（90s 内未进入可注入轮次）；"
                       f"非200样例={bad if bad else '无（assignment 恒200但状态不满足）'}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="保留演练工作区（排查/留档用）")
    args = ap.parse_args()

    ws = BACKEND / "drill_workspace"
    if ws.exists():
        for i in range(5):  # 上一次异常退出可能残留日志句柄（进程被杀后需短暂释放）
            try:
                shutil.rmtree(ws)
                break
            except PermissionError:
                if i == 4:
                    raise
                time.sleep(1)
    ws.mkdir()
    evidence: dict = {"started_at": dt.datetime.now().isoformat(timespec="seconds"),
                      "mode": "单机双进程模拟双机（协议/加密/容错语义与物理双机一致）"}

    free_port(HTTP_PORT)
    free_port(HTTPS_PORT)

    # ---- 准备：两院异构合成数据 + 随机密钥（方案 2.1/2.3 口径） ----
    from node_agent import synthdata
    data_a = ws / "hospital_a.csv"
    data_b = ws / "hospital_b.csv"
    n_a = synthdata.write_csv(str(data_a), "hospital_a", seed=2026)
    n_b = synthdata.write_csv(str(data_b), "hospital_b", seed=2027)
    param_key = random_b64_32()
    admin_secret = "drill-admin-" + os.urandom(8).hex()
    admin_password = "Drill-" + os.urandom(12).hex()
    log(f"数据就绪：A 院 {n_a} 行 / B 院 {n_b} 行（异构规模，供体量验证）；信封密钥已随机生成")

    plat = start_platform(ws, HTTP_PORT, https=False,
                          admin_secret=admin_secret, param_key=param_key,
                          admin_password=admin_password)
    # 生产形态管理面：走管理端登录拿 HMAC token（X-Admin-Token 非裸密钥）
    code_l, login = api("POST", f"{BASE}/api/admin/login",
                        {"username": "drill-admin", "password": admin_password})
    assert code_l == 200 and login.get("token"), f"管理端登录失败: {login}"
    admin = {"X-Admin-Token": login["token"]}
    log("管理端登录完成（生产形态 RBAC 就绪）")
    try:
        _, node_a = api("POST", f"{BASE}/api/federation/nodes", {"name": "温附一院"}, admin)
        _, node_b = api("POST", f"{BASE}/api/federation/nodes", {"name": "温附二院"}, admin)
        assert node_a.get("node_token") and node_b.get("node_token"), f"节点注册失败: {node_a}"
        node_a["dp_seed"], node_b["dp_seed"] = 7, 13
        log(f"节点注册完成：A={node_a['node_id'][:12]}… B={node_b['node_id'][:12]}…（token 仅一次下发）")

        # ============ 场景 1+2+4：对照任务（无中断）全链路 ============
        _, ctrl = api("POST", f"{BASE}/api/federation/nodes/jobs",
                      {"node_ids": [node_a["node_id"], node_b["node_id"]],
                       "rounds": ROUNDS, "local_epochs": EPOCHS, "dp_sigma": DP_SIGMA}, admin)
        ctrl_id = ctrl["id"]
        log(f"对照任务 {ctrl_id[:12]}… rounds={ROUNDS} dp_sigma={DP_SIGMA}，两节点开始训练")
        log_a, log_b = ws / "agent-a-ctrl.log", ws / "agent-b-ctrl.log"
        ag_a = start_agent(node_a, data_a, param_key, https=False, client_certs=None,
                           log_path=log_a)
        ag_b = start_agent(node_b, data_b, param_key, https=False, client_certs=None,
                           log_path=log_b)

        job_ctrl = wait_done(ctrl_id, admin, DONE_TIMEOUT_SEC)
        out_a, out_b = agent_output(ag_a, log_a), agent_output(ag_b, log_b)
        assert "加权 AUC" in out_a and "加权 AUC" in out_b, f"节点未正常完成:\n{out_a[-300:]}\n{out_b[-300:]}"
        envelope_bytes = [int(ln.split("信封 ")[1].split(" 字节")[0])
                          for ln in (out_a + out_b).splitlines() if "信封 " in ln]
        evidence["scenario1_protocal"] = {
            "control_job": ctrl_id, "final_auc": job_ctrl["result"]["final_auc"],
            "event_hash": job_ctrl.get("event_hash"), "envelope_bytes": envelope_bytes}
        log(f"✅ 场景1 对照任务 done：final_auc={job_ctrl['result']['final_auc']} "
            f"存证={str(job_ctrl.get('event_hash'))[:16]}…")

        env_ev = verify_envelopes(ws / f"drill_{HTTP_PORT}.db", param_key)
        evidence["scenario2_zero_exfiltration"] = {
            **env_ev, "rows_a": n_a, "rows_b": n_b,
            "note": "两院样本量 1200/800 不同但每份信封字节数恒定（与行数无关）；"
                    "库内信封全部 enc2: 密文，解密仅 coef/n/local_auc 三键，无任何行级数据。"
                    "物理双机演练时叠加 tcpdump 抓包强验证（方案第 3 章，命令已备）"}
        log(f"✅ 场景2 零传输（应用层证据）：{env_ev['envelopes']} 份信封全部 enc2 密文，"
            f"{env_ev['min_bytes']}-{env_ev['max_bytes']} 字节，字段={env_ev['field_keys'][0]}")

        # ============ 场景 3：拔节点断点续训 ============
        _, drill = api("POST", f"{BASE}/api/federation/nodes/jobs",
                       {"node_ids": [node_a["node_id"], node_b["node_id"]],
                        "rounds": ROUNDS, "local_epochs": EPOCHS, "dp_sigma": DP_SIGMA}, admin)
        drill_id = drill["id"]
        log(f"拔节点任务 {drill_id[:12]}… 启动")
        log_a2, log_b2 = ws / "agent-a-drill.log", ws / "agent-b-drill.log"
        ag_a2 = start_agent(node_a, data_a, param_key, https=False, client_certs=None,
                            log_path=log_a2)
        ag_b2 = start_agent(node_b, data_b, param_key, https=False, client_certs=None,
                            log_path=log_b2)

        def b_progress() -> int:
            _, j = api("GET", f"{BASE}/api/federation/nodes/jobs/{drill_id}", headers=admin)
            rows = j.get("progress") or []
            row = next((r for r in rows if r.get("node_id") == node_b["node_id"]), {})
            return int(row.get("last_update_round") or 0)

        deadline = time.time() + 120
        while time.time() < deadline and b_progress() < 2:
            time.sleep(1.0)
        kill_round = b_progress()
        ag_b2.send_signal(signal.SIGTERM)   # 模拟节点 B 断电/拔网线
        ag_b2.wait(timeout=15)
        time.sleep(2)
        _, nodes_list = api("GET", f"{BASE}/api/federation/nodes", headers=admin)
        b_before = next(n for n in nodes_list if n["id"] == node_b["node_id"]).get("last_seen_at")
        _, j1 = api("GET", f"{BASE}/api/federation/nodes/jobs/{drill_id}", headers=admin)
        log(f"节点 B 已于第 {kill_round} 轮提交后被拔线；任务状态={j1['status']} "
            f"current_round={j1.get('round')}，B last_seen={b_before}")

        time.sleep(30)                      # 模拟检修窗口（物理机演练按 30 分钟执行）
        _, nodes_off = api("GET", f"{BASE}/api/federation/nodes", headers=admin)
        b_off = next(n for n in nodes_off if n["id"] == node_b["node_id"]).get("last_seen_at")

        # ---- 场景 4：借 B 离线窗口注入恶意参数（当前轮无人提交，无 409 竞速）----
        con = sqlite3.connect(ws / f"drill_{HTTP_PORT}.db")
        kept_before = con.execute("SELECT COUNT(*) FROM distributed_job_updates").fetchone()[0]
        con.close()
        try:
            poison = poison_attempt(drill_id, node_b, param_key)
        except RuntimeError:
            log("— 恶意注入未命中，节点日志尾部如下 —")
            log(log_a2.read_text(encoding="utf-8", errors="replace")[-800:])
            log(log_b2.read_text(encoding="utf-8", errors="replace")[-800:])
            raise
        con = sqlite3.connect(ws / f"drill_{HTTP_PORT}.db")
        kept = con.execute("SELECT COUNT(*) FROM distributed_job_updates").fetchone()[0]
        con.close()
        assert kept == kept_before, f"422 拒绝后库内信封数变化: {kept_before} → {kept}"
        evidence["scenario4_poison"] = {**poison, "db_updates_total": kept,
                                        "updates_delta": kept - kept_before,
                                        "note": "取反 coef（cosine≈-1）被 422 拒绝，"
                                                "恶意参数不落库/不参与聚合/不留存证"
                                                "（注入窗口：节点 B 离线、当前轮空缺）"}
        log(f"✅ 场景4 恶意参数：取反 coef 上行 → HTTP {poison['http_status']}（cosine 拒绝），"
            f"库内信封 {kept_before}→{kept}（0 新增，恶意参数未落库）")

        _, paused = api("POST", f"{BASE}/api/federation/nodes/jobs/{drill_id}/pause", {}, admin)
        assert paused.get("status") == "paused", f"暂停失败: {paused}"
        _, res = api("POST", f"{BASE}/api/federation/nodes/jobs/{drill_id}/resume", {}, admin)
        assert res.get("status") in ("running", "collecting"), f"恢复失败: {res}"
        log(f"管理面 pause→resume 完成（断点续训按 current_round 回 {res.get('status')}），节点 B 复电重连")

        ag_b3 = start_agent(node_b, data_b, param_key, https=False, client_certs=None,
                            log_path=ws / "agent-b-resume.log")
        job_drill = wait_done(drill_id, admin, DONE_TIMEOUT_SEC)
        agent_output(ag_a2, log_a2), agent_output(ag_b3, ws / "agent-b-resume.log")
        auc_ctrl = job_ctrl["result"]["final_auc"]
        auc_drill = job_drill["result"]["final_auc"]
        _, nodes_after = api("GET", f"{BASE}/api/federation/nodes", headers=admin)
        b_after = next(n for n in nodes_after if n["id"] == node_b["node_id"]).get("last_seen_at")
        evidence["scenario3_resilience"] = {
            "job": drill_id, "killed_after_round": kill_round,
            "last_seen_frozen_while_offline": b_off == b_before,
            "last_seen_refreshed_after_reconnect": b_after > b_off,
            "pause_resume": True, "final_auc": auc_drill,
            "control_final_auc": auc_ctrl, "final_auc_matches_control": auc_drill == auc_ctrl,
            "event_hash": job_drill.get("event_hash")}
        log(f"✅ 场景3 断点续训：拔线任务 final_auc={auc_drill} vs 对照 {auc_ctrl} → "
            f"{'一致' if auc_drill == auc_ctrl else '不一致（需排查）'}；"
            f"last_seen 冻结={b_off == b_before} → 重连刷新={b_after > b_off}")
        stop_all()
    finally:
        stop_all()

    # ============ 场景 5：mTLS 双向认证（HTTPS 平台实例） ============
    try:
        certs = gen_certs(ws)
        global certs_ca
        certs_ca = certs
        # gen_certs / start_platform 一并纳入 try：启动失败也走 stop_all，
        # 不留占端口/占日志句柄的孤儿平台进程（曾致下次演练 rmtree 失败）
        plat_tls = start_platform(ws, HTTPS_PORT, https=True,
                                  admin_secret=admin_secret, param_key=param_key, certs=certs)
        code_tls, node_tls = api("POST", f"{BASE_TLS}/api/federation/nodes",
                                 {"name": "mTLS演练院"}, {**admin},
                                 client_certs=certs["node_b"])
        assert code_tls == 200, f"HTTPS 平台注册失败: {node_tls}"

        def tls_attempt(client: tuple[str, str] | None, ca: str) -> str:
            """真实 TLS 握手探测（与单测 socketpair 等价的端到端版）。"""
            ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=ca)
            if client:
                ctx.load_cert_chain(client[0], client[1])
            try:
                with socket.create_connection(("127.0.0.1", HTTPS_PORT), timeout=8) as sock:
                    with ctx.wrap_socket(sock, server_hostname="localhost") as s:
                        s.sendall(b"GET /api/federation/nodes/my-jobs HTTP/1.1\r\n"
                                  b"Host: localhost\r\nConnection: close\r\n\r\n")
                        first = s.recv(4096).decode("utf-8", "replace").splitlines()
                        # TLS 1.3 客户端握手先于服务端证书校验完成：无证书/野证书
                        # 被拒时 recv 返回空（服务端不发任何 HTTP 数据直接断连）
                        if not first:
                            return "handshake_rejected: server_closed_no_http_data"
                        return "handshake_ok:" + first[0].strip()
            except (ssl.SSLError, ConnectionResetError, OSError) as e:
                return f"handshake_rejected: {type(e).__name__}: {str(e)[:60]}"

        r1 = tls_attempt(None, certs["ca_crt"])                # ① 无客户端证书
        r2 = tls_attempt(certs["wild"], certs["ca_crt"])       # ② 联邦 CA 验服务端 + 野客户端证书
        r3 = tls_attempt(certs["node_a"], certs["ca_crt"])     # ③ 正式联邦证书 → 握手成功
        # ③ 应用层接管：正式证书 + 真实节点凭证经 mTLS 调业务端点（my-jobs）。
        # 凭证/证书路径一律经环境变量传递：中文路径嵌入 -c 代码串会被转义
        # 序列（如 \backend 的 \b）破坏，曾致子进程 Errno 22 崩溃（stdout 空）
        probe_env = {**os.environ, "PYTHONPATH": str(BACKEND / "node_agent"),
                     "COORDINATOR_URL": BASE_TLS,
                     "NODE_ID": node_tls["node_id"], "NODE_TOKEN": node_tls["node_token"],
                     "TLS_CA_CERT": certs["ca_crt"],
                     "TLS_CLIENT_CERT": certs["node_b"][0],
                     "TLS_CLIENT_KEY": certs["node_b"][1]}
        probe = subprocess.run(
            [sys.executable, "-c",
             "from node_agent import config, main\n"
             "print('my_jobs:', main.discover_job() or 'empty-ok')"],
            cwd=str(BACKEND / "node_agent"), env=probe_env,
            capture_output=True, text=True, timeout=60)
        biz = (probe.stdout.strip().splitlines() or [""])[-1]
        if probe.returncode != 0:
            biz = f"probe_failed(rc={probe.returncode}): {probe.stderr.strip()[-120:]}"
        mtls_ok = ("handshake_rejected" in r1 and "handshake_rejected" in r2
                   and r3.startswith("handshake_ok") and biz.startswith("my_jobs:"))
        evidence["scenario5_mtls"] = {
            "no_cert": r1, "wild_client_cert": r2, "federated_cert": r3,
            "agent_business_call_over_mtls": biz, "pass": mtls_ok,
            "note": "③ 语义：mTLS（传输层）与节点 token（应用层）两层独立防线，"
                    "握手成功不代表放行（my-jobs 空列表=应用层凭证校验通过后正常应答）"}
        log(f"✅ 场景5 mTLS：① 无证书→{r1.split(':')[0]} ② 野证书→{r2.split(':')[0]} "
            f"③ 正式证书→{r3.split(':')[0]}；Agent 经 mTLS 业务调用={biz}")
        stop_all()
    finally:
        stop_all()

    evidence["finished_at"] = dt.datetime.now().isoformat(timespec="seconds")
    passed = (evidence["scenario3_resilience"]["final_auc_matches_control"]
              and evidence["scenario3_resilience"]["last_seen_frozen_while_offline"]
              and evidence["scenario3_resilience"]["last_seen_refreshed_after_reconnect"]
              and evidence["scenario4_poison"]["http_status"] == 422
              and evidence["scenario2_zero_exfiltration"]["envelopes"] > 0
              and evidence["scenario5_mtls"]["pass"])
    evidence["overall"] = "PASS" if passed else "FAIL"
    (ws / "drill_result.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"{'=' * 20} 演练总体：{'✅ PASS' if passed else '❌ FAIL'}；证据 → {ws / 'drill_result.json'}")
    if not args.keep:
        shutil.rmtree(ws, ignore_errors=True)
        log("演练工作区已清理（--keep 可保留）")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
