"""TOTP 2단계 인증 (RFC 6238, 6자리·30초). 표준 라이브러리만 쓴다.

구글 OTP·마이크로소프트 Authenticator 등 표준 앱과 호환된다.
`REQUIRE_TOTP=true` 일 때만 로그인 흐름에 끼어든다 (config 참고).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
import urllib.parse

STEP_SECONDS = 30
DIGITS = 6


def generate_secret() -> str:
    """OTP 앱에 등록할 비밀키 (base32, 패딩 없음)."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _hotp(secret: str, counter: int) -> str:
    padded = secret + "=" * (-len(secret) % 8)
    key = base64.b32decode(padded, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 10 ** DIGITS:0{DIGITS}d}"


def code_at(secret: str, at: float | None = None) -> str:
    moment = time.time() if at is None else at
    return _hotp(secret, int(moment // STEP_SECONDS))


def verify(secret: str, code: str, *, at: float | None = None, window: int = 1) -> bool:
    """코드를 검증한다. 시계가 조금 어긋난 앱을 위해 앞뒤 한 칸(±30초)까지 본다."""
    cleaned = (code or "").strip().replace(" ", "")
    if not cleaned.isdigit() or len(cleaned) != DIGITS:
        return False
    moment = time.time() if at is None else at
    counter = int(moment // STEP_SECONDS)
    return any(
        hmac.compare_digest(_hotp(secret, counter + offset), cleaned)
        for offset in range(-window, window + 1)
    )


def provisioning_uri(secret: str, account: str, issuer: str = "언론사 주소록") -> str:
    """OTP 앱이 읽는 otpauth:// 주소. QR로 보여 주거나 직접 입력하게 한다."""
    label = urllib.parse.quote(f"{issuer}:{account}")
    query = urllib.parse.urlencode({"secret": secret, "issuer": issuer})
    return f"otpauth://totp/{label}?{query}"
