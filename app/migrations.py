"""Small idempotent migration for the existing seven-table skeleton."""

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Text, inspect
from sqlalchemy.engine import Engine


def add_agent_tables(engine: Engine) -> None:
    """Additive, idempotent; do not alter existing review/ticket records."""
    from app.models import AgentRun, AgentStep
    AgentRun.__table__.create(bind=engine, checkfirst=True)
    AgentStep.__table__.create(bind=engine, checkfirst=True)


def agent_stance_handoff(engine: Engine) -> None:
    """Idempotent parent rebuild; preserve IDs, children and foreign keys."""
    from app.models import Contract, Ticket
    with engine.connect() as connection:
        connection.exec_driver_sql('PRAGMA foreign_keys=OFF')
        connection.commit()
        try:
            connection.exec_driver_sql('BEGIN IMMEDIATE')
            columns = {c['name'] for c in inspect(connection).get_columns('tickets')}
            for name in ('source_system', 'source_contract_id', 'source_content_hash', 'agent_run_id'):
                if name not in columns:
                    connection.exec_driver_sql('ALTER TABLE tickets ADD COLUMN '+name+' TEXT')
            operations = Operations(MigrationContext.configure(connection))
            checks = inspect(connection).get_check_constraints('tickets')
            if not any("'needs_stance'" in c['sqltext'] for c in checks):
                with operations.batch_alter_table('tickets', copy_from=Ticket.__table__, recreate='always'):
                    pass
            stance = next(c for c in inspect(connection).get_columns('contracts') if c['name']=='party_stance')
            if not stance['nullable']:
                with operations.batch_alter_table('contracts', copy_from=Contract.__table__, recreate='always'):
                    pass
            if connection.exec_driver_sql('PRAGMA foreign_key_check').all():
                raise RuntimeError('Stance migration foreign-key validation failed')
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.exec_driver_sql('PRAGMA foreign_keys=ON')
            connection.commit()


def add_user_org_name(engine: Engine) -> None:
    with engine.begin() as connection:
        if "org_name" not in {c["name"] for c in inspect(connection).get_columns("users")}:
            connection.exec_driver_sql("ALTER TABLE users ADD COLUMN org_name TEXT")


def expand_user_roles(engine: Engine) -> None:
    """Atomically rebuild the referenced parent table; preserve all IDs/FKs."""
    from app.models import User
    with engine.connect() as connection:
        checks = inspect(connection).get_check_constraints("users")
        if any("role" in c["sqltext"] and "'requester'" in c["sqltext"] for c in checks):
            return
        connection.commit()
        # No parent-table rename: SQLite would rewrite references in children.
        # Enforcement is disabled only on this startup migration connection.
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            operations = Operations(MigrationContext.configure(connection))
            with operations.batch_alter_table("users", copy_from=User.__table__, recreate="always"):
                pass
            if connection.exec_driver_sql("PRAGMA foreign_key_check").all():
                raise RuntimeError("Role migration foreign-key validation failed")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()


def add_ticket_tables(engine: Engine) -> None:
    """Additive migration: does not rebuild any existing review table."""
    from app.models import Conversation, ConversationMessage, Ticket
    for model in (Conversation, ConversationMessage, Ticket):
        model.__table__.create(bind=engine, checkfirst=True)


def ticket_auto_review(engine: Engine) -> None:
    """Rebuild only tickets, preserving rows and converting legacy assigned."""
    from sqlalchemy import MetaData, Table, select, literal
    from app.models import Ticket
    with engine.connect() as connection:
        ddl = connection.exec_driver_sql("SELECT sql FROM sqlite_master WHERE name='tickets'").scalar_one()
        if 'high_risk_count' in ddl and "'needs_human'" in ddl and "'assigned'" not in ddl:
            return
        connection.exec_driver_sql('BEGIN IMMEDIATE')
        try:
            connection.exec_driver_sql('ALTER TABLE tickets RENAME TO tickets_before_auto_review')
            # Drop named indexes before creating their replacement names.
            for index in inspect(connection).get_indexes('tickets_before_auto_review'):
                connection.exec_driver_sql('DROP INDEX "'+index['name'].replace('"','""')+'"')
            old = Table('tickets_before_auto_review', MetaData(), autoload_with=connection)
            Ticket.__table__.create(connection)
            columns = [c.name for c in Ticket.__table__.columns if c.name in old.c]
            from sqlalchemy import case
            values = [case((old.c.status=='assigned','reviewing'),else_=old.c.status) if name=='status' else old.c[name] for name in columns]
            connection.execute(Ticket.__table__.insert().from_select(columns, select(*values)))
            connection.exec_driver_sql('DROP TABLE tickets_before_auto_review')
            if connection.exec_driver_sql('PRAGMA foreign_key_check').all():raise RuntimeError('Ticket migration foreign-key check failed')
            connection.commit()
        except Exception:
            connection.rollback();raise


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
        if "not_applicable" in ddl and "'merged'" in ddl:
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
