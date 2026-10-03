import os
from datetime import datetime, timezone

from sqlalchemy import create_engine, Column, Integer, String, Boolean, Text, DateTime, UniqueConstraint
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker

# Support both dev and PyInstaller bundled paths
if getattr(__import__('sys'), 'frozen', False):
    # PyInstaller: keep DB next to executable for persistence
    base_dir = os.path.dirname(__import__('sys').executable)
else:
    base_dir = os.path.dirname(__file__)

import sys
backend_dir = os.path.dirname(__file__)
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

# JARVIS_DB_PATH overrides the location (used by tests with a temporary DB and
# available for future user-data-dir relocation). Never point it at real data
# during tests.
if os.getenv("VEGA_PROFILE") == "beta":
    try:
        from beta_target import resolve_beta_db_path
    except ImportError:
        try:
            from backend.beta_target import resolve_beta_db_path
        except ImportError:
            from .beta_target import resolve_beta_db_path
    DB_PATH = resolve_beta_db_path()
elif os.getenv("JARVIS_DB_PATH"):
    DB_PATH = os.getenv("JARVIS_DB_PATH")
else:
    DB_PATH = os.path.join(base_dir, "jarvis.db")

SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def utcnow_naive() -> datetime:
    """Naive UTC now — the storage format for all *_utc DateTime columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def as_utc(dt):
    """Attach UTC tzinfo to a naive datetime read from the DB."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso_utc(dt) -> str | None:
    """ISO-8601 rendering (with Z) of a naive-UTC column value."""
    dt = as_utc(dt)
    if dt is None:
        return None
    return dt.isoformat().replace("+00:00", "Z")


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    text = Column(String(500), index=True, nullable=False)
    completed = Column(Boolean, default=False, nullable=False)
    # --- M1 additions (applied by migrations.run_migrations, not create_all) ---
    deadline_utc = Column(DateTime, nullable=True, index=True)
    subject = Column(String(200), nullable=True)
    source = Column(String(50), nullable=False, default="ui")
    created_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)
    # --- P2 additions (schema_version 2; nullable, old rows untouched) ---
    workspace_id = Column(Integer, nullable=True, index=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "text": self.text,
            "completed": bool(self.completed),
            "deadline_utc": iso_utc(self.deadline_utc),
            "subject": self.subject,
            "source": self.source or "ui",
            "workspace_id": self.workspace_id,
            "created_at": iso_utc(self.created_at),
            "updated_at": iso_utc(self.updated_at),
        }


class Note(Base):
    __tablename__ = "notes"

    id = Column(Integer, primary_key=True, index=True)
    text = Column(Text, default="", nullable=False)


class Timer(Base):
    """Persistent countdown. Lifecycle: active -> fired | cancelled | missed."""
    __tablename__ = "timers"

    id = Column(Integer, primary_key=True, index=True)
    label = Column(String(300), default="", nullable=False)
    duration_seconds = Column(Integer, nullable=False)
    start_utc = Column(DateTime, nullable=False)
    end_utc = Column(DateTime, nullable=False, index=True)
    status = Column(String(20), nullable=False, default="active", index=True)
    source = Column(String(50), nullable=False, default="assistant")
    timezone_name = Column(String(120), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow_naive)
    ended_at = Column(DateTime, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "duration_seconds": self.duration_seconds,
            "start_utc": iso_utc(self.start_utc),
            "end_utc": iso_utc(self.end_utc),
            "status": self.status,
            "source": self.source,
            "timezone_name": self.timezone_name,
            "created_at": iso_utc(self.created_at),
            "ended_at": iso_utc(self.ended_at),
        }


