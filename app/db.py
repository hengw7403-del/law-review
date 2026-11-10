"""SQLite engine, sessions and schema initialization (Python 3.9)."""

import os
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_PATH = PROJECT_ROOT / "law.db"
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "sqlite:///" + DATABASE_PATH.as_posix()
)

Base = declarative_base()


def create_db_engine(database_url: str) -> Engine:
    db_engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False, "timeout": 30.0},
    )

    @event.listens_for(db_engine, "connect")
    def configure_sqlite(dbapi_connection, connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute("PRAGMA busy_timeout = 30000")
            # WAL persists on disk; timeout and synchronous are connection-local.
            # Configure before SQLAlchemy starts a transaction, including clients
            # that create an engine without calling init_db (CLI/tests).
            cursor.execute("PRAGMA journal_mode = WAL")
            cursor.execute("PRAGMA synchronous = NORMAL")
        finally:
            cursor.close()

    return db_engine


engine = create_db_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def init_db() -> None:
    # Register every table before create_all. No accounts or business data seeded.
    from app import models  # noqa: F401

    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode = WAL")
    Base.metadata.create_all(bind=engine)
    from app.migrations import add_user_org_name
    add_user_org_name(engine)
    from app.migrations import allow_preview_llm_logs, add_applicability_and_preamble
    allow_preview_llm_logs(engine)
    add_applicability_and_preamble(engine)
    from app.migrations import add_ticket_tables
    add_ticket_tables(engine)
    from app.migrations import ticket_auto_review
    ticket_auto_review(engine)


def check_database_ready() -> None:
    """Read the schema and acquire a real write transaction, then roll it back."""
    with engine.connect() as connection:
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            connection.exec_driver_sql("SELECT id FROM users LIMIT 1")
            # A main-database write, without creating rows or an eighth table.
            connection.exec_driver_sql("UPDATE users SET active = active WHERE 0")
        finally:
            connection.rollback()
