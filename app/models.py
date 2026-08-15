"""데이터 모델.

설계 원칙
---------
1. **사람 1명 = 1행.** 엑셀의 매트릭스 구조를 그대로 옮기지 않는다.
   열 구성이 파일마다 다르고 한 칸에 여러 명이 들어가기 때문이다.
2. **삭제하지 않는다.** 자리(assignment)와 번호(person_phone)는 `valid_to`만 채워
   과거 상태를 그대로 남긴다 (SCD Type-2). "6월엔 고영득, 8월엔 이윤주"가
   자동으로 이력이 되고, 특정 시점 조회도 가능해진다.
3. **출처를 항상 붙인다.** 모든 값은 어느 파일 어느 칸에서 왔는지 되짚을 수 있다.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# 자리 구분
DESK = "desk"
REPORTER = "reporter"

# 변경 처리 상태
PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
APPLIED = "applied"

# 변경 유형
NEW = "new"
UPDATE = "update"
REMOVE = "remove"
CONFLICT = "conflict"


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ── 매체 ────────────────────────────────────────────────────────────────────

class Outlet(Base, TimestampMixin):
    __tablename__ = "outlet"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    name_key: Mapped[str] = mapped_column(String(80), index=True)
    category: Mapped[str] = mapped_column(String(20), index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=9000)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    aliases: Mapped[list["OutletAlias"]] = relationship(
        back_populates="outlet", cascade="all, delete-orphan"
    )
    assignments: Mapped[list["Assignment"]] = relationship(back_populates="outlet")

    __table_args__ = (Index("ix_outlet_category_order", "category", "sort_order"),)


class OutletAlias(Base):
    __tablename__ = "outlet_alias"

    id: Mapped[int] = mapped_column(primary_key=True)
    outlet_id: Mapped[int] = mapped_column(ForeignKey("outlet.id", ondelete="CASCADE"))
    alias: Mapped[str] = mapped_column(String(80))
    alias_key: Mapped[str] = mapped_column(String(80), unique=True, index=True)

    outlet: Mapped[Outlet] = relationship(back_populates="aliases")


# ── 사람 ────────────────────────────────────────────────────────────────────

class Person(Base, TimestampMixin):
    __tablename__ = "person"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40), index=True)
    name_key: Mapped[str] = mapped_column(String(40), index=True)
    # 현재 대표 번호. 이력은 person_phone 에 남는다.
    phone: Mapped[str | None] = mapped_column(String(20), index=True)
    email: Mapped[str | None] = mapped_column(String(120), index=True)
    # 특이사항·성향·최근 접촉 기록 등 자유 기록
    memo: Mapped[str | None] = mapped_column(Text, nullable=True)

    phones: Mapped[list["PersonPhone"]] = relationship(
        back_populates="person", cascade="all, delete-orphan"
    )
    assignments: Mapped[list["Assignment"]] = relationship(
        back_populates="person", cascade="all, delete-orphan"
    )


class PersonPhone(Base):
    """번호 변경 이력. 같은 사람이 번호를 바꿔도 동일 인물로 추적된다."""

    __tablename__ = "person_phone"

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), index=True)
    phone: Mapped[str] = mapped_column(String(20), index=True)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_file.id", ondelete="SET NULL"), nullable=True
    )

    person: Mapped[Person] = relationship(back_populates="phones")


# ── 자리 (핵심 테이블) ──────────────────────────────────────────────────────

class Assignment(Base, TimestampMixin):
    """누가 · 어느 매체 · 어느 부서 · 무슨 직책으로 · 언제부터 언제까지."""

    __tablename__ = "assignment"

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), index=True)
    outlet_id: Mapped[int] = mapped_column(ForeignKey("outlet.id", ondelete="CASCADE"), index=True)

    kind: Mapped[str] = mapped_column(String(10), index=True)          # desk | reporter
    role_slot: Mapped[str | None] = mapped_column(String(30), index=True)  # 매트릭스 열
    role_label: Mapped[str | None] = mapped_column(String(120))            # 원문 직책
    dept: Mapped[str | None] = mapped_column(String(40), index=True)
    rank: Mapped[str] = mapped_column(String(10), default="기자")
    rank_order: Mapped[int] = mapped_column(Integer, default=50)
    note: Mapped[str | None] = mapped_column(String(120))
    concurrent: Mapped[bool] = mapped_column(Boolean, default=False)

    valid_from: Mapped[date] = mapped_column(Date, index=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)

    source_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_file.id", ondelete="SET NULL"), nullable=True
    )

    # 사람이 화면에서 직접 고친 자리. 다음 파일이 조용히 되돌리지 못하게 막는다.
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    edited_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True
    )
    # '오류 삭제' 표시. 애초에 잘못 들어온 자리 — 마감(정당한 이력)과 달리
    # 이력 화면에서도 오류로 구분된다. 기록을 지우지 않아야 다음 업로드가
    # 같은 데이터를 조용히 되살리는 것을 막을 수 있다.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    person: Mapped[Person] = relationship(back_populates="assignments")
    outlet: Mapped[Outlet] = relationship(back_populates="assignments")

    __table_args__ = (
        Index("ix_assignment_current", "outlet_id", "kind", "valid_to"),
        Index("ix_assignment_slot_current", "role_slot", "valid_to"),
    )

    @property
    def is_current(self) -> bool:
        return self.valid_to is None


# ── 원본 파일 / 원시 레코드 ─────────────────────────────────────────────────

class SourceFile(Base):
    __tablename__ = "source_file"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    file_kind: Mapped[str] = mapped_column(String(30))
    as_of: Mapped[date] = mapped_column(Date, index=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)  # 같은 파일 재업로드 차단
    stored_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    record_count: Mapped[int] = mapped_column(Integer, default=0)
    parse_warnings: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    unparsed: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # 시트(탭)별로 어떤 구조로 읽었고 몇 명을 얻었는지. 누락된 탭 확인용.
    sheet_stats: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    uploaded_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True
    )
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    records: Mapped[list["SourceRecord"]] = relationship(
        back_populates="source_file", cascade="all, delete-orphan"
    )


class SourceRecord(Base):
    """파일에서 뽑아낸 원시 1행. 파싱을 고쳐 다시 돌릴 수 있도록 원문을 보존한다."""

    __tablename__ = "source_record"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_file_id: Mapped[int] = mapped_column(
        ForeignKey("source_file.id", ondelete="CASCADE"), index=True
    )
    outlet_raw: Mapped[str] = mapped_column(String(80))
    outlet_name: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(40))
    phone: Mapped[str | None] = mapped_column(String(20))
    kind: Mapped[str] = mapped_column(String(10))
    role_slot: Mapped[str | None] = mapped_column(String(30))
    role_label: Mapped[str | None] = mapped_column(String(120))
    dept: Mapped[str | None] = mapped_column(String(40))
    rank: Mapped[str] = mapped_column(String(10), default="기자")
    rank_order: Mapped[int] = mapped_column(Integer, default=50)
    note: Mapped[str | None] = mapped_column(String(120))
    concurrent: Mapped[bool] = mapped_column(Boolean, default=False)
    source_ref: Mapped[str] = mapped_column(String(80))
    source_text: Mapped[str] = mapped_column(Text)
    warnings: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    source_file: Mapped[SourceFile] = relationship(back_populates="records")


# ── 변경 검토 (승인 후 반영) ────────────────────────────────────────────────

class ChangeSet(Base):
    """업로드 1건이 만들어 내는 변경 묶음."""

    __tablename__ = "change_set"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_file_id: Mapped[int] = mapped_column(ForeignKey("source_file.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(15), default=PENDING, index=True)
    # 비어 있던 DB를 처음 채운 적재인가. 초기 적재는 전부 '신규'이므로 변동 음영을 넣지 않는다.
    is_initial: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True
    )

    source_file: Mapped[SourceFile] = relationship()
    changes: Mapped[list["Change"]] = relationship(
        back_populates="change_set", cascade="all, delete-orphan"
    )


class Change(Base):
    """개별 변경 1건. 승인 대기 → 반영 또는 반려."""

    __tablename__ = "change"

    id: Mapped[int] = mapped_column(primary_key=True)
    change_set_id: Mapped[int] = mapped_column(
        ForeignKey("change_set.id", ondelete="CASCADE"), index=True
    )
    change_type: Mapped[str] = mapped_column(String(12), index=True)  # new|update|remove|conflict
    # 사람이 읽을 대상 표기
    outlet_name: Mapped[str] = mapped_column(String(80))
    person_name: Mapped[str] = mapped_column(String(40))
    field: Mapped[str] = mapped_column(String(30))                    # phone|role|dept|assignment
    old_value: Mapped[str | None] = mapped_column(String(300))
    new_value: Mapped[str | None] = mapped_column(String(300))
    reason: Mapped[str | None] = mapped_column(String(300))
    auto_apply: Mapped[bool] = mapped_column(Boolean, default=False)  # 자동 반영 대상인가
    decision: Mapped[str] = mapped_column(String(12), default=PENDING, index=True)
    applied: Mapped[bool] = mapped_column(Boolean, default=False)

    person_id: Mapped[int | None] = mapped_column(
        ForeignKey("person.id", ondelete="SET NULL"), nullable=True
    )
    outlet_id: Mapped[int | None] = mapped_column(
        ForeignKey("outlet.id", ondelete="SET NULL"), nullable=True
    )
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # 반영에 필요한 값 전체

    change_set: Mapped[ChangeSet] = relationship(back_populates="changes")


# ── 사용자 / 감사 로그 ──────────────────────────────────────────────────────

class AppUser(Base, TimestampMixin):
    __tablename__ = "app_user"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(40))
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(10), default="viewer")  # viewer|editor|admin
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    # 2단계 인증(TOTP). REQUIRE_TOTP=true 일 때 로그인에 사용된다.
    totp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    totp_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)

    @property
    def can_edit(self) -> bool:
        return self.role in {"editor", "admin"}

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


class AuditLog(Base):
    """누가 언제 무엇을 조회·변경했는지. 개인정보 취급 기록용."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True
    )
    username: Mapped[str] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(40), index=True)
    detail: Mapped[str | None] = mapped_column(String(500))
    ip: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    __table_args__ = (UniqueConstraint("id", name="uq_audit_log_id"),)
