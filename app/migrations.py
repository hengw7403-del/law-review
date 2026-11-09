"""Small idempotent migration for the existing seven-table skeleton."""

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Text, inspect
from sqlalchemy.engine import Engine


def allow_preview_llm_logs(engine: Engine) -> None:
    with engine.begin() as connection:
        columns = inspect(connection).get_columns("llm_logs")
        review_column = next(column for column in columns if column["name"] == "review_id")
        if review_column["nullable"]:
            return
        # Explicit BEGIN keeps SQLite DDL and the data copy in one transaction.
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        operations = Operations(MigrationContext.configure(connection))
        with operations.batch_alter_table("llm_logs", recreate="always") as batch:
            batch.alter_column("review_id", existing_type=Text(), nullable=True)


def add_applicability_and_preamble(engine: Engine) -> None:
    from app.models import ReviewItem
    with engine.begin() as connection:
        if "preamble" not in {c["name"] for c in inspect(connection).get_columns("contracts")}:
            connection.exec_driver_sql("ALTER TABLE contracts ADD COLUMN preamble TEXT")
    with engine.connect() as connection:
        ddl = connection.exec_driver_sql("SELECT sql FROM sqlite_master WHERE name='review_items'").scalar_one()
        connection.commit()
        if "not_applicable" in ddl:
            return
        # llm_logs references this table: disable FK enforcement only on this
        # startup migration connection, copy atomically, then verify and restore.
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            operations = Operations(MigrationContext.configure(connection))
            with operations.batch_alter_table("review_items", copy_from=ReviewItem.__table__, recreate="always"):
                pass
            if connection.exec_driver_sql("PRAGMA foreign_key_check").all():
                raise RuntimeError("Migration foreign-key validation failed")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()
