"""FastAPI 애플리케이션 — 화면과 라우트."""

from __future__ import annotations

import logging
import re
import shutil
import tempfile
from datetime import date, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import BASE_DIR, get_settings
from .db import get_db
from .ingest.normalize import format_phone
from .models import (
    APPLIED,
    APPROVED,
    CONFLICT,
    NEW,
    PENDING,
    REJECTED,
    REMOVE,
    UPDATE,
    AppUser,
    AuditLog,
    Change,
    ChangeSet,
    Outlet,
)
from .services import directory, search as search_service
from .services.auth import (
    ROLES,
    authenticate,
    generate_password,
    hash_password,
    issue_session,
    log_action,
    read_session,
    verify_password,
)
from .services.ingest import DuplicateFileError, apply_change_set, reject_all_pending, stage_file

logger = logging.getLogger("contacts")

app = FastAPI(title="언론사 주소록", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")

_PHONE_IN_TEXT = re.compile(r"01[016-9]-\d{3,4}-\d{4}")

templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))
templates.env.globals["format_phone"] = format_phone


def mask_phone(digits: str | None) -> str:
    """가운데 자리를 가린 표기. 어깨너머·화면 캡처 노출을 줄이기 위한 것이다."""
    formatted = format_phone(digits)
    if not formatted:
        return "—"
    head, mid, tail = formatted.split("-")
    return f"{head}-{'●' * len(mid)}-{tail}"


def phone_spans(text: str | None, mask: bool = True) -> Markup:
    """문장 속 전화번호를 클릭해서 보는 스팬으로 바꾼다. (변경 이력 등 목록 화면용)"""
    if not text:
        return Markup("")

    def replace(match: re.Match[str]) -> str:
        raw = match.group(0)
        shown = mask_phone(re.sub(r"\D", "", raw)) if mask else raw
        return f'<span class="phone" data-phone="{escape(raw)}">{escape(shown)}</span>'

    return Markup(_PHONE_IN_TEXT.sub(replace, escape(text)))


templates.env.globals["mask_phone"] = mask_phone
templates.env.globals["phone_spans"] = phone_spans
templates.env.globals["ROLES"] = ROLES

CHANGE_TYPE_LABEL = {
    NEW: "신규",
    UPDATE: "변경",
    REMOVE: "삭제",
    CONFLICT: "확인필요",
}
templates.env.globals["CHANGE_TYPE_LABEL"] = CHANGE_TYPE_LABEL


# ── 인증 ────────────────────────────────────────────────────────────────────

def current_user(request: Request, db: Session = Depends(get_db)) -> AppUser | None:
    token = request.cookies.get(get_settings().session_cookie)
    if not token:
        return None
    data = read_session(token)
    if not data:
        return None
    user = db.get(AppUser, data.get("uid"))
    return user if user and user.active else None


def require_user(user: AppUser | None = Depends(current_user)) -> AppUser:
    if user is None:
        raise HTTPException(status_code=401)
    return user


def require_editor(user: AppUser = Depends(require_user)) -> AppUser:
    if not user.can_edit:
        raise HTTPException(status_code=403, detail="업로드 권한이 없습니다.")
    return user


def require_admin(user: AppUser = Depends(require_user)) -> AppUser:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="관리자만 접근할 수 있습니다.")
    return user


@app.exception_handler(401)
async def unauthorized(request: Request, exc: HTTPException) -> Response:
    return RedirectResponse(f"/login?next={request.url.path}", status_code=303)


@app.exception_handler(403)
async def forbidden(request: Request, exc: HTTPException) -> Response:
    return templates.TemplateResponse(
        request, "error.html", {"message": exc.detail or "권한이 없습니다."}, status_code=403
    )


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    # 개인정보가 담긴 화면이므로 색인·캐시·프레임 삽입을 모두 막는다.
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots() -> str:
    return "User-agent: *\nDisallow: /\n"


@app.get("/healthz", response_class=PlainTextResponse)
async def healthz() -> str:
    return "ok"


# ── 로그인 ──────────────────────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, next: str = "/") -> Response:
    return templates.TemplateResponse(request, "login.html", {"next": next, "user": None})


