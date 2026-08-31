import asyncio
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app import metrics as app_metrics
from app.config import settings
from app.database import init_db
from app.routers import (
    admin,
    agents,
    auth,
    body,
    body_archive,
    cancer,
    claims,
    coverage,
    data,
    drugs,
    eeg,
    federation,
    governance,
    health_profile,
    imaging,
    marketplace,
    payments,
    policy,
    security,
    users,
)
from app.services import orchestrator


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await orchestrator.initialize_services()
    # 存证链定时锚定（周期可配；0 = 关闭仅手动锚定）
    from app.services.anchor_scheduler import start_anchor_task
    anchor_task = start_anchor_task()
    yield
    if anchor_task is not None:
        anchor_task.cancel()
        try:
            await anchor_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="瓯医数链 OuMedTrust · 医疗数据要素可信流通平台",
    description="第二届全球技术创新大赛 AI+医疗专题赛（赛道二）：联邦学习医疗协作 + AI 数据治理 Copilot + "
                "差分隐私与审计存证 + 数据要素流通交易闭环，让医疗数据「可用不可见、可控可计量」",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS 配置：从环境变量读取，默认开放（Demo 模式）
# 生产环境通过 CORS_ORIGINS 收敛为白名单（逗号分隔）
_cors_origins_env = os.getenv("CORS_ORIGINS", "*")
if _cors_origins_env.strip() == "*":
    _cors_origins = ["*"]
    _cors_credentials = False
else:
    _cors_origins = [o.strip() for o in _cors_origins_env.split(",") if o.strip()]
    _cors_credentials = True

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def metrics_middleware(request, call_next):
    """请求埋点：按路由模板+状态码计数并记录耗时（/metrics 自身不计数）。"""
    start = time.perf_counter()
    response = await call_next(request)
    if request.url.path not in ("/metrics", "/api/ops/metrics"):
        route_obj = request.scope.get("route")
        route = getattr(route_obj, "path", request.url.path) if route_obj else "unmatched"
        app_metrics.observe_request(route, response.status_code, time.perf_counter() - start)
    return response


@app.get("/metrics", include_in_schema=False)
@app.get("/api/ops/metrics", include_in_schema=False)
async def prometheus_metrics():
    """Prometheus 采集端点（零依赖手写 exposition 格式；仅计数指标，不含个人数据）。

    双路径：/metrics 供直连后端采集；/api/ops/metrics 走同域反代（魔搭 Next
    rewrites 仅转发 /api/*，根路径端点会被前端 404）。
    """
    return PlainTextResponse(
        app_metrics.render_prometheus(demo_mode=settings.DEMO_MODE),
        media_type="text/plain; version=0.0.4",
    )

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(agents.router)
app.include_router(coverage.router)
app.include_router(claims.router)
app.include_router(health_profile.router)
app.include_router(policy.router)
app.include_router(security.router)
app.include_router(federation.router)
app.include_router(governance.router)
app.include_router(marketplace.router)
app.include_router(payments.router)
app.include_router(eeg.router)
app.include_router(imaging.router)
app.include_router(cancer.router)
app.include_router(data.router)
app.include_router(body.router)
app.include_router(body_archive.router)
app.include_router(users.router)
app.include_router(drugs.router)

# 数字人体 3D 查看器（静态资源，前端 /body-archive 页面 iframe 嵌入）
_digital_body_dir = Path(__file__).resolve().parent / "static" / "digital-body"
if _digital_body_dir.is_dir():
    app.mount(
        "/digital-body",
        StaticFiles(directory=_digital_body_dir, html=True),
        name="digital-body",
    )


@app.get("/api/health")
async def health_check():
    """基础健康检查"""
    return {"status": "ok", "service": "瓯医数链 OuMedTrust", "version": "1.0.0"}


