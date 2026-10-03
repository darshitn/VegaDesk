"""Deterministic idempotent seed script for VEGA Beta Acceptance Profile.

Creates synthetic tasks, projects, coursework, and session notes exclusively
within the verified beta profile database.

Safety guards:
- Strictly refuses to touch production `jarvis.db`
- Requires VEGA_PROFILE=beta or an explicit beta database path
- Zero scheduled alert rows (no surprise popups/sounds at startup)
- Fully idempotent: running multiple times updates rather than duplicates
"""

import os
import sys
from datetime import datetime, timezone, timedelta

# Ensure backend root is on sys.path
backend_dir = os.path.dirname(__file__)
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)


def get_beta_db_path() -> str:
    from beta_target import resolve_beta_db_path
    return resolve_beta_db_path()


def verify_beta_target(db_path: str, custom_root: str = None):
    from beta_target import validate_beta_db_path
    try:
        validate_beta_db_path(db_path, custom_root=custom_root)
    except Exception as e:
        print(f"[FATAL SEED SAFETY ERROR] Refusing to seed: {e}", file=sys.stderr)
        sys.exit(1)


def seed_database(db_path: str = None) -> dict:
    if os.getenv("VEGA_PROFILE") != "beta":
        os.environ["VEGA_PROFILE"] = "beta"
    target_path = db_path or get_beta_db_path()
    verify_beta_target(target_path, custom_root=os.getenv("VEGA_PROFILE_ROOT"))

    os.environ["JARVIS_DB_PATH"] = target_path

    # Now import DB models and migrations
    import db
    if os.path.normpath(db.DB_PATH) != os.path.normpath(target_path):
        import importlib
        importlib.reload(db)

    from db import SessionLocal, Task, Note, Workspace, Coursework, SessionNote, ScheduledAlert, Timer, Reminder
    import migrations

    # Ensure tables exist and migrations are up to date
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    migrations.run_migrations(db.engine, db.Base, target_path)
    db.Base.metadata.create_all(bind=db.engine)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    stats = {"tasks": 0, "workspaces": 0, "coursework": 0, "notes": 0, "session_notes": 0}

    with SessionLocal() as session:
        # 1. Project / Workspace
        ws_key = "compiler-optimizer"
        ws = session.query(Workspace).filter_by(key=ws_key).first()
        if not ws:
            ws = Workspace(
                key=ws_key,
                name="Compiler Optimization Engine",
                type="academic",
                goal="Implement LLVM passes for loop invariant code motion",
                status="active",
                blocker="Register spilling on benchmark suite B",
                next_action="Profile memory footprint with valgrind",
                updated_utc=now,
            )
            session.add(ws)
            session.flush()
        stats["workspaces"] = 1

        # 2. Synthetic Tasks
        task_data = [
            {
                "text": "Implement SSA live range analysis pass",
                "completed": False,
                "deadline_utc": now + timedelta(days=2),
                "subject": "Compilers",
                "workspace_id": ws.id,
            },
            {
                "text": "Write unit tests for dominator tree verification",
                "completed": False,
                "deadline_utc": now + timedelta(days=4),
                "subject": "Compilers",
                "workspace_id": ws.id,
            },
            {
                "text": "Set up LLVM test suite harness",
                "completed": True,
                "deadline_utc": None,
                "subject": "Compilers",
                "workspace_id": ws.id,
            },
            {
                "text": "Review AST parser token stream grammar",
                "completed": True,
                "deadline_utc": None,
                "subject": "Compilers",
                "workspace_id": ws.id,
            },
        ]

        for idx, item in enumerate(task_data):
            seed_key = f"seed:task:{idx}"
            existing = session.query(Task).filter_by(source=seed_key).first()
            if not existing:
                existing = session.query(Task).filter_by(text=item["text"]).first()
                if existing:
                    existing.source = seed_key
            if not existing:
                existing = Task(
                    text=item["text"],
                    completed=item["completed"],
                    deadline_utc=item["deadline_utc"],
                    subject=item["subject"],
                    source=seed_key,
                    workspace_id=item["workspace_id"],
                    updated_at=now,
                )
                session.add(existing)
            # Deduplication: do not overwrite user modifications (edited title/status) if already present
            stats["tasks"] += 1

        # 3. Coursework
        coursework_data = [
            {
                "title": "Advanced Compilers Assignment 2",
                "kind": "assignment",
                "effort_minutes": 180,
                "completed": False,
                "due_utc": now + timedelta(days=5),
                "workspace_id": ws.id,
            },
            {
                "title": "Midterm Examination",
                "kind": "exam",
                "effort_minutes": 120,
                "completed": False,
                "due_utc": now + timedelta(days=12),
                "workspace_id": ws.id,
            },
        ]

        for idx, cw in enumerate(coursework_data):
            seed_key = f"seed:coursework:{idx}"
            existing_cw = session.query(Coursework).filter_by(source=seed_key).first()
            if not existing_cw:
                existing_cw = session.query(Coursework).filter_by(title=cw["title"]).first()
                if existing_cw:
                    existing_cw.source = seed_key
            if not existing_cw:
                existing_cw = Coursework(
                    title=cw["title"],
                    kind=cw["kind"],
                    effort_minutes=cw["effort_minutes"],
                    completed=cw["completed"],
                    due_utc=cw["due_utc"],
                    workspace_id=cw["workspace_id"],
                    source=seed_key,
                    updated_utc=now,
                )
                session.add(existing_cw)
            # Deduplication: do not overwrite user modifications if already present
            stats["coursework"] += 1

        # 4. Session Note
        sn = session.query(SessionNote).filter_by(workspace_id=ws.id, source="seed:session_note:0").first()
        if not sn:
            sn = session.query(SessionNote).filter_by(workspace_id=ws.id, source="seed").first()
            if sn:
                sn.source = "seed:session_note:0"
        if not sn:
            sn = SessionNote(
                workspace_id=ws.id,
                outcome="Completed initial control flow graph validation pass.",
                blocker="Spilling overhead remains high in tight nested loops.",
                next_action="Tune heuristic for register prioritization before next benchmark run.",
                source="seed:session_note:0",
                created_utc=now,
            )
            session.add(sn)
        stats["session_notes"] = 1

        # 5. General Note
        note_text = "VEGA Beta Acceptance Profile - Deterministic Testing Environment"
        n = session.query(Note).filter(Note.text.like("%VEGA Beta Acceptance%")).first()
        if not n:
            n = Note(text=note_text)
            session.add(n)
        stats["notes"] = 1

        # Note: Do NOT delete ScheduledAlert, Timer, or Reminder rows!
        # Reseeding preserves user-created and active acceptance state.
        session.commit()

    return {
        "status": "success",
        "target_db": target_path,
        "profile": "beta",
        "counts": stats,
    }


if __name__ == "__main__":
    os.environ.setdefault("VEGA_PROFILE", "beta")
    result = seed_database()
    print("=" * 60)
    print("VEGA BETA ACCEPTANCE PROFILE SEEDED SUCCESSFULLY")
    print(f"Target DB:  {result['target_db']}")
    print(f"Profile:    {result['profile']}")
    print(f"Seeded:     {result['counts']}")
    print("=" * 60)
    print("To reset and re-seed, run:")
    print("  python backend/seed_beta.py")
    print("=" * 60)