@app.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
    db: Session = Depends(get_db),
) -> Response:
    user = authenticate(db, username, password)
    if user is None:
        log_action(db, None, "login_failed", f"id={username}", request.client.host if request.client else None)
        db.commit()
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "아이디 또는 비밀번호가 올바르지 않습니다.", "next": next, "user": None},
            status_code=401,
        )
    log_action(db, user, "login", None, request.client.host if request.client else None)
    db.commit()

    target = next if next.startswith("/") else "/"
    response = RedirectResponse(target, status_code=303)
    settings = get_settings()
    response.set_cookie(
        settings.session_cookie,
        issue_session(user),
        max_age=settings.session_max_age,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return response


@app.get("/logout")
async def logout(request: Request) -> Response:
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(get_settings().session_cookie)
    return response


# ── 메인: 주요 간부 현황 매트릭스 ───────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    category: str | None = None,
    user: AppUser = Depends(require_user),
    db: Session = Depends(get_db),
) -> Response:
    slots, groups = directory.matrix(db, category=category)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "user": user,
            "slots": slots,
            "groups": groups,
            "stats": directory.stats(db),
            "categories": directory.CATEGORY_ORDER,
            "selected_category": category,
            "mask": get_settings().mask_phones_by_default,
            "highlight_days": get_settings().highlight_days,
        },
    )


@app.get("/search", response_class=HTMLResponse)
async def search_page(
    request: Request,
    q: str = "",
    user: AppUser = Depends(require_user),
    db: Session = Depends(get_db),
) -> Response:
    result = search_service.search(db, q)
    if q.strip():
        log_action(db, user, "search", q[:200], request.client.host if request.client else None)
        db.commit()
    return templates.TemplateResponse(
        request,
        "search.html",
        {"user": user, "result": result, "q": q, "mask": get_settings().mask_phones_by_default},
    )


@app.get("/outlet/{outlet_id}", response_class=HTMLResponse)
async def outlet_modal(
    request: Request,
    outlet_id: int,
    user: AppUser = Depends(require_user),
    db: Session = Depends(get_db),
) -> Response:
    detail = directory.outlet_detail(db, outlet_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="매체를 찾을 수 없습니다.")
    log_action(db, user, "view_outlet", detail["outlet"].name, request.client.host if request.client else None)
    db.commit()
    return templates.TemplateResponse(
        request,
        "_outlet_detail.html",
        {"user": user, **detail, "mask": get_settings().mask_phones_by_default},
    )


@app.get("/person/{person_id}", response_class=HTMLResponse)
async def person_page(
    request: Request,
    person_id: int,
    user: AppUser = Depends(require_user),
    db: Session = Depends(get_db),
) -> Response:
    detail = directory.person_detail(db, person_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="인물을 찾을 수 없습니다.")
    return templates.TemplateResponse(request, "person.html", {"user": user, **detail})


@app.get("/history", response_class=HTMLResponse)
async def history_page(
    request: Request,
    user: AppUser = Depends(require_user),
    db: Session = Depends(get_db),
) -> Response:
    files = db.scalars(
        select(ChangeSet).order_by(ChangeSet.id.desc()).limit(50)
    ).all()
    return templates.TemplateResponse(
        request,
        "history.html",
        {"user": user, "changes": directory.recent_changes(db), "change_sets": files},
    )


# ── 업로드 → 검토 → 반영 ───────────────────────────────────────────────────

@app.get("/upload", response_class=HTMLResponse)
async def upload_form(
    request: Request,
    user: AppUser = Depends(require_editor),
    db: Session = Depends(get_db),
) -> Response:
    pending = db.scalars(
        select(ChangeSet).where(ChangeSet.status == PENDING).order_by(ChangeSet.id.desc())
    ).all()
    return templates.TemplateResponse(
        request, "upload.html", {"user": user, "pending": pending, "error": None}
    )


@app.post("/upload")
async def upload(
    request: Request,
    file: UploadFile,
    as_of: str = Form(""),
    user: AppUser = Depends(require_editor),
    db: Session = Depends(get_db),
) -> Response:
    settings = get_settings()
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".xlsx", ".xlsm", ".docx"}:
        return await _upload_error(request, db, user, "xlsx 또는 docx 파일만 올릴 수 있습니다.")

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
        shutil.copyfileobj(file.file, handle, length=1 << 20)
        temp_path = Path(handle.name)

    try:
        size_mb = temp_path.stat().st_size / (1 << 20)
        if size_mb > settings.max_upload_mb:
            return await _upload_error(
                request, db, user, f"파일이 너무 큽니다 ({size_mb:.0f}MB > {settings.max_upload_mb}MB)."
            )

        override = None
        if as_of.strip():
            try:
                override = date.fromisoformat(as_of.strip())
            except ValueError:
                return await _upload_error(request, db, user, "기준일 형식이 올바르지 않습니다 (YYYY-MM-DD).")

        change_set = stage_file(
            db,
            temp_path,
            filename=file.filename or temp_path.name,
            uploaded_by_id=user.id,
            as_of_override=override,
        )
        log_action(db, user, "upload", file.filename, request.client.host if request.client else None)
        db.commit()
        return RedirectResponse(f"/review/{change_set.id}", status_code=303)
    except DuplicateFileError as exc:
        db.rollback()
        return await _upload_error(request, db, user, str(exc))
    except ValueError as exc:
        db.rollback()
        return await _upload_error(request, db, user, str(exc))
    except Exception as exc:  # noqa: BLE001 - 원인을 사용자에게 보여 주고 로그에 남긴다
        db.rollback()
        logger.exception("업로드 처리 실패: %s", file.filename)
        return await _upload_error(
            request,
            db,
            user,
            f"파일을 처리하는 중 오류가 발생했습니다.\n\n"
            f"{type(exc).__name__}: {exc}\n\n"
            f"서버에서 'docker compose logs --tail=80 app' 으로 자세한 내용을 볼 수 있습니다.",
        )
    finally:
        temp_path.unlink(missing_ok=True)


