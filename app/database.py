# -*- coding: utf-8 -*-
"""SQLite + SQLAlchemy 引擎与会话管理。"""
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from . import config

Base = declarative_base()

cfg = config.load_config()

# check_same_thread=False：FastAPI 多线程 + 后台任务线程共用连接
_engine = create_engine(
    f"sqlite:///{cfg.database}",
    connect_args={"check_same_thread": False},
    echo=False,
)

SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)


def get_db():
    """FastAPI 依赖：请求级会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """建表。"""
    import app.models  # noqa: F401  确保模型已注册
    cfg.database.parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=_engine)