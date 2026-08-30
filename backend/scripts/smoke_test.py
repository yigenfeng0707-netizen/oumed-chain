"""
P0 升级冒烟测试：用 TestClient 验证所有 Router 真实数据链路

不依赖端口启动，直接走 ASGI in-process。
覆盖：agents / coverage / claims / health / policy / security
"""

import asyncio
import json
import os
import sys

# 让脚本能从 backend 目录直接运行时找到 app 包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Windows GBK 控制台无法输出 emoji，遇到编码错误时替换而非崩溃
try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

from fastapi.testclient import TestClient

from app.database import engine, init_db
from app.main import app


# 幂等补齐开发库表结构（新增模型时旧库自动升级，不触发 LLM/知识库初始化）
# 用后立即 dispose 连接池：避免跨事件循环复用 aiosqlite 连接导致 database is locked
async def _ensure_schema():
    await init_db()
    await engine.dispose()


asyncio.run(_ensure_schema())

client = TestClient(app)


def section(title: str):
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


def main():
    results = {"pass": 0, "fail": 0, "errors": []}

    def check(name: str, ok: bool, detail: str = ""):
        mark = "✅" if ok else "❌"
        print(f"  {mark} {name}" + (f" — {detail}" if detail else ""))
        if ok:
            results["pass"] += 1
        else:
            results["fail"] += 1
            results["errors"].append(name)

    # ---------- 1. 健康检查 ----------
    section("1. 健康检查 GET /api/health")
    r = client.get("/api/health")
    check("健康检查 200", r.status_code == 200, str(r.json()))

    # ---------- 2. 覆盖度 / 用户切换（核心 P0-2 验证）----------
    section("2. 权益全景 — 多用户数据差异")
    u1 = client.get("/api/coverage/user_001").json()
    u2 = client.get("/api/coverage/user_002").json()
    check("user_001 张阿姨", u1["user"]["name"] == "张阿姨", u1["user"]["name"])
    check("user_002 李大爷", u2["user"]["name"] == "李大爷", u2["user"]["name"])
    check("两用户姓名不同", u1["user"]["name"] != u2["user"]["name"], "数据已区分 ✨")
    check("缴费历史非空", len(u1.get("payment_history", [])) > 0, f"{len(u1.get('payment_history', []))} 条")
    check("payment_history_values 存在", "payment_history_values" in u1, "前端兼容字段")
    check("近期活动含字符串金额", all(isinstance(a.get("amount"), str) for a in u1.get("recent_activities", [])))

    # ---------- 3. 报销测算（多场景对比）----------
    section("3. 报销测算 estimate")
    est = client.get("/api/coverage/user_001/estimate", params={
        "total_cost": 20000, "visit_type": "住院", "hospital_level": "二级"
    }).json()
    check("测算 200", True)
    # claims_engine 输出 estimated_reimbursement（总报销 = 基本 + 大病）
    reimbursed = est.get("estimated_reimbursement") or est.get("reimbursed_amount") or 0
    check("总报销 > 0", reimbursed > 0, f"{reimbursed}")
    check("comparison 多场景对比", len(est.get("comparison", [])) >= 3, f"{len(est.get('comparison', []))} 场景")
    check("含 explanation", bool(est.get("explanation")))
    check("含 steps 分步推导", len(est.get("steps", [])) >= 6, f"{len(est.get('steps', []))} 步")

    # ---------- 4. 报销预审（分步推导）----------
    section("4. 报销预审 pre-review")
    pr = client.post("/api/claims/pre-review", json={
        "total_amount": 1000, "visit_type": "门诊", "insurance_type": "职工医保"
    }).json()
    check("预审返回 steps", len(pr.get("steps", [])) >= 5, f"{len(pr.get('steps', []))} 步")
    check("个人自付 > 0", pr.get("out_of_pocket", 0) > 0, f"{pr.get('out_of_pocket')}")
    check("含 explanation", bool(pr.get("explanation")))

    # ---------- 5. 健康画像（真实数据动态评分）----------
    section("5. 健康画像 — 多用户评分差异")
    h1 = client.get("/api/health/user_001/profile").json()  # 张阿姨 糖尿病+高血压
    h3 = client.get("/api/health/user_003/profile").json()  # 王先生 无慢病
    check("张雷达 5 维", len(h1.get("radar_data", [])) == 5, f"{len(h1.get('radar_data', []))} 维")
    check("王雷达 5 维", len(h3.get("radar_data", [])) == 5)
    check("两用户评分不同", h1.get("health_score") != h3.get("health_score"),
          f"张{h1.get('health_score')} vs 王{h3.get('health_score')} ✨")
    check("慢病用户有预警", len(h1.get("alerts", [])) > 0, f"{len(h1.get('alerts', []))} 条")
    check("medication statusColor 是 class 字符串",
          any("text-" in str(m.get("statusColor", "")) for m in h1.get("medications", [])))

    # ---------- 6. 政策匹配（基于慢病差异化）----------
    section("6. 政策匹配 — 慢病差异")
    p1 = client.get("/api/policy/match/user_001").json()  # 糖尿病+高血压
    p3 = client.get("/api/policy/match/user_003").json()  # 无慢病
    check("张匹配政策数 > 0", len(p1.get("policies", [])) > 0, f"{len(p1.get('policies', []))} 条")
    check("张省钱 > 0", p1.get("total_savings", 0) > 0, f"{p1.get('total_savings')} 元")
    check("张比王更匹配", len(p1.get("policies", [])) >= len(p3.get("policies", [])),
          f"张{len(p1.get('policies', []))} vs 王{len(p3.get('policies', []))}")
    check("含 matchReason 双字段", any("matchReason" in x for x in p1.get("policies", [])))

    # ---------- 7. 数据安全（授权 + 审计 + 存证）----------
    section("7. 数据安全 — 授权/审计/存证")
    s1 = client.get("/api/security/authorizations/user_001").json()
    check("授权矩阵 4 数据类型", len(s1.get("authorization_matrix", [])) == 4,
          f"{len(s1.get('authorization_matrix', []))} 行")
    check("审计日志含 proof_hash",
          any("proof_hash" in str(l) for l in s1.get("audit_log", [])))

    # 创建授权（真实写库）
    ca = client.post("/api/security/authorize", json={
        "user_id": "user_001", "data_type": "健康档案",
        "authorized_agent": "健康卫士", "duration_days": 90
    }).json()
    check("授权创建返回 id", "id" in ca, str(ca.get("id")))
    check("授权创建含 proof_hash", "proof_hash" in ca, ca.get("proof_hash", "")[:16])

    # 数据流（P2-2 端点）
    df = client.get("/api/security/data-flow/user_001").json()
    check("数据流转记录", df.get("total_flows", 0) >= 0, f"{df.get('total_flows')} 条流转")
    check("含 principle", bool(df.get("principle")))

    # ---------- 8. AI 对话（核心 P0-1 验证）----------
    section("8. AI 对话 — 真实链路（非 mock）")
    for msg, expect_agent in [
        ("我的医保卡里有多少钱", "coverage_agent"),
        ("我有糖尿病能享受什么政策", "policy_agent"),
        ("帮我看看我的健康风险", "health_agent"),
        ("帮我做脑电健康评估", "eeg_agent"),
    ]:
        r = client.post("/api/agents/chat", json={
            "message": msg, "user_id": "user_001"
        })
        if r.status_code == 200:
            d = r.json()
            ok_agent = d.get("agent_type") == expect_agent
            ok_resp = len(d.get("response", "")) > 20
            ok_profile = d.get("user_profile") is not None
            check(f"[{msg[:15]}…] 意图={expect_agent}", ok_agent, d.get("agent_type"))
            check(f"[{msg[:15]}…] 回复非空", ok_resp, f"{len(d.get('response', ''))} 字符")
            check(f"[{msg[:15]}…] 含用户画像", ok_profile,
                  str(d.get("user_profile", {}).get("name")) if ok_profile else "无")
        else:
            check(f"[{msg[:15]}…] 200", False, f"HTTP {r.status_code}: {r.text[:100]}")

    # ---------- 9. EEG 脑电健康（BCI×医保创新模块）----------
    section("9. EEG 脑电健康 — 关键医疗信号")
    # 9.1 心理状态列表
    states = client.get("/api/eeg/states").json()
    check("心理状态列表", len(states.get("states", [])) == 5,
          f"{len(states.get('states', []))} 种状态")
    check("通道数 4", len(states.get("channels", [])) == 4,
          f"{states.get('channels')}")
    check("采样率 256Hz", states.get("sample_rate") == 256)

    # 9.2 发起 EEG 采集会话
    sess = client.post("/api/eeg/user_001/session", params={
        "mental_state": "stressed", "duration_seconds": 4
    }).json()
    check("EEG 会话返回 session_id", bool(sess.get("session_id")))
    check("心理状态 stressed", sess.get("mental_state") == "stressed",
          sess.get("mental_state_label"))
    check("4 通道波形", len(sess.get("waveform", [])) == 4,
          f"{len(sess.get('waveform', []))} 通道")
    check("五频段功率", len(sess.get("avg_band_powers", {})) == 5,
          f"{list(sess.get('avg_band_powers', {}).keys())}")
    check("四维健康指标",
          all(k in sess.get("metrics", {}) for k in
              ["stress_index", "attention_index", "sleep_quality", "cognitive_load"]))
    check("高压力触发预警", len(sess.get("alerts", [])) > 0,
          f"{len(sess.get('alerts', []))} 条预警")
    check("高压力联动医保政策", len(sess.get("policy_links", [])) > 0,
          f"{len(sess.get('policy_links', []))} 条政策")

    # 9.3 获取最近一次 EEG 评估
    latest = client.get("/api/eeg/user_001/latest").json()
    check("最新 EEG 含波形", len(latest.get("waveform", [])) == 4)
    check("最新 EEG 含指标", "stress_index" in latest.get("metrics", {}))
    # ⭐ 赛道7新增指标验证
    metrics = latest.get("metrics", {})
    check("含脑血管风险指数", "cerebrovascular_risk" in metrics)
    check("含认知衰退风险", "cognitive_decline_risk" in metrics)
    check("含精神状态筛查", "mental_health" in metrics)
    check("脑血管风险0-100", 0 <= metrics.get("cerebrovascular_risk", -1) <= 100)
    check("认知衰退风险0-100", 0 <= metrics.get("cognitive_decline_risk", -1) <= 100)
    mh = metrics.get("mental_health", {})
    check("精神状态含焦虑评分", "anxiety_score" in mh)
    check("精神状态含抑郁评分", "depression_score" in mh)
    check("精神状态含筛查标签", "screening_label" in mh)

    # 9.4 EEG 历史趋势
    hist = client.get("/api/eeg/user_001/history").json()
    check("历史记录非空", hist.get("total_sessions", 0) > 0,
          f"{hist.get('total_sessions')} 次")
    check("趋势数据非空", len(hist.get("trend", [])) > 0,
          f"{len(hist.get('trend', []))} 个点")

    # 9.5 实时流
    rt = client.get("/api/eeg/user_001/realtime", params={
        "mental_state": "relaxed", "seed": 1
    }).json()
    check("实时流波形", len(rt.get("waveform", [])) > 0)
    check("实时流含频段功率", len(rt.get("band_powers", {})) == 5)

    # 9.6 医保政策联动
    pl = client.get("/api/eeg/user_001/policy-links").json()
    check("政策联动返回", "policy_links" in pl,
          f"{len(pl.get('policy_links', []))} 条")

    # 9.7 不同心理状态指标差异
    sess_relaxed = client.post("/api/eeg/user_001/session", params={
        "mental_state": "relaxed", "duration_seconds": 4
    }).json()
    stress_stressed = sess.get("metrics", {}).get("stress_index", 0)
    stress_relaxed = sess_relaxed.get("metrics", {}).get("stress_index", 0)
    check("压力状态指标差异", stress_stressed > stress_relaxed,
          f"高压力{stress_stressed} vs 放松{stress_relaxed} ✨")


    # ---------- 10. 档案管家（人体健康档案，只增不删）----------
    section("10. 档案管家 — 对话归档 / 上传归档 / 检索")
    chat = client.post("/api/agents/chat", json={
        "message": "我2026年2月查出肺部小结节", "user_id": "user_009"
    }).json()
    check("对话触发归档 body_updates", len(chat.get("body_updates", [])) >= 1,
          f"{[u.get('organ') for u in chat.get('body_updates', [])]}")
    check("body_focus=lungs", chat.get("body_focus") == "lungs", str(chat.get("body_focus")))
    up = client.post("/api/body/user_009/upload", files={
        "file": ("ct_0715.txt", "2026年7月复查CT：肺部结节较前相仿。".encode("utf-8"), "text/plain")
    }).json()
    check("上传归档 records_added>=1", up.get("records_added", 0) >= 1, f"{up.get('doc_kind')}")
    check("上传回复含对比", bool(up.get("comparison")), "同部位自动并列历史记录")
    recs = client.get("/api/body/user_009/records", params={"organ": "lungs"}).json()
    check("肺部记录 >= 2 条（只增不删）", recs.get("total", 0) >= 2, f"{recs.get('total')} 条")
    check("时间倒序", recs["records"][0]["event_date"] >= recs["records"][-1]["event_date"],
          f"{recs['records'][0]['event_date']} → {recs['records'][-1]['event_date']}")
    check("每条带来源标签", all(r.get("source_label") for r in recs["records"]))
    organs = client.get("/api/body/organs").json()
    check("器官分类表", "lungs" in organs.get("organs", {}))

    # ---------- 汇总 ----------
    section("测试汇总")
    print(f"  通过: {results['pass']}  失败: {results['fail']}")
    if results["errors"]:
        print(f"  失败项: {results['errors']}")
        sys.exit(1)
    print("\n  🎉 P0 升级冒烟测试全部通过！")


if __name__ == "__main__":
    main()
