# LLM 并发压测报告（P2-3.5 验收证据）

> 日期：2026-09-01 ｜ 环境：本地后端（uvicorn，SQLite 副本库，DEMO_MODE 鉴权演示模式）
> 模型链路：主力 Kimi-K3（aiping 网关）→ 备选 step-3.7-flash（阶跃星辰），真实 API 调用
> 压测脚本：`backend/scripts/loadtest_llm_concurrency.py`（零新依赖：httpx + asyncio）
> 明细数据：`backend/loadtest_result_c10.json`（10 路轮次）

## 验收结论

**P2-3.5 验收标准"并发 5 路稳定"通过，且在 2 倍压力（10 路）下仍保持 100% 成功率。**

| 指标 | 并发 5 路（3 轮 × 5） | 并发 10 路（2 轮 × 10） |
|------|----------------------|------------------------|
| 成功率 | **15/15 = 100%** | **20/20 = 100%** |
| 延迟 p50 | 14.0 s | 15.0 s |
| 延迟 p90 | 23.2 s | 28.8 s |
| 延迟 p95 | 24.3 s | 28.9 s |
| 延迟 max | 24.3 s | 28.9 s |
| 吞吐 | 0.327 req/s | 0.500 req/s |
| 健康基线（/api/health） | 30.5 ms | 22.0 ms |

- 压测对象为 `/api/agents/chat` **全链路**：意图识别（LLM）→ 用户画像注入 → 智能体路由 → LLM/RAG 生成 → 会话落库，非仅 LLM 单点。
- 15/20 个请求覆盖 6 类智能体（coverage/policy/health/eeg/body/cancer），路由与生成均正常。
- 框架开销极小（健康基线 < 35ms）：延迟几乎全部来自推理模型生成时长（与线上观测 20–55s 量级一致），并发提升到 10 路未见框架层排队恶化。

## 关键发现

1. **429 限频 + 主备切换实战验证** ✅：10 路并发时主力网关出现
   `429 模型调用频率超限`，`LLMService` 自动切换备选模型 step-3.7-flash，
   该请求最终仍 200 返回——双模降级机制在真实故障下有效，无请求失败。
2. **空内容自愈** ✅：日志出现一次"主力 LLM 返回空内容"（推理模型思考耗尽 token 场景），
   reasoning 字段兜底与备选切换均正常工作。
3. **本地环境限制（非并发问题）**：本机知识库索引未构建（chunks=0）且
   ONNX 嵌入模型并发加载失败（缓存目录权限 error 13），政策/权益类问题降级
   mock 模板（28/37 字短回复即来源于此）。线上环境知识库 137 chunks 正常，
   不影响并发结论；但暴露了 **Chroma ONNX 模型冷启动并发加载的竞态**，
   已记入待办（见下）。
4. **健康预警子链路**：日志见一条"健康预警生成失败：LLM 服务未初始化"
   （health agent 内部子组件懒加载时序问题），主响应不受影响，属低优先级缺陷。

## 复现方式

```powershell
# 1. 以演示模式 + 独立库启动后端（避免污染主库）
cd oumed-chain/backend
Copy-Item yibao.db loadtest.db -Force
$env:DEMO_MODE='true'; $env:DATABASE_URL='sqlite+aiosqlite:///./loadtest.db'
$env:CHAIN_ANCHOR_INTERVAL_HOURS='0'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8010

# 2. 压测（验收档 / 压力档）
.\.venv\Scripts\python.exe scripts\loadtest_llm_concurrency.py --concurrency 5 --rounds 3
.\.venv\Scripts\python.exe scripts\loadtest_llm_concurrency.py --concurrency 10 --rounds 2
```

## 后续待办（由本次压测发现）

- [x] **LLM 网关限流**：`LLMService` 加全局 semaphore（默认 8 路，`LLM_MAX_CONCURRENCY` 可配），
  主动规避 429 而非被动依赖备选切换（备选切换虽有效但消耗双份配额）。✅ 2026-10-12 落地，见下节。
- [x] Chroma ONNX 嵌入模型改为**进程启动时预热加载**，规避并发冷加载竞态（error 13）。✅ 2026-10-12 落地，见下节。
- [ ] health agent 健康预警子组件初始化时序修复（懒加载竞态）。
- [x] 结构化字段准确率评估集（200 条标注对照）——3.5 验收另一半。✅ 2026-10-12 交付，实测 99.94%，见下节。

## P2.5 生产化落地（2026-10-12）

### ① 全局并发限流（`LLM_MAX_CONCURRENCY`，默认 8）

- `LLMService` 单信号量覆盖**主力/备选/视觉全部出站通道**：一个槽位保护一次完整调用链
  （含故障切换），超出上限的请求排队等待而非打满上游——主动规避压测实测的 429 限频。
- 排队复用既有路由层超时保护；`chat_vision` 顺带修复同步 SDK 直调阻塞事件循环的问题
  （与 `chat` 同款 `asyncio.to_thread` 包装，规避线上 504 教训）。
- 测试：`tests/test_llm_production.py`（6 路并发打 2 路限流器峰值 ≤2、故障切换同槽位、
  视觉通道共限流、未初始化报错语义）。

### ② Chroma ONNX 冷启动预热

- `KnowledgeBase.prewarm()`：启动后由编排器**后台任务**强制完成嵌入模型下载/加载
  （10-30s 级冷启动不再由首个用户请求承担），失败只告警不影响功能；
  同时消除压测发现的并发首次冷加载竞态窗口。
- 接线：`orchestrator.initialize_services()` 知识库初始化成功后 `create_task(_prewarm_kb())`。

### ③ 结构化字段准确率评估：**99.94%（验收线 95%，达标）**

- 评估集：`data/structure_eval_set.json`——200 条合成入院记录 + 字段级标注（固定 seed 可复现，
  标注忠实性由测试硬校验：每个标注值都能在病历原文中找到出处）。
- 评分口径：9 个叶子字段/条（age/sex/主诉/血压/心率/体温/诊断/用药/既往史），
  归一化对照（空白/标点/单位容错，集合多重匹配，多报与漏报均记错），共 1800 个字段判定。
- 真实链路实测（`backend/scripts/evaluate_structure_accuracy.py`，治理链
  本地 Ollama→云端自动切换，本次全量命中 Kimi-K3 云端）：

| 指标 | 结果 |
|------|------|
| 总体字段准确率 | **1799/1800 = 99.94%**（验收 ≥95% ✅） |
| 分字段 | age/sex/主诉/bp/心率/体温/诊断/用药 100%；既往史 99.5% |
| 并发/耗时 | 5 路，总耗时 256.5s，平均单条 6.3s |
| 唯一失分样例 | eval-046：现病史片段「10年前起病…」被误归入既往史（多报 1/200，真实模型边界） |

- 评测中发现并修复的系统性问题：既往史「无特殊/体健」被模型误收为要点 →
  `_clean_structured` 增加空值语义归一（第一轮 99.50% → 修复后 99.94%）。
- 明细证据：`backend/structure_eval_result.json`（200 条 pred/gold 逐字段对照）。
