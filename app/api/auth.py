from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from app import db
from app.services.auth import current_user, login, public_user

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
