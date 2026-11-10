"""Explicit test fixtures and owned subprocesses; never automatic production seeds."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Optional
import uuid

from sqlalchemy import select

from app.models import Clause, Contract, User
from app.services.parser import parse_document
from app.services.clause_splitter import split_document
from app.services.review_tasks import get_task
from tests.parse_helpers import ROOT


def seed_contract_a(session_factory) -> str:
    data = (ROOT / "samples/contract_a.txt").read_bytes()
    text, mapping = parse_document("contract_a.txt", data)
    document = split_document(text, mapping)
    clauses = document["clauses"]
    user_id, contract_id = uuid.uuid4().hex, uuid.uuid4().hex
    with session_factory.begin() as session:
        session.add(User(id=user_id, username="acceptance-" + user_id,
                         password_hash="!disabled-test-account", role="reviewer", active=0))
        session.flush()
        session.add(Contract(id=contract_id, series_id=contract_id, contract_version=1,
                             title="合同 A（受限任务验收样本）", filename="contract_a.txt",
                             stored_path=str(ROOT / "samples/contract_a.txt"),
                             file_hash=hashlib.sha256(data).hexdigest(), party_stance="party_a",
                             raw_text=text, preamble=document["preamble"], parse_status="parsed", owner_id=user_id, reviewer_id=user_id))
        session.flush()
        for clause in clauses:
            session.add(Clause(id=uuid.uuid4().hex, contract_id=contract_id, seq=clause["seq"],
                               original_no=clause["original_no"], content=clause["content"],
                               char_start=clause["char_start"], char_end=clause["char_end"],
                               location_json=json.dumps({"label": clause["location"]})))
    return contract_id


def stop_process_tree(process) -> None:
    if process.poll() is None:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        else:
            process.kill()
        process.wait(timeout=10)


def start_worker(database: Path, log_path: Path, mock: bool = True, delay: float = 0,
                 fail_seq: Optional[int] = None, pause_after: Optional[int] = None):
    environment = os.environ.copy()
    environment["DATABASE_URL"] = "sqlite:///" + database.resolve().as_posix()
    environment["PYTHONIOENCODING"] = "utf-8"
    command = [sys.executable, "-m", "tests.review_worker_process" if mock else "app.cli"]
    if mock:
        command += ["--delay", str(delay)]
        if fail_seq is not None:
            command += ["--fail-seq", str(fail_seq)]
        if pause_after is not None:
            command += ["--pause-after", str(pause_after)]
    else:
        command += ["run-worker", "--once", "--unit-delay", str(delay)]
    with log_path.open("w", encoding="utf-8") as log:
        return subprocess.Popen(command, cwd=str(ROOT), env=environment, stdout=log,
                                stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def wait_progress(session_factory, task_id: str, processed: int, process, timeout: float = 30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = get_task(session_factory, task_id)
        if state["processed_items"] >= processed:
            return state
        if process.poll() is not None:
            raise RuntimeError("worker exited before expected checkpoint")
        time.sleep(0.05)
    raise TimeoutError("worker checkpoint timeout")
