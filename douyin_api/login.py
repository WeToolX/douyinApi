from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import uuid
from dataclasses import dataclass
from typing import Any


@dataclass
class LoginSession:
    session_id: str
    page: Any
    context: Any
    browser: Any
    playwright: Any
    note: str | None


class DouyinQRCodeLogin:
    """抖音创作者中心扫码登录。未安装 Playwright 时接口返回明确错误。"""

    login_url = "https://creator.douyin.com/creator-micro/login?enter_from=qr"
    field_aliases = {
        "user_id": ("user_id", "userid", "userId", "userID", "id", "biz_user_id", "bizUserId", "login_user_id", "loginUserId"),
        "uid": ("uid", "uid_str", "uidStr", "user_uid", "userUid"),
        "douyin_id": ("douyin_id", "douyinId", "unique_id", "uniqueId", "short_id", "shortId"),
        "name": ("name", "nickname", "screen_name", "screenName", "nickName"),
        "avatar": ("avatar", "avatar_url", "avatarUrl", "avatar_uri", "avatarUri"),
        "sec_uid": ("sec_uid", "secUid", "sec_user_id", "secUserId"),
        "employee_no": (
            "employee_no",
            "employeeNo",
            "employee_id",
            "employeeId",
            "employee_code",
            "employeeCode",
            "staff_id",
            "staffId",
            "staff_no",
            "staffNo",
            "job_number",
            "jobNumber",
            "work_no",
            "workNo",
            "员工号",
        ),
        "totp": ("totp", "totp_code", "totpCode", "otp", "otp_code", "otpCode", "mfa_totp", "mfaTotp"),
    }

    def __init__(self):
        self.sessions: dict[str, LoginSession] = {}
        self._lock = asyncio.Lock()

    async def generate(self, note: str | None = None, headless: bool = True) -> dict[str, Any]:
        try:
            from playwright.async_api import async_playwright
        except Exception as exc:
            raise RuntimeError("Playwright 未安装，无法生成抖音扫码二维码；请安装 playwright 并执行 playwright install chromium") from exc

        session_id = str(uuid.uuid4())
        pw = await async_playwright().start()
        browser = None
        try:
            browser = await pw.chromium.launch(headless=headless, args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
            context = await browser.new_context(viewport={"width": 1280, "height": 800})
            page = await context.new_page()
            await page.goto(self.login_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(1500)
            qr_image = await self._extract_qr_image(page)
            async with self._lock:
                self.sessions[session_id] = LoginSession(session_id, page, context, browser, pw, note)
            return {"session_id": session_id, "qr_image": qr_image, "expires_in": 300}
        except Exception:
            with contextlib.suppress(Exception):
                if browser:
                    await browser.close()
            with contextlib.suppress(Exception):
                await pw.stop()
            raise

    async def poll(self, session_id: str) -> dict[str, Any]:
        async with self._lock:
            session = self.sessions.get(session_id)
        if not session:
            return {"status": "expired", "message": "二维码会话不存在或已结束"}

        page = session.page
        context = session.context
        try:
            cookies = await context.cookies()
            cookie_names = {c.get("name") for c in cookies}
            has_auth_cookie = bool(cookie_names & {"sessionid", "sessionid_ss", "sid_guard", "sid_tt", "passport_auth_id", "odin_tt"})
            on_login = "login" in (page.url or "").lower() or "passport" in (page.url or "").lower()
            user_info = await self._extract_user_info(page)
            if not on_login and (has_auth_cookie or user_info.get("user_id") or user_info.get("douyin_id")):
                storage_state = await context.storage_state()
                user_info = self._merge_user_info(
                    user_info,
                    self._extract_storage_user_info(storage_state),
                    self._extract_cookie_user_info(storage_state.get("cookies") or cookies),
                )
                await self.cleanup(session_id)
                return {
                    "status": "confirmed",
                    "message": "登录成功",
                    "storage_state": storage_state,
                    "user_info": user_info,
                }
            return {"status": "waiting", "message": "等待手机扫码确认"}
        except Exception as exc:
            await self.cleanup(session_id)
            return {"status": "failed", "message": str(exc)}

    async def cleanup(self, session_id: str) -> None:
        async with self._lock:
            session = self.sessions.pop(session_id, None)
        if not session:
            return
        with contextlib.suppress(Exception):
            await session.browser.close()
        with contextlib.suppress(Exception):
            await session.playwright.stop()

    async def _extract_qr_image(self, page: Any) -> str:
        selectors = [
            "xpath=//div[@id='animate_qrcode_container']//img[contains(@class,'qrcode_img')]",
            "img[class*='qrcode']",
            "img[src*='qrcode']",
            ".qrcode img",
        ]
        for selector in selectors:
            with contextlib.suppress(Exception):
                node = await page.wait_for_selector(selector, timeout=5000)
                if node:
                    src = await node.get_attribute("src")
                    if src:
                        return src
        shot = await page.screenshot(full_page=False)
        return f"data:image/png;base64,{base64.b64encode(shot).decode('utf-8')}"

    async def _extract_user_info(self, page: Any) -> dict[str, Any]:
        info: dict[str, Any] = {}
        with contextlib.suppress(Exception):
            js_info = await page.evaluate(
                """() => {
	                  const out = {};
	                  out.routerData = window._ROUTER_DATA || null;
	                  out.initialState = window.__INITIAL_STATE__ || null;
	                  out.nextData = window.__NEXT_DATA__ || null;
	                  out.localStorage = {};
	                  out.sessionStorage = {};
	                  for (let i = 0; i < window.localStorage.length; i++) {
	                    const key = window.localStorage.key(i);
	                    out.localStorage[key] = window.localStorage.getItem(key);
	                  }
	                  for (let i = 0; i < window.sessionStorage.length; i++) {
	                    const key = window.sessionStorage.key(i);
	                    out.sessionStorage[key] = window.sessionStorage.getItem(key);
	                  }
	                  return out;
	                }"""
            )
            if isinstance(js_info, dict):
                info.update(self._extract_fields(js_info))
        with contextlib.suppress(Exception):
            body_text = await page.inner_text("body")
            import re

            match = re.search(r"(抖音号|抖音ID|抖音id)[:：]?\s*([A-Za-z0-9_.-]+)", body_text)
            if match and not info.get("douyin_id"):
                info["douyin_id"] = match.group(2)
            employee_match = re.search(r"(员工号|工号)[:：]?\s*([A-Za-z0-9_.-]+)", body_text)
            if employee_match and not info.get("employee_no"):
                info["employee_no"] = employee_match.group(2)
        return self._merge_user_info(info)

    def _extract_storage_user_info(self, storage_state: dict[str, Any]) -> dict[str, Any]:
        sources: list[Any] = []
        for origin in storage_state.get("origins") or []:
            if not isinstance(origin, dict):
                continue
            for storage_key in ("localStorage", "sessionStorage"):
                for item in origin.get(storage_key) or []:
                    if isinstance(item, dict):
                        sources.append({item.get("name"): item.get("value")})
                        sources.append(item.get("value"))
        return self._extract_fields(sources)

    def _extract_cookie_user_info(self, cookies: list[dict[str, Any]]) -> dict[str, Any]:
        cookie_map = {
            str(cookie.get("name")): cookie.get("value")
            for cookie in cookies
            if isinstance(cookie, dict) and cookie.get("name") and cookie.get("value") is not None
        }
        result = self._extract_fields(cookie_map)
        result.setdefault("extra", {})
        result["extra"]["cookies"] = cookie_map
        return result

    def _merge_user_info(self, *items: dict[str, Any]) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        extra: dict[str, Any] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            normalized = self._extract_fields(item)
            for key in self.field_aliases:
                if not merged.get(key) and normalized.get(key):
                    merged[key] = normalized[key]
            item_extra = item.get("extra")
            if isinstance(item_extra, dict):
                extra.update(item_extra)
            extra.update({k: v for k, v in item.items() if k != "extra"})
        if not merged.get("user_id"):
            merged["user_id"] = merged.get("douyin_id") or merged.get("uid")
        merged["extra"] = extra
        return merged

    def _extract_fields(self, source: Any) -> dict[str, Any]:
        result: dict[str, Any] = {}
        alias_map = {
            self._normalize_key(alias): canonical
            for canonical, aliases in self.field_aliases.items()
            for alias in aliases
        }

        def visit(value: Any, depth: int = 0) -> None:
            if depth > 8 or value is None:
                return
            if isinstance(value, str):
                text = value.strip()
                if text.startswith(("{", "[")):
                    with contextlib.suppress(Exception):
                        visit(json.loads(text), depth + 1)
                return
            if isinstance(value, list):
                for item in value[:200]:
                    visit(item, depth + 1)
                return
            if not isinstance(value, dict):
                return
            for key, child in value.items():
                canonical = alias_map.get(self._normalize_key(str(key)))
                if canonical and not result.get(canonical):
                    text = self._scalar_text(child)
                    if text:
                        result[canonical] = text
                visit(child, depth + 1)

        visit(source)
        return result

    @staticmethod
    def _normalize_key(value: str) -> str:
        return "".join(ch for ch in value.lower() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")

    @staticmethod
    def _scalar_text(value: Any) -> str | None:
        if isinstance(value, (str, int, float)):
            text = str(value).strip()
            return text or None
        return None
