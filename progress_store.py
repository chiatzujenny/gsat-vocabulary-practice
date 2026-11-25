"""Persistent quiz attempts; connection credentials are supplied by the host."""

import hashlib
import json
import secrets
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, Integer, MetaData, String, Table, Text, create_engine, func, select, update
from sqlalchemy.exc import IntegrityError


metadata = MetaData()
attempts = Table(
    "quiz_attempts", metadata,
    Column("attempt_id", String(36), primary_key=True),
    Column("student_name", String(80), nullable=False),
    Column("student_id", String(36), nullable=False),
    Column("submitted_at", String(32), nullable=False),
    Column("quiz_type", String(20), nullable=False),
    Column("score", Integer, nullable=False),
    Column("total", Integer, nullable=False),
    Column("answers_json", Text, nullable=False),
)
students = Table(
    "quiz_students", metadata,
    Column("student_id", String(36), primary_key=True),
    Column("code_hash", String(64), nullable=False, unique=True),
    Column("created_at", String(32), nullable=False),
)
student_profiles = Table(
    "quiz_student_profiles", metadata,
    Column("student_id", String(36), primary_key=True),
    Column("display_name", String(80), nullable=False),
)
auth_failures = Table(
    "quiz_auth_failures", metadata,
    Column("scope", String(80), primary_key=True),
    Column("failures", Integer, nullable=False),
    Column("locked_until", Integer, nullable=False),
)
section_runs = Table(
    "quiz_section_runs", metadata,
    Column("attempt_id", String(36), primary_key=True),
    Column("student_id", String(36), nullable=False, index=True),
    Column("student_name", String(80), nullable=False),
    Column("submitted_at", String(32), nullable=False),
    Column("section", String(20), nullable=False),
    Column("score", Integer, nullable=False),
    Column("total", Integer, nullable=False),
    Column("duration_seconds", Integer, nullable=False),
    Column("answers_json", Text, nullable=False),
)


