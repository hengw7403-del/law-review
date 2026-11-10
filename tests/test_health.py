"""Real HTTP smoke checks with Uvicorn, using an isolated temporary database."""

import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, build_opener


class HealthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.directory = tempfile.TemporaryDirectory()
        cls.database = Path(cls.directory.name) / "health.db"
        cls.log_path = Path(cls.directory.name) / "uvicorn.log"
        cls.log = cls.log_path.open("w", encoding="utf-8")
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        cls.base_url = "http://127.0.0.1:{}".format(port)
        cls.opener = build_opener(ProxyHandler({}))
        environment = os.environ.copy()
        environment["DATABASE_URL"] = "sqlite:///" + cls.database.as_posix()
        environment["PYTHONUTF8"] = "1"
        cls.process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=str(Path(__file__).resolve().parent.parent),
            env=environment,
            stdout=cls.log,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if cls.process.poll() is not None:
                    break
                try:
                    with cls.opener.open(cls.base_url + "/health/live", timeout=1) as response:
                        if response.status == 200:
                            return
                except (OSError, URLError):
                    time.sleep(0.1)
            raise RuntimeError("Uvicorn startup failed: " + cls.log_path.read_text(encoding="utf-8"))
        except Exception:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.process.poll() is None:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(cls.process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                )
            else:
                cls.process.terminate()
            try:
                cls.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                cls.process.kill()
                cls.process.wait(timeout=5)
        cls.log.close()
        cls.directory.cleanup()

    def test_live_and_ready_over_http(self) -> None:
        for path in ("/health/live", "/health/ready"):
            with self.subTest(path=path):
                with self.opener.open(self.base_url + path, timeout=10) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(json.load(response), {"status": "ok"})

    def test_startup_creates_exactly_ten_empty_tables(self) -> None:
        self.assertTrue(self.database.is_file())
        with sqlite3.connect(str(self.database)) as connection:
            names = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            self.assertEqual(names, {
                "users", "contracts", "clauses", "knowledge_chunks",
                "review_tasks", "review_items", "llm_logs",
                "conversations", "conversation_messages", "tickets",
            })
            for table in names:
                self.assertEqual(connection.execute(
                    "SELECT count(*) FROM {}".format(table)
                ).fetchone()[0], 0)

    def test_ready_returns_503_on_database_failure_while_live_stays_ok(self) -> None:
        with sqlite3.connect(str(self.database)) as connection:
            connection.execute("ALTER TABLE users RENAME TO unavailable_users")
        try:
            with self.assertRaises(HTTPError) as error:
                self.opener.open(self.base_url + "/health/ready", timeout=10)
            self.assertEqual(error.exception.code, 503)
            self.assertEqual(json.load(error.exception), {"status": "unavailable"})
            error.exception.close()
            with self.opener.open(self.base_url + "/health/live", timeout=10) as response:
                self.assertEqual(response.status, 200)
        finally:
            with sqlite3.connect(str(self.database)) as connection:
                connection.execute("ALTER TABLE unavailable_users RENAME TO users")


if __name__ == "__main__":
    unittest.main()
