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

    # 身份作用域开关（P0 鉴权强制）：
    # true（默认）= 演示模式，业务端点信任 user_id 路径参数（user-switcher 零摩擦）；
    # false = 生产模式，业务端点强制 X-User-Token 与 user_id 匹配（管理员可代查）。
    DEMO_MODE: bool = True

    # 存证链外部锚定：RFC 3161 可信时间戳机构地址（留空 = 仅离线留痕不请求 TSA）
    CHAIN_ANCHOR_TSA_URL: str = "https://freetsa.org/tsr"
    # 定时锚定周期（小时；0 = 关闭，仅管理端手动锚定）
    CHAIN_ANCHOR_INTERVAL_HOURS: int = 24

    # 告警触达通道：钉钉/飞书群机器人 Webhook（留空 = 仅日志不推送）
    ALERT_WEBHOOK_URL: str = ""
    # 管理端登录失败多少次触发撞库告警（滑窗内计数）
    ALERT_LOGIN_FAILURE_THRESHOLD: int = 5

    # 支付宝在线支付：sandbox = 沙箱零配置；live = 真实收款（个人电脑网站支付）
    ALIPAY_MODE: str = "sandbox"
    ALIPAY_APP_ID: str = ""
    # PKCS1 PEM（换行用 \n 转义写入 .env）；私钥切勿提交仓库/粘贴聊天。留空兜底读 backend/keys/
    ALIPAY_APP_PRIVATE_KEY: str = ""
    ALIPAY_PUBLIC_KEY: str = ""
    # 支付成功异步回调地址（需公网 HTTPS，如 ms.show 域名）
    ALIPAY_NOTIFY_URL: str = ""
    # 支付完成同步跳转地址（电脑网站支付 return_url，留空 = 不跳转）
    ALIPAY_RETURN_URL: str = ""

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