class Reminder(Base):
    """One-shot reminder. Lifecycle: pending -> fired | cancelled | missed (snooze revives to pending, moves due_utc)."""
    __tablename__ = "reminders"

    id = Column(Integer, primary_key=True, index=True)
    text = Column(String(500), nullable=False)
    due_utc = Column(DateTime, nullable=False, index=True)
    status = Column(String(20), nullable=False, default="pending", index=True)
    snooze_count = Column(Integer, nullable=False, default=0)
    source = Column(String(50), nullable=False, default="assistant")
    timezone_name = Column(String(120), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow_naive)
    fired_at = Column(DateTime, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "text": self.text,
            "due_utc": iso_utc(self.due_utc),
            "status": self.status,
            "snooze_count": self.snooze_count,
            "source": self.source,
            "timezone_name": self.timezone_name,
            "created_at": iso_utc(self.created_at),
            "fired_at": iso_utc(self.fired_at),
        }


class FocusSession(Base):
    """Lifecycle: active -> completed | cancelled."""
    __tablename__ = "focus_sessions"

    id = Column(Integer, primary_key=True, index=True)
    objective = Column(String(300), default="", nullable=False)
    start_utc = Column(DateTime, nullable=False)
    planned_end_utc = Column(DateTime, nullable=False)
    actual_end_utc = Column(DateTime, nullable=True)
    status = Column(String(20), nullable=False, default="active", index=True)
    outcome_note = Column(Text, default="", nullable=False)
    timer_id = Column(Integer, nullable=True)
    source = Column(String(50), nullable=False, default="assistant")
    timezone_name = Column(String(120), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow_naive)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "objective": self.objective,
            "start_utc": iso_utc(self.start_utc),
            "planned_end_utc": iso_utc(self.planned_end_utc),
            "actual_end_utc": iso_utc(self.actual_end_utc),
            "status": self.status,
            "outcome_note": self.outcome_note,
            "timer_id": self.timer_id,
            "source": self.source,
            "timezone_name": self.timezone_name,
            "created_at": iso_utc(self.created_at),
        }


class ScheduledAlert(Base):
    """Delivery queue for due events. One scheduler owner claims rows atomically
    (UPDATE ... WHERE status='pending'), so overlapping processes still deliver
    at most once. Lifecycle: pending -> delivered | missed | cancelled."""
    __tablename__ = "scheduled_alerts"

    id = Column(Integer, primary_key=True, index=True)
    kind = Column(String(40), nullable=False)  # timer_due | reminder_due | task_deadline | focus_end
    entity_type = Column(String(30), nullable=False)
    entity_id = Column(Integer, nullable=False)
    due_utc = Column(DateTime, nullable=False, index=True)
    status = Column(String(20), nullable=False, default="pending", index=True)
    message = Column(String(500), nullable=False, default="")
    delivered_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow_naive)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "due_utc": iso_utc(self.due_utc),
            "status": self.status,
            "message": self.message,
            "delivered_at": iso_utc(self.delivered_at),
            "created_at": iso_utc(self.created_at),
        }


class ActionReceipt(Base):
    """Proof of every executed assistant action. idempotency_key is unique so a
    retried request returns the stored receipt instead of re-executing."""
    __tablename__ = "action_receipts"

    id = Column(Integer, primary_key=True, index=True)
    idempotency_key = Column(String(120), nullable=True, unique=True, index=True)
    action = Column(String(60), nullable=False)
    target_key = Column(String(64), nullable=True)
    success = Column(Boolean, nullable=False, default=True)
    entity_type = Column(String(30), nullable=True)
    entity_id = Column(Integer, nullable=True)
    message = Column(Text, nullable=False, default="")
    command_text = Column(String(600), nullable=True)
    source = Column(String(50), nullable=True)
    created_utc = Column(DateTime, nullable=False, default=utcnow_naive)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "idempotency_key": self.idempotency_key,
            "action": self.action,
            "target_key": self.target_key,
            "success": bool(self.success),
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "message": self.message,
            "command_text": self.command_text,
            "source": self.source,
            "created_utc": iso_utc(self.created_utc),
        }


