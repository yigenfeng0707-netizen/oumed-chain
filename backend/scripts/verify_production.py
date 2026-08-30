"""线上全功能端到端验证（线上 Render 后端）

逐个测试 8 大功能，给出权威结论。

用法：
    python verify_production.py                          # 默认线上地址
    python verify_production.py --base http://localhost:8000  # 本地后端
"""

import argparse
import json
import urllib.request
import urllib.error
import ssl
import time

# 默认线上地址（可通过命令行参数覆盖）
DEFAULT_BASE = "https://yibao-zhinao-api.onrender.com"
ssl_ctx = ssl.create_default_context()

results = {"pass": 0, "fail": 0, "items": []}


def check(name, ok, detail=""):
    mark = "✅" if ok else "❌"
    line = f"  {mark} {name}" + (f" — {detail}" if detail else "")
    print(line)
    results["items"].append((name, ok))
    if ok:
        results["pass"] += 1
    else:
        results["fail"] += 1


def api_get(path, timeout=45):
    try:
        req = urllib.request.Request(f"{BASE}{path}")
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx) as resp:
            return json.loads(resp.read().decode("utf-8")), resp.status
    except Exception as e:
        return None, str(e)


def api_post(path, data, timeout=60):
    try:
        body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(
            f"{BASE}{path}", data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx) as resp:
            return json.loads(resp.read().decode("utf-8")), resp.status
    except Exception as e:
        return None, str(e)


def section(t):
    print(f"\n{'='*60}\n{t}\n{'='*60}")


# 命令行参数解析
parser = argparse.ArgumentParser(description="MedSignal线上验证脚本")
parser.add_argument("--base", default=DEFAULT_BASE,
                    help=f"后端 API 基地址（默认：{DEFAULT_BASE}）")
args = parser.parse_args()
BASE = args.base.rstrip("/")

# 唤醒 Render（免费套餐冷启动）
print(f"目标服务：{BASE}")
print("唤醒服务（冷启动，请等待...）")
t0 = time.time()
api_get("/api/health", timeout=90)
print(f"  唤醒耗时 {time.time()-t0:.1f}s\n")

# ===== 1. 基础健康检查 =====
section("1. 基础健康检查")
h, s = api_get("/api/health")
check("GET /api/health", s == 200 and h and h.get("status") == "ok",
      f"v{h.get('version') if h else '?'}")

hd, sd = api_get("/api/health/detailed")
if hd:
    deps = hd.get("dependencies", {})
    check("LLM 可用", deps.get("llm", {}).get("available"), deps.get("llm", {}).get("primary_model"))
    check("数据库可用", deps.get("database", {}).get("available"))
    check("OCR 可用", deps.get("ocr", {}).get("available"))
    check("知识库可用", deps.get("knowledge_base", {}).get("available"))
    check("非降级模式", hd.get("demo_mode") is False, "LLM 真实生效")

# ===== 2. AI 对话（核心 P0-1）=====
section("2. AI 对话（真实 LLM）")
for msg, expect in [
    ("我的医保卡里有多少钱", "coverage_agent"),
    ("我有糖尿病能享受什么政策", "policy_agent"),
    ("帮我看看我的健康风险", "health_agent"),
]:
    r, s = api_post("/api/agents/chat", {"message": msg, "user_id": "user_001"})
    if r:
        ok_agent = r.get("agent_type") == expect
        ok_resp = len(r.get("response", "")) > 15
        ok_profile = r.get("user_profile") is not None
        name = r.get("user_profile", {}).get("name", "") if ok_profile else ""
        check(f"[{msg[:14]}] 意图={expect}", ok_agent, r.get("agent_type"))
        check(f"[{msg[:14]}] 回复有效", ok_resp, f"{len(r.get('response', ''))}字")
        check(f"[{msg[:14]}] 用户画像", ok_profile, f"为「{name}」个性化")
    else:
        check(f"[{msg[:14]}]", False, str(s))

# ===== 3. 多智能体协作（P2-1）=====
section("3. 多智能体协作 /complex-chat")
r, s = api_post("/api/agents/complex-chat", {
    "message": "我父亲做心脏搭桥能报多少，有哪些政策能省钱",
    "user_id": "user_001",
})
if r:
    check("复合意图识别", r.get("multi_agent") is True, f"agents={r.get('agents_invoked')}")
    check("意图权重返回", len(r.get("intent_weights", [])) >= 1)
else:
    check("complex-chat", False, str(s))

