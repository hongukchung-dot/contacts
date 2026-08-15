"""TOTP 구현 검증. DB 불필요."""

from __future__ import annotations

import base64

from app.services import totp

# RFC 6238 부록 B 의 표준 시험 벡터 (SHA-1, 비밀키 "12345678901234567890")
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode().rstrip("=")


class TestRfcVectors:
    def test_known_codes(self):
        # (시각, 8자리 기준값) — 우리는 6자리이므로 뒤 6자리를 비교한다
        for at, expected8 in [
            (59, "94287082"),
            (1111111109, "07081804"),
            (1234567890, "89005924"),
            (2000000000, "69279037"),
        ]:
            assert totp.code_at(RFC_SECRET, at) == expected8[-6:], at


class TestVerify:
    def test_accepts_current_code(self):
        secret = totp.generate_secret()
        assert totp.verify(secret, totp.code_at(secret, 1_700_000_015), at=1_700_000_015)

    def test_accepts_adjacent_step_for_clock_skew(self):
        secret = totp.generate_secret()
        previous = totp.code_at(secret, 1_700_000_000 - 30)
        assert totp.verify(secret, previous, at=1_700_000_000)

    def test_rejects_wrong_and_stale_codes(self):
        secret = totp.generate_secret()
        assert not totp.verify(secret, "000000", at=1_700_000_000) or totp.code_at(
            secret, 1_700_000_000
        ) == "000000"
        stale = totp.code_at(secret, 1_700_000_000 - 300)
        assert not totp.verify(secret, stale, at=1_700_000_000)

    def test_rejects_malformed_input(self):
        secret = totp.generate_secret()
        for bad in ("", "12345", "1234567", "abcdef", "12 34"):
            assert not totp.verify(secret, bad)

    def test_accepts_code_with_spaces(self):
        secret = totp.generate_secret()
        code = totp.code_at(secret, 1_700_000_015)
        spaced = f"{code[:3]} {code[3:]}"
        assert totp.verify(secret, spaced, at=1_700_000_015)


class TestProvisioning:
    def test_uri_contains_secret_and_account(self):
        uri = totp.provisioning_uri("ABC234", "hong")
        assert uri.startswith("otpauth://totp/")
        assert "secret=ABC234" in uri
        assert "hong" in uri

    def test_generated_secret_is_valid_base32(self):
        secret = totp.generate_secret()
        base64.b32decode(secret + "=" * (-len(secret) % 8))
