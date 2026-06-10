from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AccountStore:
    """SQLite 账号库，cookie/storage_state 单独落盘。"""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.cookies_dir = self.data_dir / "cookies"
        self.db_path = self.data_dir / "douyin_accounts.db"
        self.cookies_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    account_id TEXT PRIMARY KEY,
                    platform TEXT NOT NULL DEFAULT 'douyin',
                    name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'valid',
                    cookie_file TEXT NOT NULL,
                    last_checked TEXT,
                    avatar TEXT,
                    original_name TEXT,
                    note TEXT,
                    user_id TEXT,
                    uid TEXT,
                    douyin_id TEXT,
                    sec_uid TEXT,
                    employee_no TEXT,
                    totp TEXT,
                    login_status TEXT NOT NULL DEFAULT 'unknown',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_used_at TEXT
                )
                """
            )
            self._ensure_account_columns(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sec_uid_cache (
                    query_type TEXT NOT NULL,
                    query_value TEXT NOT NULL,
                    sec_uid TEXT NOT NULL,
                    source_account_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (query_type, query_value)
                )
                """
	            )

    def _ensure_account_columns(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute("PRAGMA table_info(accounts)").fetchall()
        existing = {row["name"] for row in rows}
        for name in ("employee_no", "totp"):
            if name not in existing:
                conn.execute(f"ALTER TABLE accounts ADD COLUMN {name} TEXT")

    def _row_to_account(self, row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)

    def _build_account_identity(self, user_info: dict[str, Any], note: str | None) -> dict[str, str | None]:
        uid = self._first_text(user_info, "uid", "uid_str", "user_uid", "uidShort")
        douyin_id = self._first_text(user_info, "douyin_id", "douyinId", "unique_id", "uniqueId", "short_id", "shortId")
        user_id = self._first_text(user_info, "user_id", "userId", "id") or douyin_id or uid
        sec_uid = self._first_text(user_info, "sec_uid", "secUid", "sec_user_id", "secUserId")
        employee_no = self._first_text(user_info, "employee_no", "employeeNo", "employee_id", "employeeId", "staff_id", "staffId")
        totp = self._first_text(user_info, "totp", "totp_code", "totpCode", "otp", "otp_code", "otpCode")
        account_id = user_id or uid or douyin_id or f"account_{uuid.uuid4().hex[:12]}"
        name = self._first_text(user_info, "name", "nickname", "screen_name") or note or account_id
        avatar = self._first_text(user_info, "avatar", "avatar_url", "avatarUrl")
        return {
            "account_id": str(account_id),
            "user_id": str(user_id) if user_id else None,
            "uid": str(uid) if uid else None,
            "douyin_id": str(douyin_id) if douyin_id else str(user_id) if user_id else None,
            "sec_uid": str(sec_uid) if sec_uid else None,
            "employee_no": str(employee_no) if employee_no else None,
            "totp": str(totp) if totp else None,
            "name": str(name),
            "avatar": str(avatar) if avatar else None,
        }

    @staticmethod
    def _first_text(data: dict[str, Any], *keys: str) -> str | None:
        for key in keys:
            value = data.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        extra = data.get("extra")
        if isinstance(extra, dict):
            for key in keys:
                value = extra.get(key)
                if value is None:
                    continue
                text = str(value).strip()
                if text:
                    return text
        return None

    def save_login_state(
        self,
        note: str | None,
        storage_state: dict[str, Any],
        user_info: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        user_info = dict(user_info or {})
        identity = self._build_account_identity(user_info, note)
        account_id = identity["account_id"]
        now = utc_now()
        cookie_file = f"douyin_{account_id}.json"
        payload = dict(storage_state or {})
        payload["user_info"] = {**user_info, **{k: v for k, v in identity.items() if v is not None}}
        (self.cookies_dir / cookie_file).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO accounts (
                    account_id, platform, name, status, cookie_file, last_checked,
                    avatar, original_name, note, user_id, uid, douyin_id, sec_uid,
                    employee_no, totp, login_status, created_at, updated_at
                )
                VALUES (?, 'douyin', ?, 'valid', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'logged_in', ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    name=excluded.name,
                    status='valid',
                    cookie_file=excluded.cookie_file,
                    last_checked=excluded.last_checked,
                    avatar=excluded.avatar,
                    original_name=excluded.original_name,
                    note=excluded.note,
                    user_id=excluded.user_id,
                    uid=excluded.uid,
                    douyin_id=excluded.douyin_id,
                    sec_uid=excluded.sec_uid,
                    employee_no=excluded.employee_no,
                    totp=excluded.totp,
                    login_status='logged_in',
                    updated_at=excluded.updated_at
                """,
                (
                    account_id,
                    identity["name"],
                    cookie_file,
                    now,
                    identity["avatar"],
                    identity["name"],
                    note,
                    identity["user_id"],
                    identity["uid"],
	                    identity["douyin_id"],
	                    identity["sec_uid"],
	                    identity["employee_no"],
	                    identity["totp"],
	                    now,
	                    now,
	                ),
            )
        account = self.get_account(account_id)
        if identity["sec_uid"]:
            if identity["uid"]:
                self.upsert_sec_uid_cache("uid", identity["uid"], identity["sec_uid"], account_id)
            if identity["douyin_id"]:
                self.upsert_sec_uid_cache("douyin_id", identity["douyin_id"], identity["sec_uid"], account_id)
        return account

    def list_accounts(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM accounts ORDER BY updated_at DESC").fetchall()
        return [self._row_to_account(row) for row in rows]

    def get_account(self, account_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM accounts WHERE account_id = ?", (account_id,)).fetchone()
        return self._row_to_account(row) if row else None

    def get_available_account(self) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM accounts
                WHERE platform = 'douyin'
                  AND status = 'valid'
                  AND login_status NOT IN ('session_expired', 'error')
                ORDER BY COALESCE(last_used_at, ''), updated_at DESC
                LIMIT 1
                """
            ).fetchone()
            if not row:
                return None
            account = self._row_to_account(row)
            if not (self.cookies_dir / account["cookie_file"]).exists():
                conn.execute(
                    "UPDATE accounts SET status = 'file_missing', login_status = 'error', updated_at = ? WHERE account_id = ?",
                    (utc_now(), account["account_id"]),
                )
                return self.get_available_account()
            conn.execute("UPDATE accounts SET last_used_at = ? WHERE account_id = ?", (utc_now(), account["account_id"]))
            return account

    def read_storage_state(self, account: dict[str, Any]) -> dict[str, Any]:
        path = self.cookies_dir / account["cookie_file"]
        return json.loads(path.read_text(encoding="utf-8"))

    def update_login_status(self, account_id: str, login_status: str, status: str | None = None) -> None:
        status_value = status or ("expired" if login_status == "session_expired" else None)
        with self._lock, self._connect() as conn:
            if status_value:
                conn.execute(
                    "UPDATE accounts SET login_status = ?, status = ?, last_checked = ?, updated_at = ? WHERE account_id = ?",
                    (login_status, status_value, utc_now(), utc_now(), account_id),
                )
            else:
                conn.execute(
                    "UPDATE accounts SET login_status = ?, last_checked = ?, updated_at = ? WHERE account_id = ?",
                    (login_status, utc_now(), utc_now(), account_id),
                )

    def update_account_note(self, account_id: str, note: str | None, name: str | None = None) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            if name:
                conn.execute(
                    "UPDATE accounts SET note = ?, name = ?, updated_at = ? WHERE account_id = ?",
                    (note, name, utc_now(), account_id),
                )
            else:
                conn.execute("UPDATE accounts SET note = ?, updated_at = ? WHERE account_id = ?", (note, utc_now(), account_id))
        account = self.get_account(account_id)
        if not account:
            raise KeyError(account_id)
        return account

    def delete_account(self, account_id: str) -> bool:
        account = self.get_account(account_id)
        with self._lock, self._connect() as conn:
            cursor = conn.execute("DELETE FROM accounts WHERE account_id = ?", (account_id,))
        if account:
            try:
                (self.cookies_dir / account["cookie_file"]).unlink()
            except FileNotFoundError:
                pass
        return cursor.rowcount > 0

    def delete_invalid_accounts(self) -> int:
        accounts = [a for a in self.list_accounts() if a["status"] != "valid" or a["login_status"] in ("session_expired", "error")]
        for account in accounts:
            self.delete_account(account["account_id"])
        return len(accounts)

    def get_sec_uid_cache(self, query_type: str, query_value: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sec_uid_cache WHERE query_type = ? AND query_value = ?",
                (query_type, query_value),
            ).fetchone()
        return self._row_to_account(row) if row else None

    def get_sec_uid_cache_by_sec_uid(
        self,
        query_type: str,
        sec_uid: str,
        exclude_query_value: str | None = None,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            if exclude_query_value:
                row = conn.execute(
                    """
                    SELECT * FROM sec_uid_cache
                    WHERE query_type = ? AND sec_uid = ? AND query_value != ?
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (query_type, sec_uid, exclude_query_value),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT * FROM sec_uid_cache
                    WHERE query_type = ? AND sec_uid = ?
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (query_type, sec_uid),
                ).fetchone()
        return self._row_to_account(row) if row else None

    def upsert_sec_uid_cache(self, query_type: str, query_value: str, sec_uid: str, source_account_id: str | None) -> None:
        now = utc_now()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sec_uid_cache (query_type, query_value, sec_uid, source_account_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(query_type, query_value) DO UPDATE SET
                    sec_uid=excluded.sec_uid,
                    source_account_id=excluded.source_account_id,
                    updated_at=excluded.updated_at
                """,
                (query_type, query_value, sec_uid, source_account_id, now, now),
            )
