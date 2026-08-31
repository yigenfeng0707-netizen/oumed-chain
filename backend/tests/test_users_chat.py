"""User creation and offline conversational continuity tests."""

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import crud
from app.models import Base
from app.services.orchestrator import Orchestrator


def test_create_user_and_persist_conversation():
    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        async with session_factory() as session:
            user = await crud.create_user(
                session,
                name="林女士",
                age=41,
                gender="女",
                city="杭州",
                insurance_type="职工医保",
                employee_status="在职",
            )
            assert user.id == 1

            await crud.create_conversation(session, "conversation-1", user.id, "你好")
            await crud.append_chat_message(
                session, "conversation-1", "user", "你好，你能做什么？"
            )
            await crud.append_chat_message(
                session,
                "conversation-1",
                "assistant",
                "我是 瓯医数链 助手。",
                "assistant_agent",
            )
            await session.commit()

            messages = await crud.get_conversation_messages(
                session, "conversation-1"
            )
            assert [item.role for item in messages] == ["user", "assistant"]
            assert messages[1].agent_type == "assistant_agent"

        await engine.dispose()

    asyncio.run(scenario())


def test_offline_general_conversation_uses_user_profile():
    async def scenario():
        orchestrator = Orchestrator()
        profile = {
            "found": True,
            "name": "林女士",
            "age": 41,
            "insurance_type": "职工医保",
            "chronic_diseases": ["高血压"],
        }
        assert orchestrator._keyword_intent("你好，你能做什么？") == "general"
        result = await orchestrator.route_to_agent(
            "general", "我是谁？请介绍一下我", "user_001", profile
        )
        assert "林女士" in result["response"]
        assert "高血压" in result["response"]

    asyncio.run(scenario())
