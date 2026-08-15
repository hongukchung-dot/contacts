"""웹 화면 통합 테스트 — 로그인, 매트릭스, 팝업, 검색, 업로드→검토→반영."""

from __future__ import annotations

import re

import pytest

from tests.conftest import FIXTURES, requires_db

pytestmark = requires_db


@pytest.fixture()
def client(db_session):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app, follow_redirects=False) as test_client:
        yield test_client


@pytest.fixture()
def admin(db_session):
    from app.models import AppUser
    from app.services.auth import hash_password

    user = AppUser(
        username="tester",
        display_name="테스터",
        password_hash=hash_password("verysecret123"),
        role="admin",
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture()
def auth(client, admin):
    response = client.post(
        "/login", data={"username": "tester", "password": "verysecret123", "next": "/"}
    )
    assert response.status_code == 303
    return client


def upload(auth_client, name: str):
    with (FIXTURES / name).open("rb") as handle:
        return auth_client.post("/upload", files={"file": (name, handle)})


class TestAuthGate:
    def test_root_redirects_to_login(self, client):
        response = client.get("/")
        assert response.status_code == 303
        assert "/login" in response.headers["location"]

    def test_wrong_password_is_rejected(self, client, admin):
        response = client.post(
            "/login", data={"username": "tester", "password": "nope", "next": "/"}
        )
        assert response.status_code == 401
        assert "올바르지 않습니다" in response.text

    def test_login_sets_cookie(self, auth):
        assert auth.cookies.get("contacts_session")

    def test_robots_blocks_indexing(self, client):
        assert "Disallow: /" in client.get("/robots.txt").text

    def test_security_headers(self, client):
        response = client.get("/login")
        assert response.headers["X-Robots-Tag"].startswith("noindex")
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Cache-Control"] == "no-store"


class TestEmptyState:
    def test_index_without_data(self, auth):
        response = auth.get("/")
        assert response.status_code == 200
        assert "아직 등록된 자료가 없습니다" in response.text


class TestFullCycle:
    @pytest.fixture()
    def loaded(self, auth):
        response = upload(auth, "sample_desk_matrix.xlsx")
        assert response.status_code == 303, response.text
        review_url = response.headers["location"]

        page = auth.get(review_url)
        assert page.status_code == 200
        assert "신규 등록" in page.text

        change_set_id = review_url.rsplit("/", 1)[-1]
        ids = re.findall(r'name="approve" value="(\d+)"', page.text)
        applied = auth.post(f"/review/{change_set_id}/apply", data={"approve": ids})
        assert applied.status_code == 303
        return auth

    def test_upload_does_not_apply_immediately(self, auth):
        upload(auth, "sample_desk_matrix.xlsx")
        assert "아직 등록된 자료가 없습니다" in auth.get("/").text

    def test_matrix_renders_after_apply(self, loaded):
        page = loaded.get("/")
        assert page.status_code == 200
        for expected in ("조선일보", "이길성", "산업부장", "편집국장"):
            assert expected in page.text

    def test_phone_is_masked_by_default(self, loaded):
        assert "●●●●" in loaded.get("/").text

    def test_category_filter(self, loaded):
        page = loaded.get("/?category=경제지")
        body = page.text.split("<tbody>", 1)[1]
        assert "매일경제" in body
        assert "조선일보" not in body

    def test_outlet_modal_fragment(self, loaded):
        page = loaded.get("/")
        outlet_id = re.search(r'data-outlet="(\d+)"', page.text).group(1)
        modal = loaded.get(f"/outlet/{outlet_id}")
        assert modal.status_code == 200
        assert "명단 전체 복사" in modal.text

    def test_search_answers_natural_question(self, loaded):
        page = loaded.get("/search", params={"q": "매경 산업부장이 누구지?"})
        assert page.status_code == 200
        assert "매일경제 · 산업부장" in page.text
        assert "정욱" in page.text

    def test_duplicate_upload_is_blocked(self, loaded):
        response = upload(loaded, "sample_desk_matrix.xlsx")
        assert response.status_code == 400
        assert "이미 등록되어" in response.text

    def test_export_returns_xlsx(self, loaded):
        response = loaded.get("/export.xlsx")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument.spreadsheetml"
        )
        assert response.content[:2] == b"PK"

    def test_history_lists_applied_file(self, loaded):
        page = loaded.get("/history")
        assert "sample_desk_matrix.xlsx" in page.text
        assert "반영됨" in page.text

    def test_second_file_shows_review_screen(self, loaded):
        response = upload(loaded, "sample_combined.docx")
        assert response.status_code == 303
        page = loaded.get(response.headers["location"])
        assert "변경 내역 검토" in page.text

    def test_audit_log_records_activity(self, loaded):
        page = loaded.get("/admin/users")
        assert "apply_changes" in page.text
        assert "login" in page.text


class TestPermissions:
    @pytest.fixture()
    def viewer(self, client, db_session):
        from app.models import AppUser
        from app.services.auth import hash_password

        db_session.add(
            AppUser(
                username="viewer1",
                display_name="조회자",
                password_hash=hash_password("verysecret123"),
                role="viewer",
            )
        )
        db_session.commit()
        client.post(
            "/login", data={"username": "viewer1", "password": "verysecret123", "next": "/"}
        )
        return client

    def test_viewer_cannot_upload(self, viewer):
        response = viewer.get("/upload")
        assert response.status_code == 403
        assert "업로드 권한이 없습니다" in response.text

    def test_viewer_cannot_manage_users(self, viewer):
        assert viewer.get("/admin/users").status_code == 403

    def test_viewer_can_view_and_search(self, viewer):
        assert viewer.get("/").status_code == 200
        assert viewer.get("/search", params={"q": "조선"}).status_code == 200


class TestUploadValidation:
    def test_rejects_wrong_extension(self, auth, tmp_path):
        bad = tmp_path / "주소록.txt"
        bad.write_text("매체,이름", encoding="utf-8")
        with bad.open("rb") as handle:
            response = auth.post("/upload", files={"file": ("주소록.txt", handle)})
        assert response.status_code == 400
        assert "xlsx 또는 docx" in response.text


class TestHighlighting:
    def test_initial_load_is_not_highlighted(self, auth):
        """처음 적재는 전부 신규이므로 변동 음영을 넣지 않는다."""
        response = upload(auth, "sample_desk_matrix.xlsx")
        review_url = response.headers["location"]
        page = auth.get(review_url)
        ids = re.findall(r'name="approve" value="(\d+)"', page.text)
        auth.post(f"{review_url}/apply", data={"approve": ids})

        body = auth.get("/").text.split("<tbody>", 1)[1]
        assert "person recent" not in body

    def test_later_change_is_highlighted(self, auth):
        for name in ("sample_desk_matrix.xlsx", "sample_combined.docx"):
            response = upload(auth, name)
            review_url = response.headers["location"]
            page = auth.get(review_url)
            ids = re.findall(r'name="approve" value="(\d+)"', page.text)
            auth.post(f"{review_url}/apply", data={"approve": ids})

        body = auth.get("/").text.split("<tbody>", 1)[1]
        assert "person recent" in body
