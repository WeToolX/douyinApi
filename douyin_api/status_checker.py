from __future__ import annotations

from typing import Any

import httpx

from .account_store import AccountStore
from .sec_uid_resolver import SecUidResolver


class LoginStatusChecker:
    def __init__(self, store: AccountStore, client: httpx.AsyncClient | None = None):
        self.store = store
        self.client = client or httpx.AsyncClient(timeout=20.0, follow_redirects=True)

    async def check_account(self, account_id: str) -> dict[str, Any]:
        account = self.store.get_account(account_id)
        if not account:
            raise KeyError(account_id)
        storage_state = self.store.read_storage_state(account)
        cookie_header = SecUidResolver._cookie_header(storage_state)
        response = await self.client.get(
            "https://creator.douyin.com/creator-micro/home",
            headers={
                "User-Agent": "Mozilla/5.0 AppleWebKit/537.36 Chrome/124 Safari/537.36",
                "Cookie": cookie_header,
            },
        )
        final_url = str(response.url)
        if response.status_code in (401, 403) or "login" in final_url.lower() or "passport" in final_url.lower():
            login_status = "session_expired"
        elif response.status_code >= 500:
            login_status = "error"
        else:
            login_status = "logged_in"
        self.store.update_login_status(account_id, login_status)
        return {"account_id": account_id, "login_status": login_status, "final_url": final_url}

    async def check_many(self, account_ids: list[str] | None = None) -> dict[str, Any]:
        accounts = self.store.list_accounts()
        if account_ids:
            allowed = set(account_ids)
            accounts = [account for account in accounts if account["account_id"] in allowed]
        details = []
        for account in accounts:
            try:
                details.append(await self.check_account(account["account_id"]))
            except Exception as exc:
                self.store.update_login_status(account["account_id"], "error")
                details.append({"account_id": account["account_id"], "login_status": "error", "error": str(exc)})
        return {
            "success": True,
            "checked": len(details),
            "logged_in": sum(1 for item in details if item.get("login_status") == "logged_in"),
            "session_expired": sum(1 for item in details if item.get("login_status") == "session_expired"),
            "errors": sum(1 for item in details if item.get("login_status") == "error"),
            "details": details,
        }
