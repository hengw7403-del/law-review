import sqlite3
import tempfile
from pathlib import Path
import unittest

from app.db import create_db_engine


class DatabaseConcurrencyTests(unittest.TestCase):
    def test_existing_rollback_database_and_every_new_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'existing.db'
            with sqlite3.connect(str(path)) as original:
                original.execute('CREATE TABLE example (id INTEGER PRIMARY KEY, value TEXT)')
                original.execute("INSERT INTO example VALUES (1, 'preserved')")
                self.assertEqual(original.execute('PRAGMA journal_mode').fetchone()[0], 'delete')
            original.close()
            engine = create_db_engine('sqlite:///' + path.as_posix())
            try:
                for _ in range(2):
                    with engine.connect() as current:
                        self.assertEqual(current.exec_driver_sql('PRAGMA journal_mode').scalar(), 'wal')
                        self.assertEqual(current.exec_driver_sql('PRAGMA busy_timeout').scalar(), 30000)
                        self.assertEqual(current.exec_driver_sql('PRAGMA synchronous').scalar(), 1)
                        self.assertEqual(current.exec_driver_sql('SELECT value FROM example').scalar(), 'preserved')
                    engine.dispose()  # The next check uses a new physical connection.
            finally:
                engine.dispose()

    def test_reader_continues_during_write_and_exception_rolls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = create_db_engine('sqlite:///' + (Path(directory) / 'parallel.db').as_posix())
            try:
                with engine.begin() as current:
                    current.exec_driver_sql('CREATE TABLE example (value TEXT)')
                    current.exec_driver_sql("INSERT INTO example VALUES ('before')")
                with engine.connect() as writer, engine.connect() as reader:
                    writer.exec_driver_sql('BEGIN IMMEDIATE')
                    writer.exec_driver_sql("UPDATE example SET value='after'")
                    self.assertEqual(reader.exec_driver_sql('SELECT value FROM example').scalar(), 'before')
                    reader.rollback()
                    writer.commit()
                    self.assertEqual(reader.exec_driver_sql('SELECT value FROM example').scalar(), 'after')
                with self.assertRaises(RuntimeError):
                    with engine.begin() as writer:
                        writer.exec_driver_sql("UPDATE example SET value='rolled back'")
                        raise RuntimeError('rollback boundary')
                with engine.begin() as current:
                    self.assertEqual(current.exec_driver_sql('SELECT value FROM example').scalar(), 'after')
                    current.exec_driver_sql("UPDATE example SET value='next writer succeeds'")
            finally:
                engine.dispose()
