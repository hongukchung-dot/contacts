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


# ── 로그인 실패 잠금 ────────────────────────────────────────────────────────

def login_locked(session: Session, username: str, ip: str | None) -> bool:
    """짧은 시간에 실패가 몰리면 True. 무차별 대입을 막는다.

    같은 계정을 노리는 시도(IP를 바꿔 가며)와 같은 IP 에서 계정을 바꿔 가며
    두드리는 시도를 모두 잡기 위해 계정·IP 어느 쪽이든 한도를 넘으면 잠근다.
    """
    from datetime import timedelta

    from sqlalchemy import func, or_, select as sa_select

    settings = get_settings()
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=settings.login_fail_window_min)
    targets = [AuditLog.detail == f"id={username.strip()}"]
    if ip:
        targets.append(AuditLog.ip == ip)
    failures = session.scalar(
        sa_select(func.count())
        .select_from(AuditLog)
        .where(
            AuditLog.action == "login_failed",
            AuditLog.created_at >= cutoff,
            or_(*targets),
        )
    )
    return (failures or 0) >= settings.login_fail_limit


# ── 세션 쿠키 ───────────────────────────────────────────────────────────────

def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt="contacts-session")


# 비밀번호는 맞았지만 OTP 확인이 남은 상태를 잇는 단기 토큰 (5분)
_PENDING_MAX_AGE = 300


def _pending_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt="contacts-otp-pending")


def issue_pending(user: AppUser) -> str:
    return _pending_serializer().dumps({"uid": user.id})


def read_pending(token: str) -> int | None:
    try:
        data = _pending_serializer().loads(token, max_age=_PENDING_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    return data.get("uid")


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
