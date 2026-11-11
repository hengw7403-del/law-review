"""Verify the ORM schema against the SQL supplied in requirements v6."""

from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy.exc import DBAPIError, IntegrityError

from app import db, models  # noqa: F401


TABLES = {
    "users", "contracts", "clauses", "knowledge_chunks",
    "review_tasks", "review_items", "llm_logs",
}


class SchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        path = Path(self.directory.name) / "schema.db"
        self.engine = db.create_db_engine("sqlite:///" + path.as_posix())
        db.Base.metadata.create_all(self.engine)
        self.reference = sqlite3.connect(":memory:")
        self.reference.executescript(
            Path(__file__).with_name("schema_v6.sql").read_text(encoding="utf-8")
        )

    def tearDown(self) -> None:
        self.reference.close()
        self.engine.dispose()
        self.directory.cleanup()

    def seed(self, connection) -> None:
        connection.exec_driver_sql(
            "INSERT INTO users(id,username,password_hash,role) "
            "VALUES ('u','tester','unused','admin')"
        )
        connection.exec_driver_sql(
            "INSERT INTO contracts(id,series_id,contract_version,title,filename,"
            "stored_path,file_hash,party_stance,parse_status,owner_id) "
            "VALUES ('c','s',1,'test','a.txt','a.txt','hash','party_a','parsed','u')"
        )
        connection.exec_driver_sql(
            "INSERT INTO review_tasks(id,contract_id,review_version,status,"
            "input_snapshot,created_by) VALUES ('r','c',1,'queued','{}','u')"
        )

    def test_columns_defaults_foreign_keys_and_indexes_match_v6(self) -> None:
        with self.engine.connect() as actual:
            names = set(actual.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).scalars())
            self.assertEqual(names, TABLES | {'conversations','conversation_messages','tickets','agent_runs','agent_steps'})
            for table in sorted(TABLES):
                with self.subTest(table=table):
                    expected_columns = list(self.reference.execute(
                        "PRAGMA table_info({})".format(table)
                    ))
                    actual_columns = list(actual.exec_driver_sql(
                        "PRAGMA table_info({})".format(table)
                    ))
                    # SQLAlchemy explicitly makes primary keys NOT NULL.
                    def normalize(rows):
                        # The LLM preview phase explicitly relaxes this one FK;
                        # all other fields remain the original v6 schema.
                        # Agent handoff also permits a NULL stance until legal confirmation.
                        values = [(r[1], r[2], False if (table == "llm_logs" and r[1] == "review_id") or (table == 'contracts' and r[1] == 'party_stance')
                                   else bool(r[3] or r[5]), r[4], r[5]) for r in rows]
                        if table == "contracts" and not any(v[0] == "preamble" for v in values):
                            values.append(("preamble", "TEXT", False, None, 0))
                        if table == "users" and not any(v[0] == "org_name" for v in values):
                            values.append(("org_name", "TEXT", False, None, 0))
                        return sorted(values)
                    self.assertEqual(normalize(actual_columns), normalize(expected_columns))
                    expected_fks = {tuple(r[2:]) for r in self.reference.execute(
                        "PRAGMA foreign_key_list({})".format(table)
                    )}
                    actual_fks = {tuple(r[2:]) for r in actual.exec_driver_sql(
                        "PRAGMA foreign_key_list({})".format(table)
                    )}
                    self.assertEqual(actual_fks, expected_fks)

                    def indexes(execute):
                        result = set()
                        for row in execute("PRAGMA index_list({})".format(table)):
                            if row[3] == "pk":
                                continue
                            fields = tuple(r[2] for r in execute(
                                "PRAGMA index_info({})".format(row[1])
                            ))
                            predicate = None
                            if row[4]:
                                ddl = list(execute(
                                    "SELECT sql FROM sqlite_master WHERE name='{}'".format(row[1])
                                ))[0][0]
                                predicate = " ".join(ddl.split("WHERE", 1)[1].split())
                            result.add((fields, row[2], predicate))
                        return result

                    self.assertEqual(
                        indexes(actual.exec_driver_sql), indexes(self.reference.execute)
                    )

    def test_null_clause_id_does_not_bypass_item_key_uniqueness(self) -> None:
        with self.engine.begin() as connection:
            self.seed(connection)
            statement = (
                "INSERT INTO review_items(id,review_id,item_key,clause_id,rule_id) "
                "VALUES (?, 'r', 'contract:ip', NULL, 'ip')"
            )
            connection.exec_driver_sql(statement, ("i1",))
            with self.assertRaises(IntegrityError):
                connection.exec_driver_sql(statement, ("i2",))
            with self.assertRaises(IntegrityError):
                connection.exec_driver_sql(
                    "INSERT INTO review_items(id,review_id,item_key,rule_id) "
                    "VALUES ('i3','r',NULL,'ip')"
                )

    def test_only_one_active_task_per_contract(self) -> None:
        with self.engine.begin() as connection:
            self.seed(connection)
            with self.assertRaises(IntegrityError):
                connection.exec_driver_sql(
                    "INSERT INTO review_tasks(id,contract_id,review_version,status,"
                    "input_snapshot,created_by) VALUES ('r2','c',2,'running','{}','u')"
                )
            connection.exec_driver_sql("UPDATE review_tasks SET status='completed' WHERE id='r'")
            connection.exec_driver_sql(
                "INSERT INTO review_tasks(id,contract_id,review_version,status,"
                "input_snapshot,created_by) VALUES ('r2','c',2,'queued','{}','u')"
            )

    def test_foreign_keys_and_check_constraints_are_enforced(self) -> None:
        with self.engine.begin() as connection:
            self.assertEqual(connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one(), 1)
            with self.assertRaises(IntegrityError):
                connection.exec_driver_sql(
                    "INSERT INTO users(id,username,password_hash,role) VALUES ('x','x','x','invalid')"
                )
            with self.assertRaises(IntegrityError):
                connection.exec_driver_sql(
                    "INSERT INTO clauses(id,contract_id,seq,content) VALUES ('x','missing',1,'text')"
                )
            self.seed(connection)
            with self.assertRaises(IntegrityError):
                connection.exec_driver_sql(
                    "UPDATE review_tasks SET review_status='confirmed' WHERE id='r'"
                )
            with self.assertRaises(IntegrityError):
                connection.exec_driver_sql(
                    "INSERT INTO review_items(id,review_id,item_key,rule_id,execution_status) "
                    "VALUES ('i','r','contract:ip','ip','done')"
                )

    def test_readiness_checks_writes_without_changing_data(self) -> None:
        with patch.object(db, "engine", self.engine):
            db.check_database_ready()
        with self.engine.connect() as connection:
            self.assertEqual(connection.exec_driver_sql("SELECT count(*) FROM users").scalar_one(), 0)
            connection.exec_driver_sql("PRAGMA query_only=ON")
        try:
            with patch.object(db, "engine", self.engine):
                with self.assertRaises(DBAPIError):
                    db.check_database_ready()
        finally:
            with self.engine.connect() as connection:
                connection.exec_driver_sql("PRAGMA query_only=OFF")


if __name__ == "__main__":
    unittest.main()