class AIRadarItem(Base):
    """One deduplicated AI Radar finding. Created/updated by ai_radar.run_radar;
    never written from fetched text directly (titles/URLs are sanitized and
    validated first). New tables are added by create_all — existing task/note
    data is untouched. verification_status: verified | unverified | expired.
    offer_billing_required is nullable on purpose: None means 'not confirmed'."""
    __tablename__ = "ai_radar_items"

    id = Column(Integer, primary_key=True, index=True)
    source_id = Column(String(300), nullable=False, unique=True, index=True)
    canonical_url = Column(String(600), nullable=False, index=True)
    category = Column(String(30), nullable=False, default="other", index=True)
    title = Column(String(400), nullable=False, default="")
    publisher = Column(String(200), nullable=True)
    url = Column(String(600), nullable=False)
    published_utc = Column(DateTime, nullable=True, index=True)
    first_seen_utc = Column(DateTime, nullable=False, default=utcnow_naive)
    last_checked_utc = Column(DateTime, nullable=False, default=utcnow_naive)
    summary = Column(Text, nullable=False, default="")
    evidence_urls = Column(Text, nullable=False, default="[]")  # JSON list
    verification_status = Column(String(20), nullable=False, default="unverified", index=True)
    # Optional offer terms (free tier / credit grant). All nullable -> 'unknown'.
    offer_kind = Column(String(30), nullable=True)
    offer_eligibility = Column(String(300), nullable=True)
    offer_quota = Column(String(300), nullable=True)
    offer_billing_required = Column(Boolean, nullable=True)
    offer_region = Column(String(120), nullable=True)
    offer_expires_utc = Column(DateTime, nullable=True)
    offer_terms_status = Column(String(20), nullable=True)  # confirmed|unconfirmed|expired
    is_read = Column(Boolean, nullable=False, default=False)
    is_dismissed = Column(Boolean, nullable=False, default=False)
    last_run_id = Column(Integer, nullable=True)

    def to_dict(self) -> dict:
        import json as _json
        try:
            evidence = _json.loads(self.evidence_urls or "[]")
        except Exception:
            evidence = []
        return {
            "id": self.id,
            "source_id": self.source_id,
            "canonical_url": self.canonical_url,
            "category": self.category,
            "title": self.title,
            "publisher": self.publisher,
            "url": self.url,
            "published_utc": iso_utc(self.published_utc),
            "first_seen_utc": iso_utc(self.first_seen_utc),
            "last_checked_utc": iso_utc(self.last_checked_utc),
            "summary": self.summary,
            "evidence_urls": evidence,
            "verification_status": self.verification_status,
            "offer": {
                "kind": self.offer_kind,
                "eligibility": self.offer_eligibility,
                "quota": self.offer_quota,
                "billing_required": self.offer_billing_required,
                "region": self.offer_region,
                "expires_utc": iso_utc(self.offer_expires_utc),
                "terms_status": self.offer_terms_status,
            } if self.offer_kind or self.offer_terms_status else None,
            "is_read": bool(self.is_read),
            "is_dismissed": bool(self.is_dismissed),
            "last_run_id": self.last_run_id,
        }


class AIRadarRun(Base):
    """Audit row per research pass. status: running | success | partial | failed.
    trigger: scheduled | manual | restart. A single 'running' row acts as the
    cross-process lock that prevents duplicate daily jobs (see radar_scheduler)."""
    __tablename__ = "ai_radar_runs"

    id = Column(Integer, primary_key=True, index=True)
    started_utc = Column(DateTime, nullable=False, default=utcnow_naive, index=True)
    finished_utc = Column(DateTime, nullable=True)
    status = Column(String(20), nullable=False, default="running", index=True)
    trigger = Column(String(20), nullable=False, default="scheduled")
    sources_ok = Column(Integer, nullable=False, default=0)
    sources_failed = Column(Integer, nullable=False, default=0)
    new_items = Column(Integer, nullable=False, default=0)
    per_source = Column(Text, nullable=False, default="{}")  # JSON {source: {count, error}}
    digest_notified = Column(Boolean, nullable=False, default=False)
    digest_alert_id = Column(Integer, nullable=True)

    def to_dict(self) -> dict:
        import json as _json
        try:
            per_source = _json.loads(self.per_source or "{}")
        except Exception:
            per_source = {}
        return {
            "id": self.id,
            "started_utc": iso_utc(self.started_utc),
            "finished_utc": iso_utc(self.finished_utc),
            "status": self.status,
            "trigger": self.trigger,
            "sources_ok": self.sources_ok,
            "sources_failed": self.sources_failed,
            "new_items": self.new_items,
            "per_source": per_source,
            "digest_notified": bool(self.digest_notified),
            "digest_alert_id": self.digest_alert_id,
        }