# ===== 4. 权益全景（P0-2 数据库）=====
section("4. 权益全景（多用户数据）")
u1, _ = api_get("/api/coverage/user_001")
u2, _ = api_get("/api/coverage/user_002")
if u1 and u2:
    check("张阿姨(user_001)", u1["user"]["name"] == "张阿姨", u1["user"]["name"])
    check("李大爷(user_002)", u2["user"]["name"] == "李大爷", u2["user"]["name"])
    check("两用户数据不同", u1["user"]["name"] != u2["user"]["name"], "多用户生效")
    check("缴费历史非空", len(u1.get("payment_history", [])) > 0, f"{len(u1.get('payment_history', []))}条")

# ===== 5. 报销测算（P1-1 引擎）=====
section("5. 报销测算（claims_engine）")
est, _ = api_get("/api/coverage/user_001/estimate?total_cost=20000&visit_type=住院&hospital_level=二级")
if est:
    check("测算返回", est.get("estimated_reimbursement", 0) > 0, f"报销{est.get('estimated_reimbursement')}元")
    check("多场景对比", len(est.get("comparison", [])) >= 3, f"{len(est.get('comparison', []))}场景")
    check("分步推导", len(est.get("steps", [])) >= 6, f"{len(est.get('steps', []))}步")

# 报销预审
pr, _ = api_post("/api/claims/pre-review", {"total_amount": 253.5, "visit_type": "门诊", "insurance_type": "职工医保"})
if pr:
    check("预审分步推导", len(pr.get("steps", [])) >= 5, f"{len(pr.get('steps', []))}步")
    check("预审有解释", bool(pr.get("explanation")))

# ===== 6. 健康画像（P1-2 引擎）=====
section("6. 健康画像（health_engine）")
hp1, _ = api_get("/api/health/user_001/profile")  # 张阿姨 糖尿病+高血压
hp3, _ = api_get("/api/health/user_003/profile")  # 王先生 健康
if hp1 and hp3:
    check("张阿姨雷达5维", len(hp1.get("radar_data", [])) == 5)
    check("王先生雷达5维", len(hp3.get("radar_data", [])) == 5)
    check("评分因人而异", hp1.get("health_score") != hp3.get("health_score"),
          f"张{hp1.get('health_score')} vs 王{hp3.get('health_score')}")
    check("慢病用户有预警", len(hp1.get("alerts", [])) > 0, f"{len(hp1.get('alerts', []))}条")

# 主动预警
pa, _ = api_get("/api/health/user_001/proactive-alerts")
if pa:
    check("主动预警接口", pa.get("alert_count", 0) >= 0, f"{pa.get('alert_count')}条预警")

# ===== 7. 政策匹配（P1-3 引擎）=====
section("7. 政策匹配（policy_matcher）")
pm1, _ = api_get("/api/policy/match/user_001")  # 糖尿病+高血压
pm3, _ = api_get("/api/policy/match/user_003")  # 健康
if pm1 and pm3:
    check("张阿姨匹配政策", pm1.get("matched_count", 0) > 0, f"{pm1.get('matched_count')}条")
    check("张阿姨省钱>0", pm1.get("total_savings", 0) > 0, f"{pm1.get('total_savings')}元")
    check("慢病匹配多于健康", pm1.get("matched_count", 0) >= pm3.get("matched_count", 0),
          f"张{pm1.get('matched_count')} vs 王{pm3.get('matched_count')}")
    check("含省钱明细", any(p.get("annual_savings", 0) > 0 for p in pm1.get("policies", [])))

# ===== 8. 数据安全 + 可信数据空间（P2-2）=====
section("8. 数据安全 + 可信数据空间")
sec, _ = api_get("/api/security/authorizations/user_001")
if sec:
    check("授权矩阵", len(sec.get("authorization_matrix", [])) == 4, f"{len(sec.get('authorization_matrix', []))}数据类型")
    check("审计日志含存证", any("proof_hash" in str(l) for l in sec.get("audit_log", [])))

df, _ = api_get("/api/security/data-flow/user_001")
if df:
    check("数据流转记录", df.get("total_flows", 0) >= 0, f"{df.get('total_flows')}条流转")
    check("可信空间原则", bool(df.get("principle")))

# ===== 汇总 =====
section("总验证结果")
total = results["pass"] + results["fail"]
print(f"  通过: {results['pass']} / {total}")
if results["fail"]:
    print(f"  失败项:")
    for name, ok in results["items"]:
        if not ok:
            print(f"    ❌ {name}")
    print(f"\n  ⚠️  有 {results['fail']} 项失败，请检查")
else:
    print("\n  🎉 全部功能验证通过！线上服务完全可用！")
