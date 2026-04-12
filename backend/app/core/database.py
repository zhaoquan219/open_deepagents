from collections.abc import Iterator

import pymysql  # type: ignore[import-untyped]
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.base import Base


class DatabaseState:
    def __init__(self, engine: Engine, session_factory: sessionmaker[Session]):
        self.engine = engine
        self.session_factory = session_factory

    @classmethod
    def from_settings(cls, settings: Settings) -> "DatabaseState":
        if settings.is_mysql:
            _ensure_mysql_database(settings)
        connect_args = {"check_same_thread": False} if settings.is_sqlite else {}
        assert settings.database_url is not None
        engine = create_engine(
            settings.database_url,
            future=True,
            pool_pre_ping=True,
            connect_args=connect_args,
        )
        session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        return cls(engine=engine, session_factory=session_factory)

    def create_all(self) -> None:
        # Ensure ORM models are imported before SQLAlchemy reflects metadata.
        from app.db import models  # noqa: F401

        Base.metadata.create_all(bind=self.engine)

    def session(self) -> Iterator[Session]:
        db = self.session_factory()
        try:
            yield db
        finally:
            db.close()

    def dispose(self) -> None:
        self.engine.dispose()


def _ensure_mysql_database(settings: Settings) -> None:
    assert settings.database_url is not None
    url = make_url(settings.database_url)
    database_name = url.database
    if not database_name:
        raise ValueError("MySQL DATABASE_URL must include a database name")
    connection = pymysql.connect(
        host=url.host or "127.0.0.1",
        port=url.port or 3306,
        user=url.username or "",
        password=url.password or "",
        charset="utf8mb4",
        autocommit=True,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{database_name}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
    finally:
        connection.close()
