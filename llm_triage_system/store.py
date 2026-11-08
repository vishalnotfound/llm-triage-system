"""SQLite queue and transactional ticket history."""

import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

STATES = ("pending", "needs_review", "triaged", "resolved")
LEASE_SECONDS = 300

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'needs_review', 'triaged', 'resolved')),
    decision TEXT,
    lease_token TEXT,
    lease_until INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ticket_status ON tickets(status, created_at, id);
CREATE TABLE IF NOT EXISTS events (
    sequence INTEGER PRIMARY KEY,
    ticket_id TEXT NOT NULL REFERENCES tickets(id),
    kind TEXT NOT NULL,
    actor TEXT NOT NULL,
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ticket_events ON events(ticket_id, sequence);
"""


class QueueError(Exception):
    """An operation conflicts with the current ticket state."""


class Queue:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Create with restricted permissions before SQLite opens the file.
        os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)

    @contextmanager
    def write(self):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            yield

    def event(self, ticket_id, kind, actor, detail):
        self.db.execute(
            "INSERT INTO events(ticket_id, kind, actor, detail) VALUES (?, ?, ?, ?)",
            (ticket_id, kind, actor, json.dumps(detail)),
        )

    def submit(self, ticket, actor):
        with self.write():
            existing = self.db.execute(
                "SELECT subject, body FROM tickets WHERE id = ?", (ticket["id"],)
            ).fetchone()
            if existing:
                if dict(existing) != {"subject": ticket["subject"], "body": ticket["body"]}:
                    raise QueueError("That ticket ID already has different content")
                return False
            self.db.execute(
                "INSERT INTO tickets(id, subject, body) VALUES (:id, :subject, :body)", ticket
            )
            self.event(ticket["id"], "submitted", actor, {})
            return True

    def get(self, ticket_id):
        row = self.db.execute(
            """SELECT id, subject, body, status, decision, created_at, updated_at
               FROM tickets WHERE id = ?""",
            (ticket_id,),
        ).fetchone()
        if row is None:
            raise QueueError("Ticket not found")
        return self.decode(row)

    @staticmethod
    def decode(row):
        result = dict(row)
        result["decision"] = json.loads(result["decision"]) if result["decision"] else None
        return result

    def list(self, status, route, limit, offset):
        rows = self.db.execute(
            """SELECT id, subject, status, decision, created_at, updated_at
               FROM tickets
               WHERE (? IS NULL OR status = ?) AND
                     (? IS NULL OR json_extract(decision, '$.route') = ?)
               ORDER BY CASE
                   WHEN status != 'resolved' AND json_extract(decision, '$.priority') = 'urgent' THEN 0
                   WHEN status = 'needs_review' THEN 1
                   WHEN status = 'pending' THEN 2
                   WHEN status = 'triaged' THEN 3
                   ELSE 4
               END, created_at, id
               LIMIT ? OFFSET ?""",
            (status, status, route, route, limit, offset),
        )
        return [self.decode(row) for row in rows]

    def history(self, ticket_id):
        self.get(ticket_id)
        return [
            {**row, "detail": json.loads(row["detail"])}
            for row in self.db.execute(
                """SELECT sequence, kind, actor, detail, created_at
                   FROM events WHERE ticket_id = ? ORDER BY sequence""",
                (ticket_id,),
            )
        ]

    def claim(self, ticket_id, actor):
        with self.write():
            ticket = self.get(ticket_id)
            now = int(time.time())
            token = secrets.token_hex(16)
            cursor = self.db.execute(
                """UPDATE tickets SET lease_token = ?, lease_until = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE id = ? AND status IN ('pending', 'needs_review')
                   AND (lease_until IS NULL OR lease_until <= ?)""",
                (token, now + LEASE_SECONDS, ticket_id, now),
            )
            if not cursor.rowcount:
                raise QueueError("Ticket is already triaged, resolved, or being classified")
            self.event(ticket_id, "classification_started", actor, {})
            return ticket, token

    def finish(self, ticket_id, token, decision, detail, actor):
        with self.write():
            status = "triaged" if decision else "needs_review"
            cursor = self.db.execute(
                """UPDATE tickets SET status = ?, decision = ?, lease_token = NULL,
                   lease_until = NULL, updated_at = CURRENT_TIMESTAMP
                   WHERE id = ? AND lease_token = ?""",
                (status, json.dumps(decision) if decision else None, ticket_id, token),
            )
            if not cursor.rowcount:
                raise QueueError("Ticket changed while classification was in progress")
            self.event(ticket_id, status, actor, {**detail, "decision": decision})

    def review(self, ticket_id, decision, reason, actor, policy_sha256):
        with self.write():
            previous = self.get(ticket_id)
            if previous["status"] == "resolved":
                raise QueueError("Reopen the ticket before changing its decision")
            self.db.execute(
                """UPDATE tickets SET status = 'triaged', decision = ?, lease_token = NULL,
                   lease_until = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
                (json.dumps(decision), ticket_id),
            )
            self.event(ticket_id, "reviewed", actor, {
                "previous_decision": previous["decision"], "decision": decision, "reason": reason,
                "policy_sha256": policy_sha256,
            })

    def transition(self, ticket_id, expected, status, reason, actor):
        with self.write():
            ticket = self.get(ticket_id)
            if ticket["status"] != expected:
                raise QueueError(f"Ticket must be {expected} for this operation")
            self.db.execute(
                "UPDATE tickets SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (status, ticket_id),
            )
            kind = "reopened" if status == "triaged" else "resolved"
            self.event(ticket_id, kind, actor, {"reason": reason, "previous_status": expected})
