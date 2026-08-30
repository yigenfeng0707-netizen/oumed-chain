"""用户管理 API。"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from app.database import get_db
from app.schemas import UserCreate

router = APIRouter(prefix="/api/users", tags=["用户管理"])


def _user_payload(user) -> dict:
    return {
        "id": user.id,
        "public_id": f"user_{user.id:03d}",
        "name": user.name,
        "email": user.email,
        "age": user.age,
        "gender": user.gender,
        "city": user.city,
        "insurance_type": user.insurance_type,
        "employee_status": user.employee_status,
        "conditions": [],
    }


@router.get("")
async def list_users(db: AsyncSession = Depends(get_db)):
    users = await crud.get_users(db, limit=200)
    return {"users": [_user_payload(user) for user in users]}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_user(payload: UserCreate, db: AsyncSession = Depends(get_db)):
    values = payload.model_dump()
    values["name"] = values["name"].strip()
    values["city"] = values["city"].strip()
    values["insurance_type"] = values["insurance_type"].strip()
    values["employee_status"] = values["employee_status"].strip()
    if not all(
        values[key]
        for key in ("name", "city", "insurance_type", "employee_status")
    ):
        raise HTTPException(status_code=422, detail="用户信息不能为空")
    user = await crud.create_user(db, **values)
    return _user_payload(user)
