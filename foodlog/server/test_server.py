"""서버 단위 테스트 — Anthropic 호출은 목으로 대체한다.

실행: python -m pytest -q foodlog/server
"""
import base64
import json
import os
import sys
import tempfile
from unittest.mock import AsyncMock, MagicMock

_TMP = tempfile.mkdtemp(prefix="foodlog-test-")
os.environ["APP_TOKENS"] = "tok-admin,tok-friend"
os.environ["FOODLOG_DB"] = os.path.join(_TMP, "cache.db")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-dummy")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient  # noqa: E402
import server  # noqa: E402


def make_client():
    return TestClient(server.app)


JPEG_B64 = base64.b64encode(b"\xff\xd8\xff\xe0" + b"0" * 100).decode()


def test_health():
    with make_client() as c:
        r = c.get("/v1/health")
        assert r.status_code == 200 and r.json()["ok"] is True


def test_auth_required():
    with make_client() as c:
        r = c.post("/v1/analyze", json={"images": [{"data": JPEG_B64}]})
        assert r.status_code == 401
        r = c.get("/v1/barcode/8801043015337", headers={"X-App-Token": "bad"})
        assert r.status_code == 401


def test_analyze_validation():
    with make_client() as c:
        r = c.post("/v1/analyze", json={"images": []}, headers={"X-App-Token": "tok-admin"})
        assert r.status_code == 400


def test_barcode_invalid():
    with make_client() as c:
        r = c.get("/v1/barcode/abc", headers={"X-App-Token": "tok-admin"})
        assert r.status_code == 400


def test_analyze_mocked():
    result_json = {
        "items": [{"name": "김치찌개", "portion_desc": "1인분", "kcal": 250,
                   "carbs_g": None, "protein_g": None, "fat_g": None,
                   "likely_shared": False, "confidence": 0.8, "photo_indices": [0]}],
        "meal_type_guess": "LUNCH", "notes": "테스트",
    }
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "```json\n" + json.dumps(result_json, ensure_ascii=False) + "\n```"
    resp = MagicMock()
    resp.content = [text_block]
    resp.usage.input_tokens = 100
    resp.usage.output_tokens = 50

    with make_client() as c:
        server.app.state.anthropic = MagicMock()
        server.app.state.anthropic.messages.create = AsyncMock(return_value=resp)
        server.app.state.anthropic.close = AsyncMock()
        r = c.post(
            "/v1/analyze",
            json={"images": [{"data": JPEG_B64, "taken_at": "2026-09-06T12:31:00+09:00"}],
                  "hint": "회사 근처", "mode": "meal"},
            headers={"X-App-Token": "tok-admin"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["items"][0]["name"] == "김치찌개"
        assert body["meal_type_guess"] == "LUNCH"
        # 시스템 프롬프트에 prompt caching 적용 여부
        kwargs = server.app.state.anthropic.messages.create.call_args.kwargs
        assert kwargs["model"] == server.MODEL
        assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_rate_limit():
    original = server.DAILY_LIMIT
    server.DAILY_LIMIT = 0
    try:
        with make_client() as c:
            r = c.post("/v1/analyze", json={"images": [{"data": JPEG_B64}]},
                       headers={"X-App-Token": "tok-friend"})
            assert r.status_code == 429
    finally:
        server.DAILY_LIMIT = original


def test_barcode_cache_roundtrip():
    server.init_db()
    server.cache_put_product({
        "barcode": "8801234567890", "name": "테스트과자", "brand": "브랜드",
        "serving_desc": "1봉지(90g)", "kcal_per_serving": 450.0,
        "carbs_g": 60.0, "protein_g": 5.0, "fat_g": 20.0, "source": "MANUAL",
    })
    with make_client() as c:
        r = c.get("/v1/barcode/8801234567890", headers={"X-App-Token": "tok-admin"})
        assert r.status_code == 200
        body = r.json()
        assert body["found"] is True and body["product"]["name"] == "테스트과자"
        assert body["source"] == "MANUAL"
