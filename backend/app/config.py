from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite+aiosqlite:///./yibao.db"
    REDIS_URL: str = "redis://localhost:6379"

    # 主力 LLM：aiping 网关（Kimi-K3，OpenAI 兼容）
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = "https://aiping.cn/api/v1"
    LLM_MODEL: str = "Kimi-K3"

    # 备选 LLM：阶跃星辰（step-3.7-flash，主力故障时自动切换；变量名沿用 DASHSCOPE_）
    DASHSCOPE_API_KEY: str = ""
    DASHSCOPE_BASE_URL: str = "https://api.stepfun.com/step_plan/v1"
    DASHSCOPE_MODEL: str = "step-3.7-flash"

    # 视觉模型：aiping 网关（GLM-4.6V，供影像/图文理解扩展使用）
    VISION_API_KEY: str = ""
    VISION_BASE_URL: str = "https://aiping.cn/api/v1"
    VISION_MODEL: str = "GLM-4.6V"

    # 泛癌卫士（Oncoformer）：留空 = 真模型不可用，降级预计算队列模式
    ONCOFORMER_CKPT_PATH: str = ""
    # COMPASS 队列目录（含 metadata.parquet + cxr_images），本地实时推理用
    ONCOFORMER_DATA_DIR: str = ""
    # 预计算队列 JSON 覆盖路径（默认 <仓库根>/data/cancer_cohort.json）
    ONCOFORMER_COHORT_JSON: str = ""

    CHROMA_PERSIST_DIR: str = "./chroma_data"

    # 路演离线模式：跳过 LLM/知识库初始化，全程使用关键词+mock降级（无网络依赖）
    DEMO_OFFLINE: bool = False

    # OCR 服务：OCR.space
    OCR_API_KEY: str = ""
    OCR_API_URL: str = "https://api.ocr.space/parse/image"

    # 管理后台超级管理员（/admin 页面登录用）
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "瓯医数链@2026"
    # 管理员 token 签发密钥（改后所有已签发 token 失效）
    YIBAO_ADMIN_SECRET: str = "oumed-admin-secret"

    # 部署/鉴权相关：auth.py 、main.py 直接从环境变量读取，
    # 此处声明是为了让 pydantic-settings 解析 .env 时不报 extra_forbidden 错误
    YIBAO_API_KEY: str = ""
    YIBAO_SESSION_SECRET: str = "please-change-this"
    CORS_ORIGINS: str = "*"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
