from fastapi import APIRouter, Depends
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict
from app import db
from app.services.auth import current_user, login, public_user, update_org

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80, strict=True)
    password: str = Field(min_length=1, max_length=1024, strict=True)


@router.post("/login")
def authenticate(body: LoginRequest):
    return login(db.SessionLocal, body.username, body.password)


@router.get("/me")
def me(user=Depends(current_user)):
    return public_user(user)


class OrganizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    org_name: Optional[str] = Field(..., max_length=200, strict=True)


@router.patch("/me")
def set_organization(body: OrganizationRequest, user=Depends(current_user)):
    return update_org(db.SessionLocal, user, body.org_name)
