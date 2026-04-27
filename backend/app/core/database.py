from collections.abc import Iterator

import pymysql  # type: ignore[import-untyped]
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.base import Base


class DatabaseState:
    def __init__(
        self,
        engine: Engine,
        session_factory: sessionmaker[Session],
        settings: Settings,
    ):
        self.engine = engine
        self.session_factory = session_factory
        self.settings = settings

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
        return cls(engine=engine, session_factory=session_factory, settings=settings)

    def initialize_schema(self) -> None:
        self.create_all()
        self.migrate_schema()

    def create_all(self) -> None:
        # Ensure ORM models are imported before SQLAlchemy reflects metadata.
        from app.db import models  # noqa: F401

        Base.metadata.create_all(bind=self.engine)

    def migrate_schema(self) -> None:
        inspector = inspect(self.engine)
        table_names = set(inspector.get_table_names())
        if "sessions" not in table_names:
            return

        columns = {str(column["name"]) for column in inspector.get_columns("sessions")}
        indexes = {str(index["name"]) for index in inspector.get_indexes("sessions")}
        owner_index_name = "ix_sessions_owner_username"

        with self.engine.begin() as connection:
            if "owner_username" not in columns:
                connection.execute(
                    text("ALTER TABLE sessions ADD COLUMN owner_username VARCHAR(255) NULL")
                )
            if "runtime_thread_id" not in columns:
                connection.execute(
                    text("ALTER TABLE sessions ADD COLUMN runtime_thread_id VARCHAR(255) NULL")
                )

            connection.execute(
                text(
                    "UPDATE sessions "
                    "SET owner_username = :username "
                    "WHERE owner_username IS NULL"
                ),
                {"username": self.settings.admin_username},
            )

            if owner_index_name not in indexes:
                connection.execute(
                    text("CREATE INDEX ix_sessions_owner_username ON sessions (owner_username)")
                )

        if "messages" not in table_names:
            return

        message_columns = {str(column["name"]) for column in inspector.get_columns("messages")}
        message_indexes = {str(index["name"]) for index in inspector.get_indexes("messages")}
        message_type_index_name = "ix_messages_message_type"

        with self.engine.begin() as connection:
            if "message_type" not in message_columns:
                connection.execute(
                    text(
                        "ALTER TABLE messages ADD COLUMN message_type "
                        "VARCHAR(32) DEFAULT 'message' NOT NULL"
                    )
                )
            if "visibility" not in message_columns:
                connection.execute(
                    text(
                        "ALTER TABLE messages ADD COLUMN visibility "
                        "VARCHAR(16) DEFAULT 'visible' NOT NULL"
                    )
                )
            if "source" not in message_columns:
                connection.execute(text("ALTER TABLE messages ADD COLUMN source VARCHAR(255) NULL"))
            if "injection_position" not in message_columns:
                connection.execute(
                    text("ALTER TABLE messages ADD COLUMN injection_position VARCHAR(32) NULL")
                )

            connection.execute(
                text(
                    "UPDATE messages SET message_type = 'message' "
                    "WHERE message_type IS NULL OR message_type = ''"
                )
            )
            connection.execute(
                text(
                    "UPDATE messages SET visibility = 'visible' "
                    "WHERE visibility IS NULL OR visibility = ''"
                )
            )
            connection.execute(
                text(
                    "UPDATE messages SET injection_position = CASE injection_position "
                    "WHEN 'before_history' THEN 'before_system' "
                    "WHEN 'before_run_prompt' THEN 'before_user' "
                    "WHEN 'after_run_prompt' THEN 'after_user' "
                    "WHEN 'after_history' THEN 'after_user' "
                    "ELSE injection_position END "
                    "WHERE injection_position IN ("
                    "'before_history', 'before_run_prompt', 'after_run_prompt', 'after_history'"
                    ")"
                )
            )

            if message_type_index_name not in message_indexes:
                connection.execute(
                    text("CREATE INDEX ix_messages_message_type ON messages (message_type)")
                )

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