class Workspace(Base):
    """P2 durable project/subject context. A stable integer id plus a unique
    slug ``key``. ``path`` is an OPTIONAL, already-validated local folder (never
    auto-enumerated). next_action/blocker/goal/status are VEGA-owned facts the
    user set; they are not written from unverified model output. New table
    (created by create_all); adding it never touches existing rows."""
    __tablename__ = "workspaces"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(120), nullable=False, unique=True, index=True)
    name = Column(String(200), nullable=False)
    type = Column(String(20), nullable=False, default="personal")  # personal | academic
    path = Column(String(600), nullable=True)                      # validated local folder
    goal = Column(String(500), nullable=False, default="")
    status = Column(String(20), nullable=False, default="active", index=True)  # active|paused|done
    next_action = Column(String(500), nullable=False, default="")
    blocker = Column(String(500), nullable=False, default="")
    created_utc = Column(DateTime, nullable=False, default=utcnow_naive)
    updated_utc = Column(DateTime, nullable=False, default=utcnow_naive, index=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "key": self.key, "name": self.name, "type": self.type,
            "path": self.path, "goal": self.goal, "status": self.status,
            "next_action": self.next_action, "blocker": self.blocker,
            "created_utc": iso_utc(self.created_utc),
            "last_activity_utc": iso_utc(self.updated_utc),
        }


class Coursework(Base):
    """Stage 3 academic loop — one assignable item (assignment / exam / lab /
    reading) for an optional subject (workspace). Due date stored as naive UTC
    (validated against the local timezone on the way in; past dates rejected).
    ``effort_minutes`` is the user's OWN estimate, never a model guess. New
    table (create_all); adding it never touches existing rows."""
    __tablename__ = "coursework"

    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, nullable=True, index=True)  # subject, optional
    kind = Column(String(30), nullable=False, default="assignment")  # assignment|exam|lab|reading|project
    title = Column(String(300), nullable=False)
    due_utc = Column(DateTime, nullable=True, index=True)
    effort_minutes = Column(Integer, nullable=True)   # user-estimated effort
    completed = Column(Boolean, nullable=False, default=False, index=True)
    source = Column(String(50), nullable=False, default="ui")
    created_utc = Column(DateTime, nullable=False, default=utcnow_naive)
    updated_utc = Column(DateTime, nullable=False, default=utcnow_naive, index=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "workspace_id": self.workspace_id, "kind": self.kind,
            "title": self.title, "due_utc": iso_utc(self.due_utc),
            "effort_minutes": self.effort_minutes, "completed": self.completed,
            "source": self.source, "created_utc": iso_utc(self.created_utc),
            "updated_utc": iso_utc(self.updated_utc),
        }


class SessionNote(Base):
    """A closed-session summary the user drafted, edited, and confirmed. Stores
    outcome/blocker/next_action + when. Optionally linked to a workspace. Text
    comes from the user's confirmed edit, never from an unverified model claim."""
    __tablename__ = "session_notes"

    id = Column(Integer, primary_key=True, index=True)
    workspace_id = Column(Integer, nullable=True, index=True)
    outcome = Column(Text, nullable=False, default="")
    blocker = Column(Text, nullable=False, default="")
    next_action = Column(Text, nullable=False, default="")
    source = Column(String(50), nullable=False, default="ui")
    created_utc = Column(DateTime, nullable=False, default=utcnow_naive, index=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "workspace_id": self.workspace_id,
            "outcome": self.outcome, "blocker": self.blocker,
            "next_action": self.next_action, "source": self.source,
            "created_utc": iso_utc(self.created_utc),
        }
