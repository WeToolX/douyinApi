from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from .account_store import AccountStore
from .guest_cookie import HttpGuestCookieProvider
from .xbogus import XBogus


class NoAvailableAccountError(RuntimeError):
    pass


class SecUidNotFoundError(RuntimeError):
    pass


class DouyinVerificationRequiredError(RuntimeError):
    pass


@dataclass(frozen=True)
class SecUidResult:
    query_type: str
    query_value: str
    sec_uid: str
    source_account_id: str | None
    from_cache: bool = False


@dataclass(frozen=True)
class ResolvedUser:
    uid: str | None
    sec_uid: str
    unique_id: str | None
    short_id: str | None
    nickname: str | None
    source_account_id: str | None
    from_cache: bool = False


class SecUidResolver:
    """使用账号库中的抖音登录 cookie 解析 uid/抖音号到 sec_uid。"""

    def __init__(
        self,
        store: AccountStore,
        client: httpx.AsyncClient | None = None,
        guest_cookie_provider: HttpGuestCookieProvider | None = None,
    ):
        self.store = store
        self.client = client or httpx.AsyncClient(timeout=20.0, follow_redirects=True)
        self.guest_cookie_provider = guest_cookie_provider or HttpGuestCookieProvider()
        self._guest_headers_cache: tuple[float, dict[str, str]] | None = None

    async def resolve_by_uid(self, uid: str) -> SecUidResult:
        uid = uid.strip()
        if self._looks_like_sec_uid(uid):
            return SecUidResult("uid", uid, uid, None, from_cache=True)
        cached = self.store.get_sec_uid_cache("uid", uid)
        if cached:
            return SecUidResult("uid", uid, cached["sec_uid"], cached["source_account_id"], from_cache=True)

        account, headers = self._prepare_request_context()
        urls = [
            f"https://www.iesdouyin.com/web/api/v2/user/info/?uid={quote(uid)}",
            f"https://www.douyin.com/aweme/v1/web/im/user/info/?uid={quote(uid)}",
        ]
        for url in urls:
            data = await self._get_json(url, headers)
            sec_uid = self._find_sec_uid(data)
            if sec_uid:
                self.store.upsert_sec_uid_cache("uid", uid, sec_uid, account["account_id"])
                return SecUidResult("uid", uid, sec_uid, account["account_id"])
        raise SecUidNotFoundError(f"未能通过 uid 解析 sec_uid: {uid}")

    async def resolve_user_sec_uid(self, keyword: str, require_uid: bool = False) -> ResolvedUser:
        """按抖音号或 uid 查询目标用户 sec_uid，不查询当前登录账号自己。"""
        keyword = keyword.strip()
        if not keyword:
            raise SecUidNotFoundError("keyword 不能为空")
        cache_type = self._keyword_cache_type(keyword, None)
        cached = self.store.get_sec_uid_cache(cache_type, keyword)
        if not cached and keyword.isdigit() and not require_uid:
            cache_type = "uid"
            cached = self.store.get_sec_uid_cache(cache_type, keyword)
        if cached:
            cached_user = self._resolved_user_from_cache(keyword, cached, require_uid)
            if cached_user:
                return cached_user

        account, headers = self._prepare_request_context()
        urls = self._resolve_keyword_urls(keyword)
        last_data = None
        last_block_reason = None
        for url in urls:
            data = await self._get_json(url, headers)
            last_data = data
            block_reason = self._verification_block_reason(data)
            if block_reason:
                last_block_reason = block_reason
                continue
            user = self._find_matching_user(data, keyword) or self._find_first_user(data)
            resolved = self._resolved_user_from_data(user, account["account_id"]) if user else None
            if resolved:
                if require_uid and not resolved.uid:
                    continue
                if resolved.uid:
                    self.store.upsert_sec_uid_cache("uid", resolved.uid, resolved.sec_uid, account["account_id"])
                if resolved.unique_id:
                    self.store.upsert_sec_uid_cache("douyin_id", resolved.unique_id, resolved.sec_uid, account["account_id"])
                if resolved.short_id:
                    self.store.upsert_sec_uid_cache("douyin_id", resolved.short_id, resolved.sec_uid, account["account_id"])
                keyword_cache_type = self._keyword_cache_type(keyword, resolved)
                self.store.upsert_sec_uid_cache(keyword_cache_type, keyword, resolved.sec_uid, account["account_id"])
                return resolved

        sec_uid = self._find_sec_uid(last_data)
        if sec_uid:
            if require_uid:
                raise SecUidNotFoundError(f"已解析到 sec_uid，但未解析到真实 uid: {keyword}")
            self.store.upsert_sec_uid_cache(cache_type, keyword, sec_uid, account["account_id"])
            return ResolvedUser(
                uid=None,
                sec_uid=sec_uid,
                unique_id=None if keyword.isdigit() else keyword,
                short_id=None,
                nickname=None,
                source_account_id=account["account_id"],
            )
        if last_block_reason:
            self.store.update_login_status(account["account_id"], "verify_required", status="error")
            raise DouyinVerificationRequiredError(
                f"抖音要求账号完成验证后才能搜索目标用户: {last_block_reason}"
            )
        raise SecUidNotFoundError(f"未能通过 keyword 解析目标用户 sec_uid: {keyword}")

    async def handler_user_profile(self, sec_user_id: str) -> Any:
        """兼容 Evil0ctal: /handler_user_profile?sec_user_id=..."""
        sec_user_id = sec_user_id.strip()
        if not sec_user_id:
            raise SecUidNotFoundError("sec_user_id 不能为空")
        _, headers = self._prepare_request_context()
        url = "https://www.douyin.com/aweme/v1/web/user/profile/other/"
        response = await self.client.get(url, params=self._user_detail_params(sec_user_id), headers=headers)
        response.raise_for_status()
        return response.json()

    async def guest_handler_user_profile(self, sec_user_id: str) -> Any:
        """使用未登录临时 cookie 查询完整用户资料。"""
        sec_user_id = sec_user_id.strip()
        if not sec_user_id:
            raise SecUidNotFoundError("sec_user_id 不能为空")
        headers = await self._build_guest_headers(sec_user_id)
        return await self._fetch_user_profile(sec_user_id, headers)

    async def fetch_user_stats_preview(self, sec_user_id: str, limit: int = 2) -> dict[str, Any]:
        """用账号库 cookie 查询指定用户统计，并返回喜欢/收藏前几条作品 ID。"""
        sec_user_id = sec_user_id.strip()
        if not sec_user_id:
            raise SecUidNotFoundError("sec_user_id 不能为空")
        limit = max(1, min(limit, 20))
        account, headers = self._prepare_request_context()

        profile = await self._fetch_user_profile(sec_user_id, headers)
        user = profile.get("user") if isinstance(profile, dict) and isinstance(profile.get("user"), dict) else {}
        like_data = await self._fetch_target_like_videos(sec_user_id, headers, limit)
        collection_data = await self._fetch_login_account_collection(headers, limit)

        return {
            "sec_uid": self._first_text(user, "sec_uid", "sec_user_id") or sec_user_id,
            "source_account_id": account["account_id"],
            "stats": {
                "following_count": self._int_value(user.get("following_count")),
                "follower_count": self._int_value(user.get("follower_count")),
                "favoriting_count": self._int_value(user.get("favoriting_count")),
                "total_favorited": self._int_value(user.get("total_favorited")),
            },
            "user": {
                "uid": self._first_text(user, "uid", "uid_str", "user_id", "userId"),
                "sec_uid": self._first_text(user, "sec_uid", "sec_user_id") or sec_user_id,
                "unique_id": self._first_text(user, "unique_id", "uniqueId", "douyin_id", "douyinId"),
                "short_id": self._first_text(user, "short_id", "shortId"),
                "nickname": self._first_text(user, "nickname", "name"),
            },
            "like_aweme_ids": self._first_aweme_ids(like_data, limit),
            "like_source": "target_sec_uid",
            "collection_aweme_ids": self._first_aweme_ids(collection_data, limit),
            "collection_source": "login_account",
            "collection_source_account_id": account["account_id"],
        }

    async def fetch_guest_user_stats_preview(self, sec_user_id: str, limit: int = 2) -> dict[str, Any]:
        """使用未登录临时 cookie 查询资料统计、喜欢预览和收藏探测结果。"""
        sec_user_id = sec_user_id.strip()
        if not sec_user_id:
            raise SecUidNotFoundError("sec_user_id 不能为空")
        limit = max(1, min(limit, 20))
        headers = await self._build_guest_headers(sec_user_id)

        profile = await self._fetch_user_profile(sec_user_id, headers)
        user = profile.get("user") if isinstance(profile, dict) and isinstance(profile.get("user"), dict) else {}
        like_data = await self._fetch_target_like_videos(sec_user_id, headers, limit)
        collection_data = await self._fetch_login_account_collection(headers, limit)

        return {
            "cookie_source": "guest",
            "source_account_id": None,
            "sec_uid": self._first_text(user, "sec_uid", "sec_user_id") or sec_user_id,
            "profile": profile,
            "stats": {
                "following_count": self._int_value(user.get("following_count")),
                "follower_count": self._int_value(user.get("follower_count")),
                "favoriting_count": self._int_value(user.get("favoriting_count")),
                "total_favorited": self._int_value(user.get("total_favorited")),
            },
            "like_aweme_ids": self._first_aweme_ids(like_data, limit),
            "like_status_code": like_data.get("status_code") if isinstance(like_data, dict) else None,
            "like_source": "target_sec_uid",
            "collection_aweme_ids": self._first_aweme_ids(collection_data, limit),
            "collection_status_code": collection_data.get("status_code") if isinstance(collection_data, dict) else None,
            "collection_source": "guest_cookie",
        }

    async def build_http_guest_cookie(self) -> dict[str, Any]:
        """生成不依赖浏览器的抖音未登录临时 Cookie。"""
        cookie = await self.guest_cookie_provider.build(force=True)
        return {
            "source": cookie.source,
            "cookie": cookie.cookie,
            "cookie_names": cookie.cookie_names,
            "user_agent": cookie.user_agent,
            "expires_in": cookie.expires_in,
        }

    async def resolve_by_douyin_id(self, douyin_id: str) -> SecUidResult:
        douyin_id = douyin_id.strip()
        if self._looks_like_sec_uid(douyin_id):
            return SecUidResult("douyin_id", douyin_id, douyin_id, None, from_cache=True)
        cached = self.store.get_sec_uid_cache("douyin_id", douyin_id)
        if cached:
            return SecUidResult("douyin_id", douyin_id, cached["sec_uid"], cached["source_account_id"], from_cache=True)

        account, headers = self._prepare_request_context()
        url = f"https://www.douyin.com/aweme/v1/web/discover/search/?keyword={quote(douyin_id)}&type=1&count=20"
        data = await self._get_json(url, headers)
        sec_uid = self._find_matching_user_sec_uid(data, douyin_id) or self._find_sec_uid(data)
        if not sec_uid:
            raise SecUidNotFoundError(f"未能通过抖音号解析 sec_uid: {douyin_id}")
        self.store.upsert_sec_uid_cache("douyin_id", douyin_id, sec_uid, account["account_id"])
        return SecUidResult("douyin_id", douyin_id, sec_uid, account["account_id"])

    def _prepare_request_context(self) -> tuple[dict[str, Any], dict[str, str]]:
        account = self.store.get_available_account()
        if not account:
            raise NoAvailableAccountError("没有可用的抖音登录账号，请先扫码登录至少一个账号")
        storage_state = self.store.read_storage_state(account)
        cookie_header = self._cookie_header(storage_state)
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.douyin.com/",
            "Accept": "application/json, text/plain, */*",
        }
        if cookie_header:
            headers["Cookie"] = cookie_header
        return account, headers

    async def _build_guest_headers(self, sec_user_id: str) -> dict[str, str]:
        now = time.time()
        if self._guest_headers_cache and now - self._guest_headers_cache[0] < 600:
            return dict(self._guest_headers_cache[1])

        guest_cookie = await self.guest_cookie_provider.build()
        headers = {
            "User-Agent": guest_cookie.user_agent,
            "Referer": "https://www.douyin.com/",
            "Accept": "application/json, text/plain, */*",
            "Cookie": guest_cookie.cookie,
        }
        self._guest_headers_cache = (now, dict(headers))
        return headers

    def _resolve_keyword_urls(self, keyword: str) -> list[str]:
        common = self._douyin_web_params()
        urls = [
            self._with_params(
                "https://www.douyin.com/aweme/v1/web/discover/search/",
                {**common, "keyword": keyword, "type": 1, "count": 20, "search_channel": "aweme_user_web"},
            ),
            self._with_params(
                "https://www.douyin.com/aweme/v1/web/general/search/single/",
                {**common, "keyword": keyword, "search_channel": "aweme_user_web", "count": 20},
            ),
        ]
        if keyword.isdigit():
            urls.insert(
                0,
                self._with_params("https://www.douyin.com/aweme/v1/web/im/user/info/", {**common, "uid": keyword}),
            )
            urls.insert(
                1,
                self._with_params("https://www.iesdouyin.com/web/api/v2/user/info/", {**common, "uid": keyword}),
            )
        return urls

    @staticmethod
    def _keyword_cache_type(keyword: str, resolved: ResolvedUser | None) -> str:
        """数字输入可能是抖音号，只有确认等于真实 uid 时才按 uid 缓存。"""
        if resolved and resolved.uid and keyword == resolved.uid:
            return "uid"
        return "douyin_id"

    def _resolved_user_from_cache(
        self,
        keyword: str,
        cached: dict[str, Any],
        require_uid: bool,
    ) -> ResolvedUser | None:
        """require_uid 场景下用 douyin_id 缓存反查真实 uid，避免重复触发搜索风控。"""
        query_type = str(cached.get("query_type") or "")
        sec_uid = str(cached.get("sec_uid") or "")
        if not sec_uid:
            return None

        if query_type == "uid":
            return ResolvedUser(
                uid=keyword,
                sec_uid=sec_uid,
                unique_id=None,
                short_id=None,
                nickname=None,
                source_account_id=cached.get("source_account_id"),
                from_cache=True,
            )

        if not require_uid:
            return ResolvedUser(
                uid=None,
                sec_uid=sec_uid,
                unique_id=keyword if query_type == "douyin_id" else None,
                short_id=None,
                nickname=None,
                source_account_id=cached.get("source_account_id"),
                from_cache=True,
            )

        uid_cached = self.store.get_sec_uid_cache_by_sec_uid("uid", sec_uid, exclude_query_value=keyword)
        if not uid_cached:
            uid_cached = self.store.get_sec_uid_cache_by_sec_uid("uid", sec_uid)
        if not uid_cached:
            return None

        uid = str(uid_cached.get("query_value") or "")
        if not uid:
            return None
        return ResolvedUser(
            uid=uid,
            sec_uid=sec_uid,
            unique_id=keyword if query_type == "douyin_id" else None,
            short_id=keyword if query_type == "douyin_id" else None,
            nickname=None,
            source_account_id=cached.get("source_account_id") or uid_cached.get("source_account_id"),
            from_cache=True,
        )

    def _user_detail_params(self, sec_user_id: str) -> dict[str, Any]:
        return {**self._douyin_web_params(), "sec_user_id": sec_user_id}

    async def _fetch_user_profile(self, sec_user_id: str, headers: dict[str, str]) -> Any:
        params = self._user_detail_params(sec_user_id)
        ms_token = self._cookie_value(headers.get("Cookie", ""), "msToken")
        if ms_token:
            params["msToken"] = ms_token
        x_bogus = self._x_bogus(params, headers)
        if x_bogus:
            params["X-Bogus"] = x_bogus
        response = await self.client.get(
            "https://www.douyin.com/aweme/v1/web/user/profile/other/",
            params=params,
            headers=headers,
        )
        response.raise_for_status()
        return response.json()

    async def _fetch_target_like_videos(self, sec_user_id: str, headers: dict[str, str], count: int) -> Any:
        response = await self.client.get(
            "https://www.douyin.com/aweme/v1/web/aweme/favorite/",
            params={**self._douyin_web_params(), "sec_user_id": sec_user_id, "max_cursor": 0, "count": count, "msToken": ""},
            headers=headers,
        )
        response.raise_for_status()
        return response.json()

    async def _fetch_login_account_collection(self, headers: dict[str, str], count: int) -> Any:
        response = await self.client.post(
            "https://www.douyin.com/aweme/v1/web/aweme/listcollection/",
            params={**self._douyin_web_params(), "cursor": 0, "count": count},
            headers=headers,
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _with_params(url: str, params: dict[str, Any]) -> str:
        return f"{url}?{urlencode(params)}"

    @staticmethod
    def _douyin_web_params() -> dict[str, Any]:
        return {
            "device_platform": "webapp",
            "aid": "6383",
            "channel": "channel_pc_web",
            "pc_client_type": 1,
            "version_code": "290100",
            "version_name": "29.1.0",
            "cookie_enabled": "true",
            "screen_width": 1920,
            "screen_height": 1080,
            "browser_language": "zh-CN",
            "browser_platform": "Win32",
            "browser_name": "Chrome",
            "browser_version": "130.0.0.0",
            "browser_online": "true",
            "engine_name": "Blink",
            "engine_version": "130.0.0.0",
            "os_name": "Windows",
            "os_version": "10",
            "cpu_core_num": 12,
            "device_memory": 8,
            "platform": "PC",
            "downlink": "10",
            "effective_type": "4g",
            "from_user_page": "1",
            "locate_query": "false",
            "need_time_list": "1",
            "pc_libra_divert": "Windows",
            "publish_video_strategy_type": "2",
            "round_trip_time": "0",
            "show_live_replay_strategy": "1",
            "time_list_query": "0",
            "whale_cut_token": "",
            "update_version_code": "170400",
        }

    async def _get_json(self, url: str, headers: dict[str, str]) -> Any:
        response = await self.client.get(url, headers=headers)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "json" in content_type:
            return response.json()
        text = response.text.strip()
        try:
            return response.json()
        except Exception:
            return {"raw": text}

    @staticmethod
    def _cookie_header(storage_state: dict[str, Any]) -> str:
        cookies = storage_state.get("cookies") or []
        pairs = []
        for cookie in cookies:
            if not isinstance(cookie, dict):
                continue
            name = cookie.get("name")
            value = cookie.get("value")
            if name and value is not None:
                pairs.append(f"{name}={value}")
        return "; ".join(pairs)

    @staticmethod
    def _cookie_value(cookie_header: str, name: str) -> str | None:
        prefix = f"{name}="
        for part in cookie_header.split(";"):
            part = part.strip()
            if part.startswith(prefix):
                return part[len(prefix):]
        return None

    @staticmethod
    def _x_bogus(params: dict[str, Any], headers: dict[str, str]) -> str | None:
        try:
            return XBogus(headers.get("User-Agent", "")).getXBogus(urlencode(params))[1]
        except Exception:
            return None

    @staticmethod
    def _looks_like_sec_uid(value: str) -> bool:
        return value.startswith(("MS4wLj", "MS4wLjAB", "sec_user_"))

    def _find_matching_user_sec_uid(self, data: Any, douyin_id: str) -> str | None:
        wanted = douyin_id.lower()
        for item in self._walk_dicts(data):
            user = item.get("user_info") if isinstance(item.get("user_info"), dict) else item
            if not isinstance(user, dict):
                continue
            keys = ("unique_id", "uniqueId", "short_id", "shortId", "douyin_id", "nickname")
            values = {str(user.get(key, "")).strip().lower() for key in keys}
            if wanted in values:
                sec_uid = self._find_sec_uid(user)
                if sec_uid:
                    return sec_uid
        return None

    def _find_matching_user(self, data: Any, keyword: str) -> dict[str, Any] | None:
        wanted = keyword.lower()
        for item in self._walk_dicts(data):
            user = item.get("user_info") if isinstance(item.get("user_info"), dict) else item
            if not isinstance(user, dict):
                continue
            values = {
                str(user.get(key, "")).strip().lower()
                for key in ("uid", "uid_str", "unique_id", "uniqueId", "short_id", "shortId", "nickname")
            }
            if wanted in values and self._find_sec_uid(user):
                return user
        return None

    def _find_first_user(self, data: Any) -> dict[str, Any] | None:
        for item in self._walk_dicts(data):
            user = item.get("user_info") if isinstance(item.get("user_info"), dict) else item
            if isinstance(user, dict) and self._find_sec_uid(user):
                return user
        return None

    def _resolved_user_from_data(self, user: dict[str, Any], source_account_id: str | None) -> ResolvedUser | None:
        sec_uid = self._find_sec_uid(user)
        if not sec_uid:
            return None
        return ResolvedUser(
            uid=self._first_text(user, "uid", "uid_str", "user_id", "userId"),
            sec_uid=sec_uid,
            unique_id=self._first_text(user, "unique_id", "uniqueId", "douyin_id", "douyinId"),
            short_id=self._first_text(user, "short_id", "shortId"),
            nickname=self._first_text(user, "nickname", "name"),
            source_account_id=source_account_id,
        )

    @staticmethod
    def _first_text(data: dict[str, Any], *keys: str) -> str | None:
        for key in keys:
            value = data.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return None

    @staticmethod
    def _int_value(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _first_aweme_ids(data: Any, limit: int) -> list[str]:
        if not isinstance(data, dict):
            return []
        aweme_list = data.get("aweme_list")
        if not isinstance(aweme_list, list):
            return []
        ids: list[str] = []
        for item in aweme_list:
            if not isinstance(item, dict):
                continue
            aweme_id = item.get("aweme_id") or item.get("awemeId") or item.get("item_id") or item.get("itemId")
            if aweme_id:
                ids.append(str(aweme_id))
            if len(ids) >= limit:
                break
        return ids

    def _find_sec_uid(self, data: Any) -> str | None:
        for item in self._walk_dicts(data):
            for key in ("sec_uid", "secUid", "sec_user_id", "secUserId"):
                value = item.get(key)
                if value:
                    return str(value)
        return None

    def _verification_block_reason(self, data: Any) -> str | None:
        if not isinstance(data, dict):
            return None
        nil = data.get("search_nil_info")
        if isinstance(nil, dict):
            nil_type = str(nil.get("search_nil_type") or "")
            nil_item = str(nil.get("search_nil_item") or "")
            if nil_type in {"verify_check", "antispam_check"} or nil_item in {"verify_check", "hit_shark"}:
                return f"{nil_type or '-'} / {nil_item or '-'}"
        return None

    def _walk_dicts(self, value: Any):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from self._walk_dicts(child)
        elif isinstance(value, list):
            for child in value:
                yield from self._walk_dicts(child)
