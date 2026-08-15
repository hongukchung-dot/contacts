"""DB 스키마 생성 + 매체 사전 시드 + 관리자 계정 생성.

여러 번 실행해도 안전하다(있는 것은 건너뛴다).

    python tools/init_db.py
    python tools/init_db.py --admin-password '직접지정'
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import create_schema, session_scope  # noqa: E402
from app.ingest.normalize import normalize_key  # noqa: E402
from app.ingest.reference import get_reference  # noqa: E402
from app.models import AppUser, Outlet, OutletAlias  # noqa: E402
from app.services.auth import generate_password, hash_password  # noqa: E402


def seed_outlets() -> tuple[int, int]:
    reference = get_reference()
    created = updated = 0
    with session_scope() as session:
        for item in reference.outlets:
            outlet = session.scalar(select(Outlet).where(Outlet.name == item.name))
            if outlet is None:
                outlet = Outlet(
                    name=item.name,
                    name_key=normalize_key(item.name),
                    category=item.category,
                    sort_order=item.order,
                )
                session.add(outlet)
                session.flush()
                created += 1
            elif (outlet.category, outlet.sort_order) != (item.category, item.order):
                outlet.category = item.category
                outlet.sort_order = item.order
                updated += 1

        # 별칭은 사전 기준으로 다시 채운다.
        for alias_key, ref_outlet in reference._outlet_by_key.items():  # noqa: SLF001
            outlet = session.scalar(select(Outlet).where(Outlet.name == ref_outlet.name))
            if outlet is None:
                continue
            exists = session.scalar(select(OutletAlias).where(OutletAlias.alias_key == alias_key))
            if exists is None:
                session.add(
                    OutletAlias(outlet_id=outlet.id, alias=alias_key, alias_key=alias_key)
                )
    return created, updated


def ensure_admin(password: str | None) -> str | None:
    settings = get_settings()
    with session_scope() as session:
        existing = session.scalar(select(AppUser).where(AppUser.username == settings.admin_username))
        if existing:
            return None
        secret = password or settings.admin_password or generate_password()
        session.add(
            AppUser(
                username=settings.admin_username,
                display_name="관리자",
                password_hash=hash_password(secret),
                role="admin",
                must_change_password=not (password or settings.admin_password),
            )
        )
        return secret


def main() -> int:
    parser = argparse.ArgumentParser(description="DB 초기화")
    parser.add_argument("--admin-password", dest="admin_password", default=None)
    args = parser.parse_args()

    print("· 스키마 생성 …")
    create_schema()

    created, updated = seed_outlets()
    print(f"· 매체 사전 시드: 신규 {created}개, 갱신 {updated}개")

    secret = ensure_admin(args.admin_password)
    if secret:
        print("\n" + "=" * 60)
        print(f"  관리자 계정이 생성되었습니다")
        print(f"    아이디   : {get_settings().admin_username}")
        print(f"    비밀번호 : {secret}")
        print("  ※ 첫 로그인 후 반드시 비밀번호를 변경하세요.")
        print("=" * 60)
    else:
        print("· 관리자 계정은 이미 존재합니다 (변경 없음)")

    print("\n초기화 완료.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
