from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


# engine 惰性创建：import app.models 不应要求 PG 驱动在场（tests 用 sqlite 自建 engine）。
@lru_cache(maxsize=1)
def get_engine():
    return create_engine(settings.database_url, pool_pre_ping=True)


@lru_cache(maxsize=1)
def get_sessionmaker():
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)


def get_db():
    db = get_sessionmaker()()
    try:
        yield db
    finally:
        db.close()
