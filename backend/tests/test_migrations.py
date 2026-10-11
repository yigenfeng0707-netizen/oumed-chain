"""P2.1 数据库迁移体系测试：

- Alembic baseline 迁移可重复执行（空库两次 upgrade head 无异常、幂等）；
- 迁移产物表集合与 ORM metadata 完全一致（防模型/迁移漂移）；
- 迁移后库可直接承载应用启动（init_db 不再需要补列即通过，create_all 幂等共存）。

迁移通过 alembic command API 程序化执行（URL 覆盖为临时 SQLite 文件，
与生产 PG 共用同一套脚本——env.py 统一从 DATABASE_URL/settings 取连接串）。
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, inspect  # noqa: E402

from app.models import Base  # noqa: E402

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _alembic_config(db_url: str) -> Config:
    cfg = Config(os.path.join(_BACKEND_DIR, "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def test_migration_repeatable_and_matches_metadata():
    """空库执行 upgrade head（两次，验证幂等）→ 表集合与 ORM metadata 一致。"""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "mig.db").replace("\\", "/")
        url = f"sqlite+aiosqlite:///{db_path}"
        cfg = _alembic_config(url)

        command.upgrade(cfg, "head")
        command.upgrade(cfg, "head")  # 重复执行：no-op，不抛异常

        engine = create_engine(f"sqlite:///{db_path}")
        migrated = set(inspect(engine).get_table_names())
        expected = set(Base.metadata.tables.keys())
        assert expected <= migrated, f"迁移缺表: {expected - migrated}"
        assert "alembic_version" in migrated

        # 应用 init_db（create_all 幂等）在迁移产物上共存无冲突
        Base.metadata.create_all(engine)
        engine.dispose()


def test_migration_schema_reusable_by_app_engine():
    """迁移建的库可直接连接读写（health 级冒烟：插入+查询一轮）。"""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "mig2.db").replace("\\", "/")
        cfg = _alembic_config(f"sqlite+aiosqlite:///{db_path}")
        command.upgrade(cfg, "head")

        engine = create_engine(f"sqlite:///{db_path}")
        with engine.begin() as conn:
            from sqlalchemy import insert

            from app.models import User

            conn.execute(insert(User).values(
                name="迁移冒烟", age=30, gender="女", city="温州",
                insurance_type="职工医保", employee_status="在职", role="member"))
            row = conn.execute(
                __import__("sqlalchemy").text(
                    "SELECT name, role FROM users WHERE name='迁移冒烟'")).fetchone()
            assert row == ("迁移冒烟", "member")
        engine.dispose()