class ProgressStore:
    def __init__(self, database_url):
        # SQLite is useful for local testing; Community Cloud must use a remote DB.
        options = {"connect_timeout": 15} if database_url.startswith("postgresql") else {}
        self.engine = create_engine(database_url, pool_pre_ping=True, connect_args=options)

    def initialize(self):
        metadata.create_all(self.engine)

    def create_student_code(self):
        code = secrets.token_urlsafe(12)
        with self.engine.begin() as connection:
            connection.execute(students.insert().values(
                student_id=str(uuid.uuid4()),
                code_hash=hashlib.sha256(code.encode("utf-8")).hexdigest(),
                created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ))
        return code

    def authenticate_student(self, code):
        digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
        with self.engine.connect() as connection:
            return connection.execute(select(students.c.student_id).where(students.c.code_hash == digest)).scalar_one_or_none()

    def resolve_student_name(self, student_id, provided_name):
        name = provided_name.strip()
        with self.engine.begin() as connection:
            if name:
                changed = connection.execute(update(student_profiles).where(
                    student_profiles.c.student_id == student_id).values(display_name=name))
                if not changed.rowcount:
                    try:
                        with connection.begin_nested():
                            connection.execute(student_profiles.insert().values(student_id=student_id, display_name=name))
                    except IntegrityError:
                        connection.execute(update(student_profiles).where(
                            student_profiles.c.student_id == student_id).values(display_name=name))
                return name
            name = connection.execute(select(student_profiles.c.display_name).where(
                student_profiles.c.student_id == student_id)).scalar_one_or_none()
            if name:
                return name
            history = []
            for table in (section_runs, attempts):
                row = connection.execute(select(table.c.student_name, table.c.submitted_at).where(
                    table.c.student_id == student_id).order_by(table.c.submitted_at.desc()).limit(1)).first()
                if row:
                    history.append(row)
            return max(history, key=lambda row: row.submitted_at).student_name if history else f"學生-{student_id[:8]}"

    def save(self, attempt_id, student_id, student_name, quiz_type, quiz, responses):
        details = [
            {
                "question": q["question"],
                "selected": responses[i],
                "answer": q["answer"],
                "correct": responses[i] == q["answer"],
                "category": q["category"],
            }
            for i, q in enumerate(quiz)
        ]
        record = dict(
            attempt_id=attempt_id,
            student_name=student_name,
            student_id=student_id,
            submitted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            quiz_type=quiz_type,
            score=sum(item["correct"] for item in details),
            total=len(details),
            answers_json=json.dumps(details, ensure_ascii=False),
        )
        try:
            with self.engine.begin() as connection:
                connection.execute(attempts.insert().values(**record))
        except IntegrityError:
            # A retry after a successful write must not duplicate an attempt.
            with self.engine.connect() as connection:
                exists = connection.execute(
                    select(attempts.c.attempt_id).where(attempts.c.attempt_id == attempt_id)
                ).first()
            if not exists:
                raise

    def list_attempts(self):
        with self.engine.connect() as connection:
            rows = connection.execute(select(attempts).order_by(attempts.c.submitted_at.desc())).mappings()
            return [dict(row, answers=json.loads(row["answers_json"])) for row in rows]

    def save_section(self, attempt_id, student_id, student_name, section, questions, responses, duration_seconds):
        details = [{"question": q["question"], "selected": responses[i],
                    "answer": q["answer"], "correct": responses[i] == q["answer"],
                    "category": q.get("category", section),
                    "group_name": q.get("group_name", ""),
                    "passage": q.get("passage", ""),
                    "options": q.get("options", [])} for i, q in enumerate(questions)]
        row = dict(attempt_id=attempt_id, student_id=student_id, student_name=student_name,
                   submitted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   section=section, score=sum(x["correct"] for x in details), total=len(details),
                   duration_seconds=max(0, int(duration_seconds)),
                   answers_json=json.dumps(details, ensure_ascii=False))
        try:
            with self.engine.begin() as connection:
                connection.execute(section_runs.insert().values(**row))
        except IntegrityError:
            with self.engine.connect() as connection:
                exists = connection.execute(select(section_runs.c.attempt_id).where(
                    section_runs.c.attempt_id == attempt_id)).first()
            if not exists:
                raise

    def list_section_runs(self, student_id=None):
        statement = select(section_runs).order_by(section_runs.c.submitted_at.desc())
        if student_id is not None:
            statement = statement.where(section_runs.c.student_id == student_id)
        with self.engine.connect() as connection:
            return [dict(row, answers=json.loads(row["answers_json"]))
                    for row in connection.execute(statement).mappings()]

    def legacy_completed(self, student_id):
        with self.engine.connect() as connection:
            return connection.execute(select(func.count()).select_from(attempts).where(
                attempts.c.student_id == student_id)).scalar_one()

    def admin_locked(self, now):
        with self.engine.connect() as connection:
            row = connection.execute(select(auth_failures).where(auth_failures.c.scope == "admin")).first()
            return bool(row and row.locked_until > now)

    def record_admin_failure(self, now):
        # A global backoff also applies across new browser sessions and app restarts.
        # Repeated failures may temporarily delay legitimate admin logins.
        with self.engine.begin() as connection:
            result = connection.execute(
                update(auth_failures).where(auth_failures.c.scope == "admin").values(
                    failures=auth_failures.c.failures + 1,
                    locked_until=now + 5,
                )
            )
            if not result.rowcount:
                try:
                    with connection.begin_nested():
                        connection.execute(auth_failures.insert().values(scope="admin", failures=1, locked_until=now + 5))
                except IntegrityError:
                    connection.execute(update(auth_failures).where(auth_failures.c.scope == "admin").values(locked_until=now + 5))

    def clear_admin_failures(self):
        with self.engine.begin() as connection:
            connection.execute(auth_failures.delete().where(auth_failures.c.scope == "admin"))
