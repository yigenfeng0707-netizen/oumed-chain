#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MedSignal - 数据库初始化脚本

读取 mock_data.json，将数据插入到数据库中。
支持 SQLite（开发环境）和 PostgreSQL（生产环境）。

用法：
    # 使用默认SQLite
    python init_db.py

    # 使用PostgreSQL
    DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/dbname python init_db.py

    # 指定JSON路径
    python init_db.py --data-path /path/to/mock_data.json
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# 将项目根目录加入 sys.path，以便导入 app 模块
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import (
    Base,
    EEGRecord,
    InsuranceRecord,
    MedicalRecord,
    MedicationRecord,
    User,
)
from app.services.eeg import engine as eeg_engine
from app.services.body_archive_dossier import seed_demo_archive


def get_database_url() -> str:
    """获取数据库连接URL，优先使用环境变量"""
    return os.environ.get(
        "DATABASE_URL",
        "sqlite+aiosqlite:///./yibao.db",
    )


def load_mock_data(data_path: str) -> dict:
    """加载JSON格式的模拟数据"""
    path = Path(data_path)
    if not path.exists():
        print(f"❌ 数据文件不存在: {path}")
        print("请先运行 generate_data.py 生成数据")
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


async def insert_users(session: AsyncSession, users_data: list) -> dict:
    """
    插入用户数据
    返回 {用户ID_in_json: 用户ID_in_db} 的映射
    """
    id_map = {}
    for user_data in users_data:
        user = User(
            name=user_data["name"],
            age=user_data["age"],
            gender=user_data["gender"],
            city=user_data["city"],
            insurance_type=user_data["insurance_type"],
            employee_status=user_data["employee_status"],
        )
        session.add(user)
        await session.flush()  # 刷新以获取自增ID
        id_map[user_data["id"]] = user.id
        print(f"  ✅ 用户: {user.name} (JSON ID={user_data['id']} -> DB ID={user.id})")

    return id_map


async def insert_insurance_records(
    session: AsyncSession, records_data: list, id_map: dict
):
    """插入医保缴费记录"""
    count = 0
    for record in records_data:
        db_user_id = id_map.get(record["user_id"])
        if db_user_id is None:
            continue

        ins = InsuranceRecord(
            user_id=db_user_id,
            year=record["year"],
            month=record["month"],
            base_amount=record["base_amount"],
            personal_amount=record["personal_amount"],
            company_amount=record["company_amount"],
        )
        session.add(ins)
        count += 1

    print(f"  ✅ 缴费记录: {count} 条")


async def insert_medical_records(
    session: AsyncSession, records_data: list, id_map: dict
):
    """插入就诊记录"""
    count = 0
    for record in records_data:
        db_user_id = id_map.get(record["user_id"])
        if db_user_id is None:
            continue

        med = MedicalRecord(
            user_id=db_user_id,
            date=datetime.strptime(record["date"], "%Y-%m-%d %H:%M:%S"),
            hospital=record["hospital"],
            department=record["department"],
            diagnosis=record["diagnosis"],
            visit_type=record["visit_type"],
            total_cost=record["total_cost"],
            reimbursed_amount=record["reimbursed_amount"],
        )
        session.add(med)
        count += 1

    print(f"  ✅ 就诊记录: {count} 条")


async def insert_medication_records(
    session: AsyncSession, records_data: list, id_map: dict
):
    """插入购药记录"""
    count = 0
    for record in records_data:
        db_user_id = id_map.get(record["user_id"])
        if db_user_id is None:
            continue

        drug = MedicationRecord(
            user_id=db_user_id,
            date=datetime.strptime(record["date"], "%Y-%m-%d"),
            medication_name=record["medication_name"],
            category=record["category"],
            quantity=record["quantity"],
            unit_price=record["unit_price"],
            is_chronic=record["is_chronic"],
        )
        session.add(drug)
        count += 1

    print(f"  ✅ 购药记录: {count} 条")


