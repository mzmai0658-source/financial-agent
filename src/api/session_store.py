from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional
from uuid import uuid4


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class SessionMessageRecord:
    role: str
    content: str
    ts: str
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class ChatSessionRecord:
    session_id: str
    messages: List[SessionMessageRecord] = field(default_factory=list)
    latest_context: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    next_chart_index: int = 1


class SessionStore:
    MAX_SESSIONS = 100

    def __init__(self, persist_path: Optional[Path | str] = None) -> None:
        self._lock = RLock()
        self._sessions: Dict[str, ChatSessionRecord] = {}
        self._session_locks: Dict[str, asyncio.Lock] = {}
        default_path = Path(__file__).resolve().parents[2] / "data" / "runtime" / "chat_sessions.json"
        self._persist_path = Path(persist_path or os.getenv("CHAT_SESSION_STORE_PATH") or default_path)
        self._load()

    def _session_to_dict(self, session: ChatSessionRecord) -> Dict[str, Any]:
        return {
            "session_id": session.session_id,
            "messages": [
                {
                    "role": message.role,
                    "content": message.content,
                    "ts": message.ts,
                    "metadata": message.metadata,
                }
                for message in session.messages
            ],
            "latest_context": session.latest_context,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "next_chart_index": session.next_chart_index,
        }

    def _session_from_dict(self, payload: Dict[str, Any]) -> Optional[ChatSessionRecord]:
        session_id = str(payload.get("session_id") or "").strip()
        if not session_id:
            return None
        messages = []
        for item in payload.get("messages") or []:
            if not isinstance(item, dict):
                continue
            messages.append(
                SessionMessageRecord(
                    role=str(item.get("role") or ""),
                    content=str(item.get("content") or ""),
                    ts=str(item.get("ts") or _now_iso()),
                    metadata=item.get("metadata") if isinstance(item.get("metadata"), dict) else None,
                )
            )
        return ChatSessionRecord(
            session_id=session_id,
            messages=messages,
            latest_context=dict(payload.get("latest_context") or {}),
            created_at=str(payload.get("created_at") or _now_iso()),
            updated_at=str(payload.get("updated_at") or _now_iso()),
            next_chart_index=int(payload.get("next_chart_index") or 1),
        )

    def _load(self) -> None:
        try:
            if not self._persist_path.exists():
                return
            payload = json.loads(self._persist_path.read_text(encoding="utf-8"))
            sessions = payload.get("sessions") if isinstance(payload, dict) else []
            if not isinstance(sessions, list):
                return
            for item in sessions:
                if not isinstance(item, dict):
                    continue
                session = self._session_from_dict(item)
                if session is None:
                    continue
                self._sessions[session.session_id] = session
                self._session_locks[session.session_id] = asyncio.Lock()
        except Exception:
            self._sessions = {}
            self._session_locks = {}

    def _prune_locked(self) -> None:
        """会话数量超限时按更新时间淘汰最旧的，防止持久化文件无限膨胀。"""
        if len(self._sessions) <= self.MAX_SESSIONS:
            return
        ordered = sorted(self._sessions.values(), key=lambda s: s.updated_at, reverse=True)
        for stale in ordered[self.MAX_SESSIONS:]:
            self._sessions.pop(stale.session_id, None)
            self._session_locks.pop(stale.session_id, None)

    def _save_locked(self) -> None:
        self._prune_locked()
        self._persist_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "sessions": [self._session_to_dict(session) for session in self._sessions.values()],
        }
        tmp_path = self._persist_path.with_suffix(self._persist_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(self._persist_path)

    def create_session(self) -> ChatSessionRecord:
        with self._lock:
            session_id = uuid4().hex
            session = ChatSessionRecord(session_id=session_id)
            self._sessions[session_id] = session
            self._session_locks[session_id] = asyncio.Lock()
            self._save_locked()
            return session

    def get_session(self, session_id: str) -> Optional[ChatSessionRecord]:
        with self._lock:
            return self._sessions.get(session_id)

    def session_ids(self) -> List[str]:
        with self._lock:
            return list(self._sessions.keys())

    def delete_session(self, session_id: str) -> bool:
        with self._lock:
            deleted = self._sessions.pop(session_id, None) is not None
            if deleted:
                self._session_locks.pop(session_id, None)
                self._save_locked()
            return deleted

    def get_session_lock(self, session_id: str) -> asyncio.Lock:
        with self._lock:
            return self._session_locks.setdefault(session_id, asyncio.Lock())

    def append_turn(
        self,
        session_id: str,
        user_content: str,
        assistant_content: str,
        assistant_metadata: Dict[str, Any],
        latest_context: Dict[str, Any],
        image_count: int,
    ) -> ChatSessionRecord:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                # 会话可能在本轮执行期间被淘汰，重建以保住本轮结果
                session = ChatSessionRecord(session_id=session_id)
                self._sessions[session_id] = session
                self._session_locks.setdefault(session_id, asyncio.Lock())
            now = _now_iso()
            session.messages.append(SessionMessageRecord(role="user", content=user_content, ts=now))
            session.messages.append(
                SessionMessageRecord(
                    role="assistant",
                    content=assistant_content,
                    ts=_now_iso(),
                    metadata=assistant_metadata,
                )
            )
            session.latest_context = dict(latest_context or {})
            session.updated_at = _now_iso()
            session.next_chart_index += max(0, int(image_count or 0))
            self._save_locked()
            return session

    def reset_session(self, session_id: str) -> Optional[ChatSessionRecord]:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            session.messages = []
            session.latest_context = {}
            session.updated_at = _now_iso()
            session.next_chart_index = 1
            self._save_locked()
            return session
