from sqlalchemy import create_engine, inspect, text

from app.core.config import Settings
from app.core.database import DatabaseState
from app.db.manage import init_database
from app.db.models import SessionRecord


def test_database_initializer_creates_configured_schema(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'fresh.db'}",
        admin_email="admin@example.com",
        admin_username="admin",
        admin_password="secret",
        admin_token_secret="test-secret-key-with-32-bytes-minimum",
        upload_storage_dir=tmp_path / "uploads",
    )

    init_database(settings)

    engine = create_engine(settings.database_url)
    try:
        inspector = inspect(engine)
        assert "sessions" in inspector.get_table_names()
        assert "owner_username" in {
            column["name"] for column in inspector.get_columns("sessions")
        }
    finally:
        engine.dispose()


def test_database_initializer_adds_session_owner_column_to_existing_schema(tmp_path) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'existing.db'}"
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE sessions (
                    id VARCHAR(36) PRIMARY KEY,
                    title VARCHAR(255),
                    status VARCHAR(32),
                    runtime_thread_id VARCHAR(255),
                    last_run_id VARCHAR(255),
                    extra JSON,
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO sessions (
                    id,
                    title,
                    status,
                    runtime_thread_id,
                    last_run_id,
                    extra,
                    created_at,
                    updated_at
                )
                VALUES (
                    'session-1',
                    'Existing',
                    'active',
                    NULL,
                    NULL,
                    '{}',
                    CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP
                )
                """
            )
        )
    engine.dispose()

    settings = Settings(
        database_url=database_url,
        admin_email="admin@example.com",
        admin_username="admin",
        admin_password="secret",
        admin_token_secret="test-secret-key-with-32-bytes-minimum",
        upload_storage_dir=tmp_path / "uploads",
    )
    database = DatabaseState.from_settings(settings)
    try:
        database.initialize_schema()
        inspector = inspect(database.engine)
        assert "owner_username" in {
            column["name"] for column in inspector.get_columns("sessions")
        }
        assert "ix_sessions_owner_username" in {
            index["name"] for index in inspector.get_indexes("sessions")
        }

        with database.session_factory() as db:
            session = db.query(SessionRecord).filter(SessionRecord.id == "session-1").first()
            assert session is not None
            assert session.owner_username == "admin"
    finally:
        database.dispose()


def test_mysql_database_is_created_before_engine_setup(monkeypatch) -> None:
    executed: list[str] = []

    class FakeCursor:
        def __enter__(self) -> "FakeCursor":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def execute(self, statement: str) -> None:
            executed.append(statement)

    class FakeConnection:
        def cursor(self) -> FakeCursor:
            return FakeCursor()

        def close(self) -> None:
            return None

    def fake_connect(**kwargs: object) -> FakeConnection:
        assert kwargs["host"] == "127.0.0.1"
        assert kwargs["port"] == 3306
        assert kwargs["user"] == "root"
        assert kwargs["password"] == "secret"
        return FakeConnection()

    monkeypatch.setattr("app.core.database.pymysql.connect", fake_connect)
    settings = Settings(
        database_url="mysql+pymysql://root:secret@127.0.0.1:3306/deepagents_platform",
        admin_email="admin@example.com",
        admin_username="admin",
        admin_password="secret",
        admin_token_secret="test-secret-key-with-32-bytes-minimum",
    )

    database = DatabaseState.from_settings(settings)
    database.dispose()

    assert executed == [
        "CREATE DATABASE IF NOT EXISTS `deepagents_platform` "
        "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
    ]
