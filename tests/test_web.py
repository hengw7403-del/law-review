from pathlib import Path
import re
import tempfile
import unittest

import httpx
from sqlalchemy.orm import sessionmaker
from app import db
from app.services.auth import create_user
from tests.parse_helpers import ROOT, PreviewServer


class NativeWebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.path = Path(cls.directory.name) / "web.db"
        cls.engine = db.create_db_engine("sqlite:///" + cls.path.as_posix())
        db.Base.metadata.create_all(cls.engine)
        sf = sessionmaker(bind=cls.engine)
        for username in ("reviewer01", "reviewer02"):
            create_user(sf, username, "reviewer", "web-tests-only-password")
        cls.server = PreviewServer()
        cls.server.start(cls.path, {"DEEPSEEK_API_KEY": "", "LLM_TEST_CALL_LIMIT": "0",
                                  "CONTRACT_UPLOAD_DIR": str(Path(cls.directory.name) / "uploads")}, authenticated=False)
        cls.http = httpx.Client(base_url=cls.server.url, trust_env=False)
        cls.headers = {}
        for username in ("reviewer01", "reviewer02"):
            token = cls.http.post("/auth/login", json={"username": username, "password": "web-tests-only-password"}).json()["access_token"]
            cls.headers[username] = {"Authorization": "Bearer " + token}

    @classmethod
    def tearDownClass(cls):
        cls.http.close()
        cls.server.stop()
        cls.engine.dispose()
        cls.directory.cleanup()

    def test_static_and_docs_public_but_apis_stay_protected_and_offline(self):
        for path in ("/", "/login.html", "/contract-list.html", "/contract-detail.html", "/style.css", "/api.js", "/nav.js", "/docs", "/redoc", "/openapi.json"):
            response = self.http.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertNotRegex(response.text, r'(?:src|href)=[\"\']https?://')
        self.assertEqual(self.http.get("/contracts").status_code, 401)
        self.assertEqual(self.http.get("/contracts/guess/clauses").status_code, 401)

    def test_list_metadata_newest_first_and_paginated_clauses_respect_access(self):
        ids = []
        for title in ("earlier-contract", "newer-contract"):
            response = self.http.post("/contracts", headers=self.headers["reviewer01"], data={"title": title, "stance": "party_b"},
                                      files={"file": ("a.txt", (ROOT / "samples/contract_a.txt").read_bytes())})
            self.assertEqual(response.status_code, 201)
            ids.append(response.json()["contract_id"])
        items = self.http.get("/contracts", headers=self.headers["reviewer01"]).json()["contracts"]
        self.assertEqual([c["contract_id"] for c in items[:2]], list(reversed(ids)))
        self.assertEqual(items[0]["party_stance"], "party_b")
        self.assertTrue(items[0]["created_at"])
        self.assertEqual(items[0]["clause_count"], 12)
        self.assertIsNone(items[0]["latest_task"])
        path = "/contracts/" + ids[-1]
        detail = self.http.get(path, headers=self.headers["reviewer01"]).json()
        self.assertEqual(detail["clause_count"], 12)
        first = self.http.get(path + "/clauses?page=1&page_size=5", headers=self.headers["reviewer01"]).json()
        last = self.http.get(path + "/clauses?page=3&page_size=5", headers=self.headers["reviewer01"]).json()
        self.assertEqual((first["total"], len(first["clauses"]), len(last["clauses"])), (12, 5, 2))
        self.assertEqual([c["seq"] for c in last["clauses"]], [11, 12])
        self.assertEqual(self.http.get(path + "/clauses", headers=self.headers["reviewer02"]).status_code, 404)
        for query in ("?page=0", "?page_size=0", "?page_size=101"):
            self.assertEqual(self.http.get(path + "/clauses" + query, headers=self.headers["reviewer01"]).status_code, 422)