async def _upload_error(request: Request, db: Session, user: AppUser, message: str) -> Response:
    pending = db.scalars(
        select(ChangeSet).where(ChangeSet.status == PENDING).order_by(ChangeSet.id.desc())
    ).all()
    return templates.TemplateResponse(
        request, "upload.html", {"user": user, "pending": pending, "error": message}, status_code=400
    )


@app.get("/review/{change_set_id}", response_class=HTMLResponse)
async def review_page(
    request: Request,
    change_set_id: int,
    user: AppUser = Depends(require_editor),
    db: Session = Depends(get_db),
) -> Response:
    change_set = db.get(ChangeSet, change_set_id)
    if change_set is None:
        raise HTTPException(status_code=404, detail="검토 대상을 찾을 수 없습니다.")

    buckets: dict[str, list[Change]] = {NEW: [], UPDATE: [], CONFLICT: [], REMOVE: []}
    for change in sorted(change_set.changes, key=lambda c: (c.outlet_name, c.person_name)):
        buckets.setdefault(change.change_type, []).append(change)

    return templates.TemplateResponse(
        request,
        "review.html",
        {
            "user": user,
            "change_set": change_set,
            "source": change_set.source_file,
            "buckets": buckets,
            "auto_count": sum(1 for c in change_set.changes if c.auto_apply),
            "pending_count": sum(1 for c in change_set.changes if c.decision == PENDING),
        },
    )


@app.post("/review/{change_set_id}/apply")
async def review_apply(
    request: Request,
    change_set_id: int,
    user: AppUser = Depends(require_editor),
    db: Session = Depends(get_db),
) -> Response:
    change_set = db.get(ChangeSet, change_set_id)
    if change_set is None or change_set.status == APPLIED:
        raise HTTPException(status_code=404, detail="이미 처리되었거나 없는 검토 건입니다.")

    form = await request.form()
    approved = {int(value) for value in form.getlist("approve")}
    for change in change_set.changes:
        change.decision = APPROVED if change.id in approved else REJECTED

    counts = apply_change_set(db, change_set, applied_by_id=user.id)
    log_action(
        db,
        user,
        "apply_changes",
        f"file={change_set.source_file.filename} counts={counts}",
        request.client.host if request.client else None,
    )
    db.commit()
    return RedirectResponse(f"/review/{change_set_id}?done=1", status_code=303)


@app.post("/review/{change_set_id}/discard")
async def review_discard(
    request: Request,
    change_set_id: int,
    user: AppUser = Depends(require_editor),
    db: Session = Depends(get_db),
) -> Response:
    change_set = db.get(ChangeSet, change_set_id)
    if change_set is None:
        raise HTTPException(status_code=404, detail="없는 검토 건입니다.")
    reject_all_pending(change_set)
    change_set.status = REJECTED
    log_action(db, user, "discard_changes", change_set.source_file.filename, None)
    db.commit()
    return RedirectResponse("/upload", status_code=303)


# ── 엑셀 내보내기 ───────────────────────────────────────────────────────────

