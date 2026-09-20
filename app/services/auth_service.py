"""
User Authentication Service.

Adds user registration / login / session identity to the existing
Agentic Commerce Platform without disturbing existing protocol services
(A2A, MCP, AP2, Razorpay, audit ledger).

- Passwords are hashed with bcrypt (never stored/logged in plaintext).
- Sessions are stateless JWTs (HS256) signed with settings.JWT_SECRET.
- Users are persisted in the shared DynamoDB table used by the audit ledger.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field

from app.config import settings

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ─── Models ──────────────────────────────────────────────────────────────

class UserRegister(BaseModel):
    email: str = Field(..., description="User email, used as login identifier")
    password: str = Field(..., min_length=8, max_length=128)
    name: Optional[str] = None


class UserLogin(BaseModel):
    email: str
    password: str


class UserPublic(BaseModel):
    user_id: str
    email: str
    name: Optional[str] = None
    created_at: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int
    user: UserPublic


# ─── Storage ─────────────────────────────────────────────────────────────

class UserStore:
    """DynamoDB-backed user store. Never persists plaintext passwords."""

    def __init__(self, table_name: str | None = None):
        import boto3
        self.table_name = table_name or settings.DYNAMODB_TABLE_NAME
        if not self.table_name:
            raise RuntimeError("DYNAMODB_TABLE_NAME must be configured.")
        self._table = boto3.resource(
            "dynamodb", region_name=settings.AWS_REGION or None
        ).Table(self.table_name)

    def create_user(self, email: str, password_hash: str, name: Optional[str]) -> dict:
        user_id = f"user_{uuid.uuid4().hex[:12]}"
        created_at = datetime.now(timezone.utc).isoformat()
        try:
            self._table.put_item(
                Item={
                    "pk": f"USER#{user_id}",
                    "sk": "PROFILE",
                    "entity_type": "USER",
                    "user_id": user_id,
                    "email": email.lower(),
                    "name": name or "",
                    "password_hash": password_hash,
                    "created_at": created_at,
                },
                ConditionExpression="attribute_not_exists(pk)",
            )
        except Exception as exc:
            if exc.__class__.__name__ == "ConditionalCheckFailedException":
                raise ValueError("A user with this email already exists.") from exc
            raise
        return {
            "user_id": user_id,
            "email": email.lower(),
            "name": name,
            "created_at": created_at,
        }

    def get_by_email(self, email: str) -> Optional[dict]:
        response = self._table.scan(
            FilterExpression="entity_type = :entity AND email = :email",
            ExpressionAttributeValues={":entity": "USER", ":email": email.lower()},
        )
        return response.get("Items", [None])[0]

    def get_by_user_id(self, user_id: str) -> Optional[dict]:
        response = self._table.get_item(
            Key={"pk": f"USER#{user_id}", "sk": "PROFILE"}
        )
        return response.get("Item")


# ─── Password hashing ────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ─── JWT session tokens ──────────────────────────────────────────────────

def create_access_token(user_id: str, email: str) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": user_id,
        "email": email,
        "iat": int(now.timestamp()),
        "exp": expire,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired. Please log in again.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token.")


# ─── FastAPI dependencies ────────────────────────────────────────────────

_bearer_scheme = HTTPBearer(auto_error=False)
user_store = UserStore()


class AuthenticatedUser(BaseModel):
    user_id: str
    email: str


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> AuthenticatedUser:
    """Dependency: resolves the authenticated user from a Bearer JWT.

    The user identity is ALWAYS derived from the verified token, never from
    a client-supplied user_id field in the request body.
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    payload = decode_access_token(credentials.credentials)
    user_id = payload.get("sub")
    row = user_store.get_by_user_id(user_id) if user_id else None
    if not row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found for this session.")
    return AuthenticatedUser(user_id=row["user_id"], email=row["email"])


def register_user(payload: UserRegister) -> TokenResponse:
    if not _EMAIL_RE.match(payload.email):
        raise HTTPException(status_code=422, detail="Invalid email address.")
    if user_store.get_by_email(payload.email):
        raise HTTPException(status_code=409, detail="A user with this email already exists.")
    password_hash = hash_password(payload.password)
    try:
        user = user_store.create_user(payload.email, password_hash, payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    token = create_access_token(user["user_id"], user["email"])
    return TokenResponse(
        access_token=token,
        expires_in_minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES,
        user=UserPublic(**user),
    )


def login_user(payload: UserLogin) -> TokenResponse:
    row = user_store.get_by_email(payload.email)
    # Constant-shape error regardless of whether the email exists, to avoid
    # leaking account existence.
    if not row or not verify_password(payload.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    token = create_access_token(row["user_id"], row["email"])
    return TokenResponse(
        access_token=token,
        expires_in_minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES,
        user=UserPublic(
            user_id=row["user_id"], email=row["email"], name=row["name"], created_at=row["created_at"]
        ),
    )
