"""Conversation store for the Astrix assistant.

Threads belong to an account, not to a browser tab. That is what makes "New
conversation" mean something: the old thread keeps its own id and history, a new
one starts empty, and both survive a reload, a different machine and a different
browser.

Every read and write is scoped by `user_id` in the query itself rather than
checked afterwards, so there is no path that returns another account's thread.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select

from ..memory.db import ConversationRow, MessageRow

TITLE_LENGTH = 60
MAX_CONVERSATIONS = 200


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def derive_title(text: str) -> str:
    """First line of the opening message, trimmed to a sidebar-sized label."""
    line = " ".join(text.strip().split())
    if len(line) <= TITLE_LENGTH:
        return line or "New conversation"
    return line[: TITLE_LENGTH - 1].rstrip() + "…"


class ConversationStore:
    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory

    # -- threads ------------------------------------------------------------ #

    def create(self, user_id: str, title: str = "New conversation") -> dict[str, Any]:
        row = ConversationRow(
            id=str(uuid.uuid4()),
            user_id=user_id,
            title=title[:160] or "New conversation",
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
        with self.session_factory() as db:
            db.add(row)
            db.commit()
            db.refresh(row)
            self._trim(db, user_id)
            return self._summary(row, 0)

    def list(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self.session_factory() as db:
            counts = dict(
                db.execute(
                    select(MessageRow.conversation_id, func.count(MessageRow.id))
                    .join(ConversationRow, ConversationRow.id == MessageRow.conversation_id)
                    .where(ConversationRow.user_id == user_id)
                    .group_by(MessageRow.conversation_id)
                ).all()
            )
            rows = db.scalars(
                select(ConversationRow)
                .where(ConversationRow.user_id == user_id, ConversationRow.archived.is_(False))
                .order_by(ConversationRow.updated_at.desc())
                .limit(limit)
            ).all()
            return [self._summary(r, counts.get(r.id, 0)) for r in rows]

    def get(self, user_id: str, conversation_id: str) -> dict[str, Any] | None:
        with self.session_factory() as db:
            row = db.scalar(
                select(ConversationRow).where(
                    ConversationRow.id == conversation_id, ConversationRow.user_id == user_id
                )
            )
            if row is None:
                return None
            messages = db.scalars(
                select(MessageRow)
                .where(MessageRow.conversation_id == conversation_id)
                .order_by(MessageRow.id)
            ).all()
            summary = self._summary(row, len(messages))
            summary["messages"] = [
                {
                    "role": m.role,
                    "content": m.content,
                    **(m.extra or {}),
                    "at": m.created_at.isoformat(),
                }
                for m in messages
            ]
            return summary

    def rename(self, user_id: str, conversation_id: str, title: str) -> bool:
        with self.session_factory() as db:
            row = db.scalar(
                select(ConversationRow).where(
                    ConversationRow.id == conversation_id, ConversationRow.user_id == user_id
                )
            )
            if row is None:
                return False
            row.title = (title.strip() or "New conversation")[:160]
            row.updated_at = _utcnow()
            db.commit()
            return True

    def delete(self, user_id: str, conversation_id: str) -> bool:
        with self.session_factory() as db:
            row = db.scalar(
                select(ConversationRow).where(
                    ConversationRow.id == conversation_id, ConversationRow.user_id == user_id
                )
            )
            if row is None:
                return False
            db.execute(delete(MessageRow).where(MessageRow.conversation_id == conversation_id))
            db.delete(row)
            db.commit()
            return True

    def clear_all(self, user_id: str) -> int:
        with self.session_factory() as db:
            ids = db.scalars(
                select(ConversationRow.id).where(ConversationRow.user_id == user_id)
            ).all()
            if not ids:
                return 0
            db.execute(delete(MessageRow).where(MessageRow.conversation_id.in_(ids)))
            db.execute(delete(ConversationRow).where(ConversationRow.id.in_(ids)))
            db.commit()
            return len(ids)

    # -- turns -------------------------------------------------------------- #

    def append(
        self,
        user_id: str,
        conversation_id: str | None,
        role: str,
        content: str,
        extra: dict[str, Any] | None = None,
    ) -> str:
        """Add one turn, creating the thread if the caller had none. Returns its id."""
        with self.session_factory() as db:
            row = None
            if conversation_id:
                row = db.scalar(
                    select(ConversationRow).where(
                        ConversationRow.id == conversation_id, ConversationRow.user_id == user_id
                    )
                )
            if row is None:
                row = ConversationRow(
                    id=conversation_id or str(uuid.uuid4()),
                    user_id=user_id,
                    title=derive_title(content) if role == "user" else "New conversation",
                    created_at=_utcnow(),
                    updated_at=_utcnow(),
                )
                db.add(row)
                db.flush()
            elif role == "user" and row.title == "New conversation":
                # The opening question names the thread.
                row.title = derive_title(content)

            db.add(
                MessageRow(
                    conversation_id=row.id,
                    role=role,
                    content=content,
                    extra={k: v for k, v in (extra or {}).items() if v is not None},
                    created_at=_utcnow(),
                )
            )
            row.updated_at = _utcnow()
            db.commit()
            return row.id

    def history(self, user_id: str, conversation_id: str, limit: int = 10) -> list[dict[str, str]]:
        """Recent turns as plain role/content pairs for the reasoner's context."""
        with self.session_factory() as db:
            owns = db.scalar(
                select(ConversationRow.id).where(
                    ConversationRow.id == conversation_id, ConversationRow.user_id == user_id
                )
            )
            if not owns:
                return []
            rows = db.scalars(
                select(MessageRow)
                .where(MessageRow.conversation_id == conversation_id)
                .order_by(MessageRow.id.desc())
                .limit(limit)
            ).all()
            return [{"role": m.role, "content": m.content} for m in reversed(rows)]

    # -- helpers ------------------------------------------------------------ #

    def _trim(self, db, user_id: str) -> None:
        """Keep the thread list bounded; oldest go first."""
        ids = db.scalars(
            select(ConversationRow.id)
            .where(ConversationRow.user_id == user_id)
            .order_by(ConversationRow.updated_at.desc())
            .offset(MAX_CONVERSATIONS)
        ).all()
        if ids:
            db.execute(delete(MessageRow).where(MessageRow.conversation_id.in_(ids)))
            db.execute(delete(ConversationRow).where(ConversationRow.id.in_(ids)))
            db.commit()

    @staticmethod
    def _summary(row: ConversationRow, message_count: int) -> dict[str, Any]:
        return {
            "id": row.id,
            "title": row.title,
            "messages": message_count,
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
        }