async def insert_eeg_records(session: AsyncSession, users_data: list, id_map: dict):
    """为每个用户生成 EEG 历史记录（BCI×医保创新模块）

    根据用户画像（年龄/慢病）选择合适的心理状态，生成 3 条历史记录。
    """
    count = 0
    for user_data in users_data:
        db_user_id = id_map.get(user_data["id"])
        if db_user_id is None:
            continue

        # 根据用户画像推荐心理状态
        profile = {
            "found": True,
            "name": user_data["name"],
            "age": user_data["age"],
            "chronic_diseases": user_data.get("conditions", []),
        }
        # 生成 3 条历史记录（不同心理状态）
        states = ["relaxed", "focused", eeg_engine.pick_mental_state_by_profile(profile)]
        for i, state in enumerate(states):
            session_obj = eeg_engine.assess_session(
                user_id=str(db_user_id),
                mental_state=state,
                duration_seconds=4,
                user_profile=profile,
                seed=42 + i,
            )
            record = EEGRecord(
                user_id=db_user_id,
                session_id=session_obj.session_id,
                duration_seconds=session_obj.duration_seconds,
                mental_state=session_obj.mental_state,
                mental_state_label=session_obj.mental_state_label,
                avg_band_powers=json.dumps(session_obj.avg_band_powers, ensure_ascii=False),
                metrics=json.dumps(session_obj.metrics, ensure_ascii=False),
                alert_count=len(session_obj.alerts),
                policy_link_count=len(session_obj.policy_links),
                summary=session_obj.summary,
            )
            session.add(record)
            count += 1

    print(f"  ✅ EEG 记录: {count} 条")


async def init_database(data_path: str):
    """主函数：建表 + 插入数据"""
    database_url = get_database_url()
    print("=" * 60)
    print("MedSignal - 数据库初始化")
    print("=" * 60)
    print(f"数据库URL: {database_url}")
    print(f"数据文件: {data_path}")

    # 加载模拟数据
    mock_data = load_mock_data(data_path)

    # 创建引擎和会话工厂
    engine = create_async_engine(database_url, echo=False)
    async_session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    # 建表
    print("\n📋 创建数据表...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("✅ 数据表创建完成")

    # 幂等检查：users 表已有数据则跳过所有插入（防止容器重启时重复写入）
    async with async_session_factory() as session:
        from sqlalchemy import func
        existing_count = (
            await session.execute(select(func.count(User.id)))
        ).scalar()
    if existing_count and existing_count > 0:
        print(f"\n⏭️  users 表已有 {existing_count} 条数据，跳过基础数据初始化（幂等保护）")
        async with async_session_factory() as session:
            archive_result = await seed_demo_archive(session)
        print(f"✅ 数字人体完整档案: {archive_result}")
        await engine.dispose()
        print("🎉 跳过完成")
        return

    # 插入数据
    print("\n📋 插入数据...")
    async with async_session_factory() as session:
        async with session.begin():
            # 用户
            print("\n[1/5] 插入用户数据...")
            id_map = await insert_users(session, mock_data["users"])

            # 缴费记录
            print("\n[2/5] 插入缴费记录...")
            await insert_insurance_records(
                session, mock_data["insurance_records"], id_map
            )

            # 就诊记录
            print("\n[3/5] 插入就诊记录...")
            await insert_medical_records(
                session, mock_data["medical_records"], id_map
            )

            # 购药记录
            print("\n[4/5] 插入购药记录...")
            await insert_medication_records(
                session, mock_data["medication_records"], id_map
            )

            # EEG 记录（BCI×医保创新）
            print("\n[5/5] 生成 EEG 脑电记录...")
            await insert_eeg_records(session, mock_data["users"], id_map)

        async with async_session_factory() as session:
            archive_result = await seed_demo_archive(session)
        print(f"  ✅ 数字人体完整档案: {archive_result}")

    # 验证
    print("\n📋 验证数据...")
    async with async_session_factory() as session:
        from sqlalchemy import func

        user_count = (await session.execute(select(func.count(User.id)))).scalar()
        ins_count = (
            await session.execute(select(func.count(InsuranceRecord.id)))
        ).scalar()
        med_count = (
            await session.execute(select(func.count(MedicalRecord.id)))
        ).scalar()
        drug_count = (
            await session.execute(select(func.count(MedicationRecord.id)))
        ).scalar()
        eeg_count = (
            await session.execute(select(func.count(EEGRecord.id)))
        ).scalar()

        print(f"  用户: {user_count} 条")
        print(f"  缴费记录: {ins_count} 条")
        print(f"  就诊记录: {med_count} 条")
        print(f"  购药记录: {drug_count} 条")
        print(f"  EEG 记录: {eeg_count} 条")

    await engine.dispose()
    print("\n🎉 数据库初始化完成！")


def main():
    parser = argparse.ArgumentParser(description="MedSignal数据库初始化")
    parser.add_argument(
        "--data-path",
        type=str,
        default=str(Path(__file__).resolve().parent.parent.parent / "data" / "mock_data.json"),
        help="mock_data.json 文件路径",
    )
    args = parser.parse_args()

    asyncio.run(init_database(args.data_path))


if __name__ == "__main__":
    main()