@app.get("/export.xlsx")
async def export_xlsx(
    request: Request,
    user: AppUser = Depends(require_user),
    db: Session = Depends(get_db),
) -> Response:
    import io

    from openpyxl import Workbook

    slots, groups = directory.matrix(db)
    workbook = Workbook()

    sheet = workbook.active
    sheet.title = "주요 데스크 현황"
    sheet.append(["분류", "매체명", *slots])
    for category, rows in groups:
        for row in rows:
            line = [category, row.name]
            for slot in slots:
                entries = row.cells.get(slot) or []
                line.append(
                    "\n".join(
                        f"{e.name} {('(' + e.role_label + ')') if e.role_label else ''} {format_phone(e.phone)}".strip()
                        for e in entries
                    )
                )
            sheet.append(line)

    sheet = workbook.create_sheet("출입기자")
    sheet.append(["분류", "매체", "부서", "이름", "직책", "전화번호"])
    for outlet in db.scalars(select(Outlet).order_by(Outlet.sort_order)).all():
        detail = directory.outlet_detail(db, outlet.id)
        if not detail:
            continue
        for dept, entries in detail["reporters_by_dept"].items():
            for entry in entries:
                sheet.append(
                    [outlet.category, outlet.name, dept, entry.name, entry.role_label or "", format_phone(entry.phone)]
                )

    buffer = io.BytesIO()
    workbook.save(buffer)
    log_action(db, user, "export", "xlsx", request.client.host if request.client else None)
    db.commit()

    stamp = datetime.now().strftime("%y%m%d")
    return Response(
        buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="contacts_{stamp}.xlsx"'},
    )


# ── 계정 ────────────────────────────────────────────────────────────────────

@app.get("/account", response_class=HTMLResponse)
async def account_page(request: Request, user: AppUser = Depends(require_user)) -> Response:
    return templates.TemplateResponse(request, "account.html", {"user": user, "message": None, "error": None})


@app.post("/account", response_class=HTMLResponse)
async def account_update(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    user: AppUser = Depends(require_user),
    db: Session = Depends(get_db),
) -> Response:
    context: dict = {"user": user, "message": None, "error": None}
    if not verify_password(user.password_hash, current_password):
        context["error"] = "현재 비밀번호가 올바르지 않습니다."
    elif len(new_password) < 10:
        context["error"] = "새 비밀번호는 10자 이상이어야 합니다."
    elif new_password != confirm_password:
        context["error"] = "새 비밀번호가 서로 다릅니다."
    else:
        managed = db.get(AppUser, user.id)
        managed.password_hash = hash_password(new_password)
        managed.must_change_password = False
        log_action(db, user, "change_password", None, None)
        db.commit()
        context["message"] = "비밀번호가 변경되었습니다."
    return templates.TemplateResponse(request, "account.html", context)


@app.get("/admin/users", response_class=HTMLResponse)
async def admin_users(
    request: Request,
    user: AppUser = Depends(require_admin),
    db: Session = Depends(get_db),
    created: str | None = None,
) -> Response:
    users = db.scalars(select(AppUser).order_by(AppUser.id)).all()
    logs = db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(100)).all()
    return templates.TemplateResponse(
        request,
        "admin_users.html",
        {"user": user, "users": users, "logs": logs, "created": created, "error": None},
    )


@app.post("/admin/users")
async def admin_create_user(
    request: Request,
    username: str = Form(...),
    display_name: str = Form(...),
    role: str = Form("viewer"),
    user: AppUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    username = username.strip()
    if not username or role not in ROLES:
        raise HTTPException(status_code=400, detail="입력값이 올바르지 않습니다.")
    if db.scalar(select(AppUser).where(AppUser.username == username)):
        users = db.scalars(select(AppUser).order_by(AppUser.id)).all()
        logs = db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(100)).all()
        return templates.TemplateResponse(
            request,
            "admin_users.html",
            {"user": user, "users": users, "logs": logs, "created": None, "error": "이미 있는 아이디입니다."},
            status_code=400,
        )

    password = generate_password()
    db.add(
        AppUser(
            username=username,
            display_name=display_name.strip() or username,
            password_hash=hash_password(password),
            role=role,
            must_change_password=True,
        )
    )
    log_action(db, user, "create_user", f"{username}({role})", None)
    db.commit()
    return RedirectResponse(f"/admin/users?created={username}:{password}", status_code=303)


@app.post("/admin/users/{user_id}/reset")
async def admin_reset_password(
    user_id: int,
    user: AppUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    target = db.get(AppUser, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="계정을 찾을 수 없습니다.")
    password = generate_password()
    target.password_hash = hash_password(password)
    target.must_change_password = True
    log_action(db, user, "reset_password", target.username, None)
    db.commit()
    return RedirectResponse(f"/admin/users?created={target.username}:{password}", status_code=303)


@app.post("/admin/users/{user_id}/toggle")
async def admin_toggle_user(
    user_id: int,
    user: AppUser = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Response:
    target = db.get(AppUser, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="계정을 찾을 수 없습니다.")
    if target.id == user.id:
        raise HTTPException(status_code=400, detail="자기 계정은 비활성화할 수 없습니다.")
    target.active = not target.active
    log_action(db, user, "toggle_user", f"{target.username} → {'활성' if target.active else '중지'}", None)
    db.commit()
    return RedirectResponse("/admin/users", status_code=303)
