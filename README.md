# 瓯医数链 OuMedTrust

> 医疗数据要素可信流通平台 —— **第二届全球技术创新大赛 · AI+医疗专题赛（赛道二）** 参赛项目
> 让医疗数据「**可用不可见、可控可计量**」

## 项目定位

**1 个可信医疗数据底座 + N 个医疗 AI 应用生态**：

- **底座（真实现）**：三家医院联邦学习协作（数据不出院）→ 差分隐私 → 审计存证链 → 数据产品流通 → 监管合规
- **应用生态**：泛癌卫士（Oncoformer 泛癌预测，温附医 Cell 2026 模型）、影像卫士（医师在环）、脑电卫士、档案管家、政策参谋、权益管家等 9 个医疗智能体，作为数据产品的消费方与民生价值出口

## 核心模块

```
backend/
├── app/services/federated/    # ⭐ 联邦学习引擎（自研，纯CPU）
│   ├── data_generator.py      #   3家异构模拟医院合成EHR（阳性率~22%，贴近流行病学）
│   └── engine.py              #   FedAvg + 差分隐私(DP-FedAvg) + 联邦统计
├── app/routers/federation.py  #   联邦任务API + 审计存证链(sha256串联)
├── app/services/orchestrator  #   多智能体编排（9个医疗Agent）
└── ...                        #   影像/脑电/档案/政策/报销等业务域
frontend/src/app/federation/   # ⭐ 联邦协作网络看板（核心演示页）
```

## 快速启动（比赛现场轻量模式，无 Docker 依赖）

```bash
# 后端（端口 8100，本机 8000 被系统占用）
cd backend
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
set DEMO_OFFLINE=true && uvicorn app.main:app --port 8100

# 前端
cd frontend
pnpm install && pnpm dev   # .env.local 已指向 http://localhost:8100
```

## 实验基准（种子固定可复现，启动后 GET /api/federation/benchmark）

| 方案 | 全局测试 AUC |
|------|-------------|
| 三甲医院本地模型 | 0.7013 |
| 县医院本地模型 | 0.6994 |
| 社区中心本地模型 | 0.6896 |
| **联邦学习 FedAvg** | **0.7018** |
| 联邦+轻噪声差分隐私（σ=0.01） | 0.6922 |
| 集中训练上界（现实不可行） | 0.7012 |

- 联邦模型 **追平集中训练上界**（数据不出院即获得大池化效果）
- 逐院公平性：三家医院全部获益（A +0.003 / B +0.000 / C +0.012）
- 数据阳性率 21.8%~25.1%，贴近真实心衰 30 天再入院流行病学
- DP 提供"隐私-效用"分档权衡：轻噪声档仅损失 ~0.01 AUC

## Docker 一键部署（生产形态，WSL Ubuntu-D）

```bash
./scripts/wsl-stack.sh up    # postgres(5433) + redis + chromadb + 后端(8100) + 前端(3000)
```

## 大赛关键信息

- 报名截止 **9月20日** → 初筛 9月下旬 → 初赛 9月底 → 决赛 10月中旬（温州鹿城）
- 报名：https://v.wjx.cn/vm/r8ktpyx.aspx ｜ 咨询：汤老师 18314853376
- 参赛资格四条（主申请人）：年龄≤50 / 硕士及以上 / 核心创始人持股≥15% / 自主知识产权

## 路线图

- [x] 联邦学习引擎 + 基准实验（可复现数字）
- [x] 平台底座（FastAPI + Next.js，含 8 医疗智能体生态）
- [x] 联邦协作看板页 + 审计存证链
- [ ] AI 病历治理 Copilot（本地 Qwen：结构化 + PHI 脱敏）
- [ ] 数据产品目录 + 授权交易闭环 + 监管看板
- [ ] 新 BP（赛道二叙事）+ 8 分钟演示动线 + 软著×3 + 专利交底书
