"""Local acceptance with real bcrypt/JWT and HTTP; no worker or LLM calls."""
import json
from pathlib import Path
import secrets
import sqlite3

import httpx
from sqlalchemy import func, select

from app import db
from app.models import LLMLog, User
from app.services.auth import create_user
from tests.parse_helpers import ROOT, PreviewServer


def main():
    result_path = ROOT / "work/auth-acceptance-results.json"
    private_path = ROOT / "work/initial-user-credentials.json"
    if result_path.exists() or private_path.exists():
        raise RuntimeError("已有账号初始化记录，不自动覆盖密码或重复上传合同")
    db.init_db()
    with db.SessionLocal() as session:
        for name in ("admin", "reviewer01", "reviewer02"):
            if session.scalar(select(User.id).where(User.username == name)):
                raise RuntimeError("种子账号已存在，不覆盖已有密码")
        log_count = session.scalar(select(func.count()).select_from(LLMLog))
    backup = ROOT / "work/backups/law-before-auth.db"
    if backup.exists():
        raise RuntimeError("已有迁移前备份，需核对初始化状态")
    with sqlite3.connect(str(ROOT / "law.db")) as source, sqlite3.connect(str(backup)) as target:
        source.backup(target)
    credentials = {name: {"password": secrets.token_urlsafe(24), "role": role}
                   for name, role in (("admin", "admin"), ("reviewer01", "reviewer"), ("reviewer02", "reviewer"))}
    # Private, Git-ignored file. Never include passwords/tokens in reports/stdout.
    with private_path.open("x", encoding="utf-8") as output:
        json.dump(credentials, output, ensure_ascii=False, indent=2)
    for name, value in credentials.items():
        create_user(db.SessionLocal, name, value["role"], value["password"])
    server = PreviewServer()
    try:
        server.start(ROOT / "law.db", {"DEEPSEEK_API_KEY": "", "LLM_TEST_CALL_LIMIT": "0"}, authenticated=False)
        with httpx.Client(base_url=server.url, trust_env=False) as client:
            tokens = {}
            for name, value in credentials.items():
                response = client.post("/auth/login", json={"username": name, "password": value["password"]})
                assert response.status_code == 200
                tokens[name] = response.json()["access_token"]
                assert response.json()["expires_in"] == 86400
            headers = {name: {"Authorization": "Bearer " + token} for name, token in tokens.items()}
            state = {"checks": {}, "accounts": list(credentials)}
            state["checks"]["anonymous_contracts"] = client.get("/contracts").status_code
            uploaded = client.post("/contracts", headers=headers["reviewer01"],
                                   files={"file": ("contract_a.txt", (ROOT / "samples/contract_a.txt").read_bytes(), "text/plain")})
            assert uploaded.status_code == 201
            cid = uploaded.json()["contract_id"]
            state["contract_id"] = cid
            state["checks"]["reviewer01_own_contract"] = client.get("/contracts/" + cid, headers=headers["reviewer01"]).status_code
            state["checks"]["reviewer02_other_contract"] = client.get("/contracts/" + cid, headers=headers["reviewer02"]).status_code
            state["checks"]["admin_contract"] = client.get("/contracts/" + cid, headers=headers["admin"]).status_code
            state["checks"]["reviewer01_admin_users"] = client.get("/admin/users", headers=headers["reviewer01"]).status_code
            state["checks"]["admin_users"] = client.get("/admin/users", headers=headers["admin"]).status_code
            bad = client.post("/auth/login", json={"username": "reviewer01", "password": "incorrect-password"})
            absent = client.post("/auth/login", json={"username": "non-existent-user", "password": "incorrect-password"})
            state["uniform_login_error"] = (bad.status_code == absent.status_code == 401 and bad.json() == absent.json() == {"detail": "账号或密码错误"})
            previous = json.loads((ROOT / "work/default-gap-live-results.json").read_text(encoding="utf-8"))
            tid = previous["task_id"]
            state["report_task_id"] = tid
            response = client.get("/reviews/" + tid + "/report", headers=headers["admin"])
            assert response.status_code == 200
            report = response.text
            assert "依据（3 条）" in report and "依据（2 条）" in report
            assert "合同条款摘要（非法律依据），" not in report
            (ROOT / "work/auth-compact-report.md").write_text(report, encoding="utf-8")
            state["compact_evidence_counts"] = [2, 3]
            state["checks"]["reviewer02_historical_report"] = client.get("/reviews/" + tid + "/report", headers=headers["reviewer02"]).status_code
            assert state["checks"] == {"anonymous_contracts": 401, "reviewer01_own_contract": 200,
                                        "reviewer02_other_contract": 404, "admin_contract": 200,
                                        "reviewer01_admin_users": 403, "admin_users": 200,
                                        "reviewer02_historical_report": 404}
            assert state["uniform_login_error"]
        with db.SessionLocal() as session:
            after = session.scalar(select(func.count()).select_from(LLMLog))
        state["llm_logs_before"], state["llm_logs_after"] = log_count, after
        state["model_calls_this_round"] = after - log_count
        assert after == log_count
        state["passed"] = True
        result_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(state, ensure_ascii=False, indent=2))
    finally:
        if hasattr(server, "process"):
            server.stop()
        db.engine.dispose()


if __name__ == "__main__":
    main()
