
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    user_id: str = Field(default="user_001")
    conversation_id: str | None = None
    # 前端携带的最近对话历史（role+content），用于上下文连续性与指代消解
    history: list[dict[str, str]] | None = None


class UserCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    age: int = Field(..., ge=0, le=130)
    gender: str = Field(..., pattern=r"^(男|女|其他)$")
    city: str = Field(..., min_length=1, max_length=50)
    insurance_type: str = Field(..., min_length=1, max_length=50)
    employee_status: str = Field(..., min_length=1, max_length=30)


class PreReviewRequest(BaseModel):
    total_amount: float = Field(..., ge=0)
    visit_type: str = Field(default="门诊")
    insurance_type: str = Field(default="职工医保")


class DataQueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    user_id: str | None = None


class DrugRegisterRequest(BaseModel):
    """用户确认后将扫描到的药品登记到用药记录"""
    user_id: str = Field(default="user_001")
    drug: dict = Field(default_factory=dict)
    category: str | None = None


class PolicySearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    category: str | None = None


class AuthorizationRequest(BaseModel):
    user_id: str
    data_type: str = Field(..., pattern=r"^(医保缴费记录|就医记录|购药记录|健康档案|脑电数据)$")
    authorized_agent: str = Field(..., pattern=r"^(权益管家|报销助手|健康卫士|政策参谋|脑电卫士|档案管家)$")
    duration_days: int = Field(default=365, ge=1, le=3650)
