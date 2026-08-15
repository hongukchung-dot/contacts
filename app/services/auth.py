"""사용자 인증. 계정별 로그인 + 세션 쿠키."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import AppUser, AuditLog

_hasher = PasswordHasher()

ROLES = {
    "viewer": "조회",
    "editor": "조회+업로드",
    "admin": "관리자",
}


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        _hasher.verify(password_hash, password)
        return True
    except (VerifyMismatchError, VerificationError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def generate_password(length: int = 14) -> str:
    alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def authenticate(session: Session, username: str, password: str) -> AppUser | None:
    user = session.scalar(select(AppUser).where(AppUser.username == username.strip()))
    if user is None or not user.active:
        # 계정이 없을 때도 해시 검증과 비슷한 시간을 쓰도록 한 번 돌려 준다.
        _hasher.hash(password)
        return None
    if not verify_password(user.password_hash, password):
        return None
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.last_login_at = datetime.now(timezone.utc)
    return user


# ── 세션 쿠키 ───────────────────────────────────────────────────────────────

def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt="contacts-session")


def issue_session(user: AppUser) -> str:
    return _serializer().dumps({"uid": user.id, "u": user.username})


def read_session(token: str) -> dict | None:
    try:
        return _serializer().loads(token, max_age=get_settings().session_max_age)
    except (BadSignature, SignatureExpired):
        return None


# ── 감사 로그 ───────────────────────────────────────────────────────────────

def log_action(
    session: Session,
    user: AppUser | None,
    action: str,
    detail: str | None = None,
    ip: str | None = None,
) -> None:
    session.add(
        AuditLog(
            user_id=user.id if user else None,
            username=user.username if user else "anonymous",
            action=action,
            detail=(detail or "")[:500] or None,
            ip=ip,
        )
    )