@app.get("/api/health/detailed")
async def detailed_health_check():
    """详细健康检查：返回各依赖服务状态（P3-4 容错降级用）

    前端 ApiStatusIndicator 可据此展示 LLM/KB/DB 各项健康度。
    """
    deps = {}

    # LLM 状态
    deps["llm"] = {
        "available": orchestrator._llm is not None and orchestrator._llm.is_available,
        "primary_model": settings.LLM_MODEL,
        "fallback_model": settings.DASHSCOPE_MODEL,
    }

    # 视觉模型状态（影像解读用，未配置 Key 时自动关闭）
    try:
        from app.services.vision_service import get_vision_service
        _vs = get_vision_service()
        vision_ok = _vs is not None
    except Exception:
        vision_ok = False
    deps["vision"] = {
        "available": vision_ok,
        "model": settings.VISION_MODEL,
        "purpose": "医学影像自然语言解读（可选能力，降级不影响主流程）",
    }

    # 知识库状态
    kb_ok = orchestrator._kb is not None
    kb_chunks = 0
    if kb_ok:
        try:
            stats = await orchestrator._kb.get_stats()
            kb_chunks = stats.get("total_chunks", 0)
        except Exception:
            pass
    deps["knowledge_base"] = {"available": kb_ok, "chunks": kb_chunks}

    # 数据库状态
    deps["database"] = {"available": True, "type": "sqlite"}

    # OCR
    deps["ocr"] = {"available": True, "provider": "ocr.space"}

    # EEG 脑电引擎（脑电信号识别模块）
    try:
        import numpy as np  # noqa: F401
        eeg_ok = True
    except Exception:
        eeg_ok = False
    deps["eeg_engine"] = {
        "available": eeg_ok,
        "channels": 4,
        "sample_rate": 256,
        "bands": ["delta", "theta", "alpha", "beta", "gamma"],
    }

    # 医学影像 AI 标注引擎（影像信号识别模块）
    try:
        from app.services.imaging import STUDY_TYPES
        imaging_ok = True
    except Exception:
        imaging_ok = False
    deps["imaging_engine"] = {
        "available": imaging_ok,
        "study_types": list(STUDY_TYPES) if imaging_ok else [],
        "image_size": 512,
        "pipeline": "预处理 → 局部对比度增强 → 连通域分析 → 形态学特征分类",
    }

    # 数字人体档案（档案管家 · 3D 查看器）
    deps["body_archive"] = {
        "available": _digital_body_dir.is_dir(),
        "viewer": "/digital-body/index.html",
        "api": "/api/body-archive/patients",
    }

    # 湖仓一体数据引擎（数据管家模块）
    try:
        from app.services.data_lake import engine as data_lake_engine
        data_engine_ok = True
        warehouse_tables = len(data_lake_engine.WAREHOUSE_TABLES)
    except Exception:
        data_engine_ok = False
        warehouse_tables = 0
    deps["data_engine"] = {
        "available": data_engine_ok,
        "warehouse_tables": warehouse_tables,
        "query_modes": ["template", "nl2sql"],
    }

    # 药品卫士引擎（拍照识别 × 用药安全）
    try:
        drug_engine_ok = True
        drug_modes = ["vision", "ocr_llm", "mock"]
    except Exception:
        drug_engine_ok = False
        drug_modes = []
    deps["drug_engine"] = {
        "available": drug_engine_ok,
        "recognition_modes": drug_modes,
        "endpoints": ["/api/drugs/scan", "/api/drugs/register"],
    }

    # 泛癌卫士引擎（Oncoformer 泛癌预测 · 真模型/预计算双形态）
    try:
        from app.services.cancer import engine as cancer_engine
        cancer_status = cancer_engine.status()
        cancer_ok = True
    except Exception:
        cancer_ok = False
        cancer_status = {}
    deps["cancer_engine"] = {
        "available": cancer_ok,
        "engine_mode": cancer_status.get("engine", "unavailable"),
        "cohort_patients": cancer_status.get("cohort_patients", 0),
        "model": cancer_status.get("model", ""),
    }

    # 联邦学习协作引擎（瓯医数链底座 · 数据要素协作）
    try:
        from app.services.federated import get_overview
        fed = get_overview()
        fed_ok = True
    except Exception:
        fed_ok = False
        fed = {}
    deps["federated_engine"] = {
        "available": fed_ok,
        "hospitals": len(fed.get("hospitals", [])),
        "task": fed.get("task", ""),
        "privacy": "FedAvg + 差分隐私（DP-FedAvg）+ 审计存证链",
    }

    all_ok = (
        all(d.get("available", False) for k, d in deps.items() if k != "vision")
        and deps["llm"].get("available", False)
    )
    return {
        "status": "ok" if all_ok else "degraded",
        "version": "1.0.0",
        "dependencies": deps,
        "demo_mode": orchestrator._llm is None,  # LLM 不可用时进入降级演示模式
    }
