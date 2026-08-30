from __future__ import annotations

from typing import Optional

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker

from backend.core.config import get_settings

Base = declarative_base()

_engine: Optional[Engine] = None
_SessionLocal: Optional[sessionmaker] = None


def init_engine() -> None:
    global _engine, _SessionLocal
    if _engine is not None and _SessionLocal is not None:
        return

    settings = get_settings()
    engine = create_engine(
        settings.database_url,
        echo=False,
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA cache_size=-64000")
        cursor.execute("PRAGMA temp_store=MEMORY")
        cursor.close()

    _engine = engine
    _SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_engine() -> Engine:
    if _engine is None:
        init_engine()
    return _engine  # type: ignore[return-value]


def get_session_local() -> sessionmaker:
    if _SessionLocal is None:
        init_engine()
    return _SessionLocal  # type: ignore[return-value]


def get_db():
    session_local = get_session_local()
    db = session_local()
    try:
        yield db
    finally:
        db.close()


def ensure_schema_upgrades() -> None:
    """幂等补齐轻量 schema 演进（create_all 不会为已存在表加列）。

    当前演进：
    - task_logs.run_id：每次运行唯一 run_id 的可追溯列；
    - task_logs.worker_id：执行进程标识（多 worker 排查并发来源）。
    """
    engine = get_engine()
    try:
        inspector = _get_inspector(engine)
        if "task_logs" not in inspector.get_table_names():
            return
        columns = {col["name"] for col in inspector.get_columns("task_logs")}
        with engine.begin() as conn:
            if "run_id" not in columns:
                conn.exec_driver_sql(
                    "ALTER TABLE task_logs ADD COLUMN run_id VARCHAR(32)"
                )
            if "worker_id" not in columns:
                conn.exec_driver_sql(
                    "ALTER TABLE task_logs ADD COLUMN worker_id VARCHAR(64)"
                )
    except Exception as e:  # pragma: no cover - 迁移失败不阻断启动
        import logging

        logging.getLogger("backend.database").warning(
            "ensure_schema_upgrades 失败（可忽略，后续运行会重试）: %s", e
        )


def _get_inspector(engine: Engine):
    try:
        from sqlalchemy import inspect as sa_inspect

        return sa_inspect(engine)
    except TypeError:  # pragma: no cover - sqlalchemy < 2.0 兼容
        from sqlalchemy.engine.reflection import Inspector

        return Inspector.from_engine(engine)
