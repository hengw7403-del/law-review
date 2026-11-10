"""Bcrypt authentication and DB-revalidated 24-hour JWT sessions."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import secrets
import uuid
from typing import Optional

import bcrypt
from fastapi import HTTPException, Request
from jose import JWTError, jwt
from sqlalchemy import select

from app import db
from app.config import load_environment
from app.models import User

EXPIRES_IN = 24 * 60 * 60
_DUMMY_HASH = bcrypt.hashpw(b"dummy-login-only", bcrypt.gensalt())


@dataclass(frozen=True)
class Principal:
    id: str
    username: str
    role: str
    auth_version: int
    org_name: Optional[str] = None


def signing_key() -> str:
    load_environment()
    configured = os.environ.get("JWT_SECRET") or os.environ.get("JWT_SECRET_KEY")
    if configured:
        if len(configured.encode("utf-8")) < 32:
            raise ValueError("JWT_SECRET_KEY 至少32字节")
        return configured
    path = db.PROJECT_ROOT / "work/jwt-secret.key"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="ascii") as output:
            output.write(secrets.token_urlsafe(64))
    except FileExistsError:
        pass
    value = path.read_text(encoding="ascii").strip()
    if len(value) < 32:
        raise ValueError("本地JWT密钥无效")
    return value


def hash_password(password: str) -> str:
    raw = password.encode("utf-8")
    if not 10 <= len(raw) <= 72:
        raise ValueError("密码UTF-8长度须为10至72字节")
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("ascii")


def create_user(session_factory, username: str, role: str, password: str):
    if role not in {"admin", "reviewer", "requester"} or not username.strip() or len(username) > 80:
        raise ValueError("用户名或角色无效")
    hashed = hash_password(password)
    with session_factory.begin() as session:
        if session.scalar(select(User.id).where(User.username == username)):
            raise ValueError("用户名已存在")
        user = User(id=uuid.uuid4().hex, username=username, role=role, password_hash=hashed, active=1, auth_version=1)
        session.add(user)
        return {"user_id": user.id, "username": username, "role": role}


def public_user(user):
    return {"user_id": user.id, "username": user.username, "role": user.role,
            "org_name": user.org_name}


def update_org(session_factory, user, org_name):
    with session_factory.begin() as session:
        row = session.get(User, user.id)
        if row is None:
            raise HTTPException(401, "未认证或登录已失效")
        row.org_name = org_name.strip() or None if org_name is not None else None
        return public_user(row)


def login(session_factory, username: str, password: str):
    with session_factory() as session:
        user = session.scalar(select(User).where(User.username == username))
        stored = user.password_hash.encode("ascii") if user and user.password_hash.startswith("$2") else _DUMMY_HASH
        raw = password.encode("utf-8")
        try:
            matched = bcrypt.checkpw(raw if len(raw) <= 72 else b"invalid-overlong-password", stored)
        except (ValueError, TypeError):
            matched = False
        if not user or user.active != 1 or len(raw) > 72 or not matched:
            raise HTTPException(status_code=401, detail="账号或密码错误", headers={"WWW-Authenticate": "Bearer"})
        now = datetime.now(timezone.utc)
        token = jwt.encode({"user_id": user.id, "auth_version": user.auth_version,
                            "iat": now, "exp": now + timedelta(seconds=EXPIRES_IN)}, signing_key(), algorithm="HS256")
        return {"access_token": token, "token_type": "bearer", "role": user.role, "expires_in": EXPIRES_IN}


def authenticate_header(header: Optional[str]) -> Principal:
    error = HTTPException(status_code=401, detail="未认证或登录已失效", headers={"WWW-Authenticate": "Bearer"})
    parts = (header or "").split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise error
    try:
        payload = jwt.decode(parts[1], signing_key(), algorithms=["HS256"], options={"require_exp": True})
        uid, version = payload.get("user_id"), payload.get("auth_version")
        if not isinstance(uid, str) or type(version) is not int or type(payload.get("exp")) is not int:
            raise error
        with db.SessionLocal() as session:
            user = session.get(User, uid)
            if not user or user.active != 1 or user.auth_version != version:
                raise error
            return Principal(user.id, user.username, user.role, user.auth_version, user.org_name)
    except (JWTError, ValueError, TypeError):
        raise error


def current_user(request: Request) -> Principal:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="未认证或登录已失效", headers={"WWW-Authenticate": "Bearer"})
    return user


def require_admin(request: Request) -> Principal:
    user = current_user(request)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user
