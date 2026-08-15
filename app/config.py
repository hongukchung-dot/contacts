"""환경설정. 값은 모두 환경변수로 주입한다 (.env.example 참고)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    def __init__(self) -> None:
        self.database_url: str = os.getenv(
            "DATABASE_URL", "postgresql+psycopg://contacts:contacts@localhost:5432/contacts"
        )
        self.secret_key: str = os.getenv("SECRET_KEY", "")
        self.session_cookie: str = os.getenv("SESSION_COOKIE", "contacts_session")
        self.session_max_age: int = int(os.getenv("SESSION_MAX_AGE", "43200"))  # 12시간

        self.upload_dir: Path = Path(os.getenv("UPLOAD_DIR", BASE_DIR / "data" / "uploads"))
        self.max_upload_mb: int = int(os.getenv("MAX_UPLOAD_MB", "50"))

        # 전화번호를 기본으로 가릴지 여부. 켜 두면 목록에서는 가려지고 클릭 시 보인다.
        self.mask_phones_by_default: bool = _bool("MASK_PHONES_BY_DEFAULT", True)
        # 최근 며칠 이내 변경을 화면에 음영 표시할지
        self.highlight_days: int = int(os.getenv("HIGHLIGHT_DAYS", "45"))

        self.admin_username: str = os.getenv("ADMIN_USERNAME", "admin")
        self.admin_password: str = os.getenv("ADMIN_PASSWORD", "")

    def validate(self) -> None:
        if not self.secret_key or len(self.secret_key) < 32:
            raise RuntimeError(
                "SECRET_KEY가 없거나 너무 짧습니다(32자 이상). "
                "`openssl rand -hex 32` 로 만들어 .env에 넣어 주세요."
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
