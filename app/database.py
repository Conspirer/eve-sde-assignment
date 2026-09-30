"""Database engine, session factory and the FastAPI `get_db` dependency."""
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


def make_engine(url: str) -> Engine:
    connect_args = {}
    if url.startswith("sqlite"):
        # SQLite only: allow the connection to be used across FastAPI's threads.
        connect_args = {"check_same_thread": False}
    return create_engine(url, connect_args=connect_args)


engine = make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    """Give each request its own DB session and always close it afterwards."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
