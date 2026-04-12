from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.base import Base


class DatabaseState:
    def __init__(self, engine: Engine, session_factory: sessionmaker[Session]):
        self.engine = engine
        self.session_factory = session_factory

    @classmethod
    def from_settings(cls, settings: Settings) -> "DatabaseState":
        connect_args = {"check_same_thread": False} if settings.is_sqlite else {}
        engine = create_engine(
            settings.database_url,
            future=True,
            pool_pre_ping=True,
            connect_args=connect_args,
        )
        session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        return cls(engine=engine, session_factory=session_factory)

    def create_all(self) -> None:
        Base.metadata.create_all(bind=self.engine)

    def session(self) -> Iterator[Session]:
        db = self.session_factory()
        try:
            yield db
        finally:
            db.close()

    def dispose(self) -> None:
        self.engine.dispose()
