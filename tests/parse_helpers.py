"""Synthetic in-memory documents and an actual-HTTP preview test server."""

from io import BytesIO
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener
import uuid

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parent.parent


def text_pdf(pages: List[str]) -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        ("<< /Type /Pages /Kids [{}] /Count {} >>".format(
            " ".join("{} 0 R".format(4 + 2 * i) for i in range(len(pages))), len(pages)
        )).encode("ascii"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for i, page in enumerate(pages):
        objects.append((
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            "/Resources << /Font << /F1 3 0 R >> >> /Contents {} 0 R >>"
        ).format(5 + i * 2).encode("ascii"))
        lines = []
        for line in page.splitlines():
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            lines.append("({}) Tj T*".format(escaped))
        stream = ("BT /F1 12 Tf 14 TL 50 750 Td " + " ".join(lines) + " ET").encode("ascii")
        objects.append(b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream")
    result = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend("{} 0 obj\n".format(number).encode("ascii") + obj + b"\nendobj\n")
    xref = len(result)
    result.extend("xref\n0 {}\n0000000000 65535 f \n".format(len(offsets)).encode("ascii"))
    for offset in offsets[1:]:
        result.extend("{:010d} 00000 n \n".format(offset).encode("ascii"))
    result.extend(("trailer\n<< /Size {} /Root 1 0 R >>\nstartxref\n{}\n%%EOF\n".format(
        len(offsets), xref)).encode("ascii"))
    return bytes(result)


def image_pdf() -> bytes:
    image = Image.new("RGB", (400, 200), "white")
    ImageDraw.Draw(image).text((20, 50), "SCANNED CONTRACT - IMAGE ONLY", fill="black")
    output = BytesIO()
    image.save(output, format="PDF")
    return output.getvalue()


class PreviewServer:
    def start(self, database_path: Optional[Path] = None, env_overrides: Optional[Dict[str, str]] = None,
              authenticated: bool = True) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.database = database_path.resolve() if database_path is not None else Path(self.directory.name) / "preview.db"
        self.log_path = Path(self.directory.name) / "server.log"
        self.log = self.log_path.open("w", encoding="utf-8")
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        self.url = "http://127.0.0.1:{}".format(port)
        self.opener = build_opener(ProxyHandler({}))
        environment = os.environ.copy()
        environment["DATABASE_URL"] = "sqlite:///" + self.database.as_posix()
        environment["PYTHONUTF8"] = "1"
        environment["AGENT_RUN_DIR"] = str(Path(self.directory.name) / "agent-runs")
        if env_overrides:
            environment.update(env_overrides)
        self.process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=str(ROOT), env=environment, stdout=self.log, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    break
                try:
                    with self.opener.open(self.url + "/health/ready", timeout=1) as response:
                        if response.status == 200:
                            if authenticated:
                                self.authenticate_test_user()
                            return
                except (OSError, URLError):
                    time.sleep(0.1)
            raise RuntimeError(self.log_path.read_text(encoding="utf-8"))
        except Exception:
            self.stop()
            raise

    def authenticate_test_user(self):
        # Test-only account and real login; production has no auth bypass.
        from app.db import create_db_engine
        from app.models import User
        from app.services.auth import create_user
        from sqlalchemy import select
        from sqlalchemy.orm import sessionmaker
        engine = create_db_engine("sqlite:///" + self.database.as_posix())
        sf = sessionmaker(bind=engine)
        try:
            with sf() as session:
                exists = session.scalar(select(User.id).where(User.username == "preview-test-admin"))
            if not exists:
                create_user(sf, "preview-test-admin", "admin", "preview-test-password")
            status, payload = self.post_json("/auth/login", {"username": "preview-test-admin", "password": "preview-test-password"})
            if status != 200:
                raise RuntimeError("测试账号登录失败")
            self.opener.addheaders = [("Authorization", "Bearer " + payload["access_token"])]
        finally:
            engine.dispose()

    def stop(self) -> None:
        if self.process.poll() is None:
            if os.name == "nt":
                # A venv redirector can spawn another python.exe on Windows.
                # Stop our entire child tree before deleting its SQLite files.
                subprocess.run(
                    ["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                )
            else:
                self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.log.close()
        self.directory.cleanup()

    def upload(self, filename: str, data: bytes) -> Tuple[int, Dict[str, Any]]:
        boundary = "law-preview-" + uuid.uuid4().hex
        body = (
            '--{}\r\nContent-Disposition: form-data; name="file"; filename="{}"\r\n'
            'Content-Type: application/octet-stream\r\n\r\n'
        ).format(boundary, filename).encode("utf-8")
        body += data + ("\r\n--{}--\r\n".format(boundary)).encode("ascii")
        request = Request(self.url + "/parse/preview", data=body, method="POST", headers={
            "Content-Type": "multipart/form-data; boundary=" + boundary,
        })
        try:
            with self.opener.open(request, timeout=30) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            try:
                return exc.code, json.load(exc)
            finally:
                exc.close()

    def post_json(self, path: str, payload: Dict[str, Any], timeout: float = 30) -> Tuple[int, Dict[str, Any]]:
        request = Request(self.url + path, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                          method="POST", headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=timeout) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            try:
                return exc.code, json.load(exc)
            finally:
                exc.close()
