from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

import httpx
from jose import jwt
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app import db
from app.cli import main as cli_main
from app.models import Clause, Contract, LLMLog, ReviewItem, ReviewTask, User
from app.services.auth import create_user, signing_key
from tests.parse_helpers import ROOT, PreviewServer


class AuthAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.path = Path(cls.directory.name) / "law.db"
        cls.engine = db.create_db_engine("sqlite:///" + cls.path.as_posix())
        db.Base.metadata.create_all(cls.engine)
        cls.Session = sessionmaker(bind=cls.engine, expire_on_commit=False)
        cls.ids = {}
        for username, role in (("admin", "admin"), ("reviewer01", "reviewer"), ("reviewer02", "reviewer")):
            cls.ids[username] = create_user(cls.Session, username, role, "test-private-password")["user_id"]
        cls.server = PreviewServer()
        cls.server.start(cls.path, env_overrides={"DEEPSEEK_API_KEY": "", "LLM_TEST_CALL_LIMIT": "0",
                                                "CONTRACT_UPLOAD_DIR": str(Path(cls.directory.name) / "uploads")}, authenticated=False)
        cls.http = httpx.Client(base_url=cls.server.url, trust_env=False)
        cls.tokens = {}
        for username in cls.ids:
            response = cls.http.post("/auth/login", json={"username": username, "password": "test-private-password"})
            assert response.status_code == 200, response.text
            cls.tokens[username] = response.json()["access_token"]

    @classmethod
    def tearDownClass(cls):
        cls.http.close()
        cls.server.stop()
        cls.engine.dispose()
        cls.directory.cleanup()

    def headers(self, user):
        return {"Authorization": "Bearer " + self.tokens[user]}

    def upload(self):
        response = self.http.post("/contracts", headers=self.headers("reviewer01"),
                                  data={"owner_id": self.ids["reviewer02"]},
                                  files={"file": ("contract_a.txt", (ROOT / "samples/contract_a.txt").read_bytes(), "text/plain")})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["owner_id"], self.ids["reviewer01"])
        return response.json()["contract_id"]

    def test_every_route_requires_bearer_except_login_and_health(self):
        for method, path in (("GET", "/contracts"), ("POST", "/contracts"), ("POST", "/parse/preview"),
                             ("POST", "/search"), ("POST", "/judge/preview"), ("GET", "/tasks/guess"),
                             ("GET", "/reviews/guess/report"), ("GET", "/admin/users"), ("GET", "/auth/me")):
            with self.subTest(path=path):
                self.assertEqual(self.http.request(method, path).status_code, 401)
        self.assertEqual(self.http.get("/health/live").status_code, 200)
        self.assertEqual(self.http.get("/health/ready").status_code, 200)
        for path in ("/docs", "/openapi.json", "/redoc", "/login.html", "/contract-list.html", "/contract-detail.html"):
            self.assertEqual(self.http.get(path).status_code, 200)

    def test_login_uniform_failure_and_no_password_leak(self):
        wrong = self.http.post("/auth/login", json={"username": "reviewer01", "password": "wrong-password"})
        absent = self.http.post("/auth/login", json={"username": "does-not-exist", "password": "wrong-password"})
        self.assertEqual((wrong.status_code, wrong.json()), (absent.status_code, absent.json()))
        self.assertEqual(wrong.json(), {"detail": "账号或密码错误"})
        data = self.http.get("/auth/me", headers=self.headers("reviewer01")).json()
        self.assertEqual(data["user_id"], self.ids["reviewer01"])
        self.assertNotIn("password", json.dumps(data))
        token = jwt.decode(self.tokens["reviewer01"], signing_key(), algorithms=["HS256"])
        self.assertEqual(token["exp"] - token["iat"], 86400)
        self.assertEqual(token["auth_version"], 1)

    def test_contract_task_report_owner_admin_and_assignment_permissions(self):
        cid = self.upload()
        for name, expected in (("reviewer01", 200), ("reviewer02", 404), ("admin", 200)):
            self.assertEqual(self.http.get("/contracts/" + cid, headers=self.headers(name)).status_code, expected)
        listed = self.http.get("/contracts", headers=self.headers("reviewer02")).json()["contracts"]
        self.assertNotIn(cid, [c["contract_id"] for c in listed])
        self.assertEqual(self.http.post("/contracts/" + cid + "/review", json={}, headers=self.headers("reviewer02")).status_code, 404)
        created = self.http.post("/contracts/" + cid + "/review", headers=self.headers("reviewer01"),
                                 json={"rules": [], "missing_rules": ["delivery_default"]})
        self.assertEqual(created.status_code, 202, created.text)
        tid = created.json()["task_id"]
        with self.Session.begin() as session:
            task = session.get(ReviewTask, tid)
            self.assertEqual(task.created_by, self.ids["reviewer01"])
            snapshot = json.loads(task.input_snapshot)
            session.add(ReviewItem(id=uuid.uuid4().hex, review_id=tid, clause_id=None, rule_id="delivery_default",
                                   item_key="contract:delivery_default", execution_status="done", check_status="supported", risk_level="medium",
                                   machine_result=json.dumps({"has_gap": True, "gap_description": "测试缺口说明供本地验证使用。", "impact": "责任计算口径不清会增加追偿举证负担。", "suggestion": "明确责任触发条件和金额计算方式。",
                                       "evidence": [{"clause_id": c["id"]} for c in snapshot["all_clauses"]]}, ensure_ascii=False)))
            task.status, task.done_items = "completed", 1
        for name, expected in (("reviewer01", 200), ("reviewer02", 404), ("admin", 200)):
            self.assertEqual(self.http.get("/tasks/" + tid, headers=self.headers(name)).status_code, expected)
            self.assertEqual(self.http.get("/reviews/" + tid + "/report", headers=self.headers(name)).status_code, expected)
        report = self.http.get("/reviews/" + tid + "/report", headers=self.headers("admin")).text
        self.assertIn("依据（3 条）", report)
        self.assertNotIn("合同条款摘要（非法律依据），", report)
        self.assertIn("第五条 验收", report)
        self.assertIn("第九条 运维", report)
        self.assertIn("第十条 违约", report)
        with self.Session() as session:
            self.assertEqual(len(session.execute(select(LLMLog)).all()), 0)
        assigned = self.http.patch("/admin/contracts/" + cid + "/reviewer", headers=self.headers("admin"),
                                   json={"reviewer_id": self.ids["reviewer02"]})
        self.assertEqual(assigned.status_code, 200)
        self.assertEqual(self.http.get("/contracts/" + cid, headers=self.headers("reviewer02")).status_code, 200)
        self.assertEqual(self.http.get("/reviews/" + tid + "/report", headers=self.headers("reviewer02")).status_code, 200)

    def test_admin_interface_reviewer_forbidden_and_output_safe(self):
        self.assertEqual(self.http.get("/admin/users", headers=self.headers("reviewer01")).status_code, 403)
        response = self.http.get("/admin/users", headers=self.headers("admin"))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("password", response.text)

    def test_revoked_disabled_expired_and_forged_tokens_fail(self):
        uid = self.ids["reviewer02"]
        try:
            with self.Session.begin() as session:
                session.get(User, uid).auth_version = 2
            self.assertEqual(self.http.get("/auth/me", headers=self.headers("reviewer02")).status_code, 401)
            with self.Session.begin() as session:
                user = session.get(User, uid)
                user.auth_version, user.active = 1, 0
            self.assertEqual(self.http.get("/auth/me", headers=self.headers("reviewer02")).status_code, 401)
            disabled = self.http.post("/auth/login", json={"username": "reviewer02", "password": "test-private-password"})
            self.assertEqual(disabled.json(), {"detail": "账号或密码错误"})
        finally:
            with self.Session.begin() as session:
                user = session.get(User, uid)
                user.auth_version, user.active = 1, 1
        for key, exp in ((signing_key(), datetime.now(timezone.utc) - timedelta(seconds=5)), ("forged-not-the-real-signing-key", datetime.now(timezone.utc) + timedelta(days=1))):
            token = jwt.encode({"user_id": uid, "auth_version": 1, "exp": exp}, key, algorithm="HS256")
            self.assertEqual(self.http.get("/auth/me", headers={"Authorization": "Bearer " + token}).status_code, 401)

    def test_cli_password_prompt_and_no_password_in_output(self):
        output = StringIO()
        with patch("sys.argv", ["app.cli", "create-user", "--username", "cli-reviewer", "--role", "reviewer"]), \
             patch("app.cli.getpass", return_value="private-cli-password"), patch("app.cli.db.init_db"), \
             patch("app.cli.db.SessionLocal", self.Session), redirect_stdout(output):
            self.assertEqual(cli_main(), 0)
        self.assertNotIn("private-cli-password", output.getvalue())
        with self.Session() as session:
            user = session.scalar(select(User).where(User.username == "cli-reviewer"))
            self.assertTrue(user.password_hash.startswith("$2"))
            self.assertNotEqual(user.password_hash, "private-cli-password")
