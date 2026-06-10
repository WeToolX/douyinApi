import httpx
import pytest

from douyin_api.account_store import AccountStore
from douyin_api.sec_uid_resolver import DouyinVerificationRequiredError, SecUidResolver


@pytest.mark.asyncio
async def test_resolve_uid_uses_logged_in_account_cookie_and_caches(tmp_path):
    store = AccountStore(tmp_path)
    store.save_login_state(
        note="查询账号",
        storage_state={"cookies": [{"name": "sessionid", "value": "sid", "domain": ".douyin.com", "path": "/"}]},
        user_info={"user_id": "query_account", "name": "查询账号"},
    )
    seen_cookie = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_cookie.append(request.headers.get("cookie", ""))
        return httpx.Response(200, json={"user_info": {"sec_uid": "MS4wLjABAAAA-from-uid"}})

    resolver = SecUidResolver(store, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    first = await resolver.resolve_by_uid("123456")
    second = await resolver.resolve_by_uid("123456")

    assert first.sec_uid == "MS4wLjABAAAA-from-uid"
    assert second.from_cache is True
    assert len(seen_cookie) == 1
    assert "sessionid=sid" in seen_cookie[0]


@pytest.mark.asyncio
async def test_resolve_douyin_id_matches_search_result(tmp_path):
    store = AccountStore(tmp_path)
    store.save_login_state(
        note="查询账号",
        storage_state={"cookies": [{"name": "sessionid", "value": "sid", "domain": ".douyin.com", "path": "/"}]},
        user_info={"user_id": "query_account", "name": "查询账号"},
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "user_list": [
                    {"user_info": {"unique_id": "other", "sec_uid": "bad"}},
                    {"user_info": {"unique_id": "target_id", "sec_uid": "MS4wLjABAAAA-target"}},
                ]
            },
        )

    resolver = SecUidResolver(store, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    result = await resolver.resolve_by_douyin_id("target_id")

    assert result.sec_uid == "MS4wLjABAAAA-target"
    assert result.source_account_id == "query_account"


@pytest.mark.asyncio
async def test_resolve_keyword_returns_target_user_fields(tmp_path):
    store = AccountStore(tmp_path)
    store.save_login_state(
        note="查询账号",
        storage_state={"cookies": [{"name": "sessionid", "value": "sid", "domain": ".douyin.com", "path": "/"}]},
        user_info={"user_id": "query_account", "name": "查询账号"},
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("keyword") == "target_id"
        assert request.url.params.get("device_platform") == "webapp"
        assert request.url.params.get("aid") == "6383"
        assert request.url.params.get("browser_name") == "Chrome"
        return httpx.Response(
            200,
            json={
                "user_list": [
                    {
                        "user_info": {
                            "uid": "1234567890",
                            "sec_uid": "MS4wLjABAAAA-target",
                            "unique_id": "target_id",
                            "short_id": "123456",
                            "nickname": "目标用户",
                        }
                    }
                ]
            },
        )

    resolver = SecUidResolver(store, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    result = await resolver.resolve_user_sec_uid("target_id")

    assert result.uid == "1234567890"
    assert result.sec_uid == "MS4wLjABAAAA-target"
    assert result.unique_id == "target_id"
    assert result.short_id == "123456"
    assert result.nickname == "目标用户"


@pytest.mark.asyncio
async def test_resolve_keyword_require_uid_ignores_numeric_cache(tmp_path):
    store = AccountStore(tmp_path)
    store.save_login_state(
        note="查询账号",
        storage_state={"cookies": [{"name": "sessionid", "value": "sid", "domain": ".douyin.com", "path": "/"}]},
        user_info={"user_id": "query_account", "name": "查询账号"},
    )
    store.upsert_sec_uid_cache("uid", "75507974362", "MS4wLjABAAAA-stale", "query_account")
    seen_paths = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        if request.url.path in ("/aweme/v1/web/im/user/info/", "/web/api/v2/user/info/"):
            return httpx.Response(200, json={"status_code": 0})
        return httpx.Response(
            200,
            json={
                "user_list": [
                    {
                        "user_info": {
                            "uid": "1883939202669508",
                            "sec_uid": "MS4wLjABAAAA-target",
                            "unique_id": "75507974362",
                            "short_id": "75507974362",
                            "nickname": "目标用户",
                        }
                    }
                ]
            },
        )

    resolver = SecUidResolver(store, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    result = await resolver.resolve_user_sec_uid("75507974362", require_uid=True)

    assert result.from_cache is False
    assert result.uid == "1883939202669508"
    assert result.sec_uid == "MS4wLjABAAAA-target"
    assert result.unique_id == "75507974362"
    assert store.get_sec_uid_cache("uid", "75507974362")["sec_uid"] == "MS4wLjABAAAA-stale"
    assert store.get_sec_uid_cache("uid", "1883939202669508")["sec_uid"] == "MS4wLjABAAAA-target"
    assert store.get_sec_uid_cache("douyin_id", "75507974362")["sec_uid"] == "MS4wLjABAAAA-target"
    assert seen_paths


@pytest.mark.asyncio
async def test_resolve_keyword_require_uid_uses_linked_uid_cache(tmp_path):
    store = AccountStore(tmp_path)
    store.save_login_state(
        note="查询账号",
        storage_state={"cookies": [{"name": "sessionid", "value": "sid", "domain": ".douyin.com", "path": "/"}]},
        user_info={"user_id": "query_account", "name": "查询账号"},
    )
    store.upsert_sec_uid_cache("uid", "75507974362", "MS4wLjABAAAA-target", "query_account")
    store.upsert_sec_uid_cache("uid", "1883939202669508", "MS4wLjABAAAA-target", "query_account")
    store.upsert_sec_uid_cache("douyin_id", "75507974362", "MS4wLjABAAAA-target", "query_account")

    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("完整缓存命中时不应请求抖音搜索接口")

    resolver = SecUidResolver(store, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    result = await resolver.resolve_user_sec_uid("75507974362", require_uid=True)

    assert result.from_cache is True
    assert result.uid == "1883939202669508"
    assert result.sec_uid == "MS4wLjABAAAA-target"
    assert result.unique_id == "75507974362"


@pytest.mark.asyncio
async def test_resolve_keyword_require_uid_rejects_sec_uid_only_result(tmp_path):
    store = AccountStore(tmp_path)
    store.save_login_state(
        note="查询账号",
        storage_state={"cookies": [{"name": "sessionid", "value": "sid", "domain": ".douyin.com", "path": "/"}]},
        user_info={"user_id": "query_account", "name": "查询账号"},
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"user_info": {"sec_uid": "MS4wLjABAAAA-only"}})

    resolver = SecUidResolver(store, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    with pytest.raises(Exception, match="未解析到真实 uid"):
        await resolver.resolve_user_sec_uid("75507974362", require_uid=True)


@pytest.mark.asyncio
async def test_handler_user_profile_uses_sec_user_id_and_cookie(tmp_path):
    store = AccountStore(tmp_path)
    store.save_login_state(
        note="查询账号",
        storage_state={"cookies": [{"name": "sessionid", "value": "sid", "domain": ".douyin.com", "path": "/"}]},
        user_info={"user_id": "query_account", "name": "查询账号"},
    )
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["sec_user_id"] = request.url.params.get("sec_user_id")
        seen["cookie"] = request.headers.get("cookie")
        return httpx.Response(200, json={"user": {"nickname": "目标用户"}})

    resolver = SecUidResolver(store, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    result = await resolver.handler_user_profile("MS4wLjABAAAA-target")

    assert result["user"]["nickname"] == "目标用户"
    assert seen["path"] == "/aweme/v1/web/user/profile/other/"
    assert seen["sec_user_id"] == "MS4wLjABAAAA-target"
    assert seen["cookie"] == "sessionid=sid"


@pytest.mark.asyncio
async def test_fetch_user_stats_preview_uses_cookie_and_returns_first_two_ids(tmp_path):
    store = AccountStore(tmp_path)
    store.save_login_state(
        note="查询账号",
        storage_state={"cookies": [{"name": "sessionid", "value": "sid", "domain": ".douyin.com", "path": "/"}]},
        user_info={"user_id": "query_account", "name": "查询账号"},
    )
    seen = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.url.params.get("sec_user_id"), request.headers.get("cookie")))
        if request.url.path == "/aweme/v1/web/user/profile/other/":
            return httpx.Response(
                200,
                json={
                    "user": {
                        "uid": "123",
                        "sec_uid": "MS4wLjABAAAA-target",
                        "unique_id": "target_id",
                        "short_id": "456",
                        "nickname": "目标用户",
                        "following_count": 4,
                        "follower_count": 5,
                        "favoriting_count": 6,
                        "total_favorited": 7,
                    }
                },
            )
        if request.url.path == "/aweme/v1/web/aweme/favorite/":
            return httpx.Response(
                200,
                json={"aweme_list": [{"aweme_id": "like_1"}, {"aweme_id": "like_2"}, {"aweme_id": "like_3"}]},
            )
        if request.url.path == "/aweme/v1/web/aweme/listcollection/":
            return httpx.Response(
                200,
                json={"aweme_list": [{"aweme_id": "collect_1"}, {"aweme_id": "collect_2"}, {"aweme_id": "collect_3"}]},
            )
        return httpx.Response(404, json={"error": "unexpected path"})

    resolver = SecUidResolver(store, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    result = await resolver.fetch_user_stats_preview("MS4wLjABAAAA-target")

    assert result["source_account_id"] == "query_account"
    assert result["sec_uid"] == "MS4wLjABAAAA-target"
    assert result["stats"] == {
        "following_count": 4,
        "follower_count": 5,
        "favoriting_count": 6,
        "total_favorited": 7,
    }
    assert result["user"]["nickname"] == "目标用户"
    assert result["like_aweme_ids"] == ["like_1", "like_2"]
    assert result["like_source"] == "target_sec_uid"
    assert result["collection_aweme_ids"] == ["collect_1", "collect_2"]
    assert result["collection_source"] == "login_account"
    assert all(item[3] == "sessionid=sid" for item in seen)
    assert ("GET", "/aweme/v1/web/aweme/favorite/", "MS4wLjABAAAA-target", "sessionid=sid") in seen
    assert ("POST", "/aweme/v1/web/aweme/listcollection/", None, "sessionid=sid") in seen


@pytest.mark.asyncio
async def test_guest_handler_user_profile_uses_temporary_cookie_without_account(tmp_path):
    store = AccountStore(tmp_path)
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["cookie"] = request.headers.get("cookie")
        return httpx.Response(200, json={"user": {"nickname": "临时用户"}})

    resolver = SecUidResolver(store, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    async def fake_guest_headers(sec_user_id: str) -> dict[str, str]:
        assert sec_user_id == "MS4wLjABAAAA-target"
        return {"User-Agent": "UA", "Referer": "https://www.douyin.com/", "Cookie": "ttwid=guest"}

    resolver._build_guest_headers = fake_guest_headers

    result = await resolver.guest_handler_user_profile("MS4wLjABAAAA-target")

    assert result["user"]["nickname"] == "临时用户"
    assert seen["path"] == "/aweme/v1/web/user/profile/other/"
    assert seen["cookie"] == "ttwid=guest"


@pytest.mark.asyncio
async def test_fetch_guest_user_stats_preview_returns_full_profile_and_probe_status(tmp_path):
    store = AccountStore(tmp_path)
    seen = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.headers.get("cookie")))
        if request.url.path == "/aweme/v1/web/user/profile/other/":
            return httpx.Response(
                200,
                json={
                    "status_code": 0,
                    "user": {
                        "uid": "123",
                        "sec_uid": "MS4wLjABAAAA-target",
                        "nickname": "临时用户",
                        "following_count": 4,
                        "follower_count": 5,
                        "favoriting_count": 6,
                        "total_favorited": 7,
                    },
                },
            )
        if request.url.path == "/aweme/v1/web/aweme/favorite/":
            return httpx.Response(200, json={"status_code": 0, "aweme_list": [{"aweme_id": "like_1"}, {"aweme_id": "like_2"}]})
        if request.url.path == "/aweme/v1/web/aweme/listcollection/":
            return httpx.Response(200, json={"status_code": 5, "aweme_list": []})
        return httpx.Response(404, json={"error": "unexpected path"})

    resolver = SecUidResolver(store, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    async def fake_guest_headers(sec_user_id: str) -> dict[str, str]:
        return {"User-Agent": "UA", "Referer": "https://www.douyin.com/", "Cookie": "ttwid=guest"}

    resolver._build_guest_headers = fake_guest_headers

    result = await resolver.fetch_guest_user_stats_preview("MS4wLjABAAAA-target")

    assert result["cookie_source"] == "guest"
    assert result["source_account_id"] is None
    assert result["profile"]["user"]["nickname"] == "临时用户"
    assert result["stats"]["following_count"] == 4
    assert result["like_aweme_ids"] == ["like_1", "like_2"]
    assert result["like_status_code"] == 0
    assert result["collection_aweme_ids"] == []
    assert result["collection_status_code"] == 5
    assert all(item[2] == "ttwid=guest" for item in seen)


@pytest.mark.asyncio
async def test_resolve_keyword_marks_account_when_douyin_requires_verification(tmp_path):
    store = AccountStore(tmp_path)
    store.save_login_state(
        note="查询账号",
        storage_state={"cookies": [{"name": "sessionid", "value": "sid", "domain": ".douyin.com", "path": "/"}]},
        user_info={"user_id": "query_account", "name": "查询账号"},
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status_code": 0,
                "user_list": [],
                "search_nil_info": {
                    "search_nil_type": "antispam_check",
                    "search_nil_item": "hit_shark",
                },
            },
        )

    resolver = SecUidResolver(store, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    with pytest.raises(DouyinVerificationRequiredError):
        await resolver.resolve_user_sec_uid("40863376123")

    account = store.get_account("query_account")
    assert account["login_status"] == "verify_required"
    assert account["status"] == "error"
