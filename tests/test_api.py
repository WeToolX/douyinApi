from fastapi.testclient import TestClient
import httpx

from douyin_api.app import create_app
from douyin_api.sec_uid_resolver import ResolvedUser


def test_accounts_api_lists_saved_accounts(tmp_path):
    app = create_app(data_dir=tmp_path)
    store = app.state.account_store
    store.save_login_state(
        note="备注",
        storage_state={"cookies": []},
        user_info={
            "user_id": "dy_api",
            "employee_no": "EMP_API",
            "totp": "789012",
            "name": "接口账号",
            "sec_uid": "MS4wLjABAAAA-api",
        },
    )

    client = TestClient(app)
    response = client.get("/api/accounts")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["total"] == 1
    assert body["items"][0]["sec_uid"] == "MS4wLjABAAAA-api"
    assert body["items"][0]["employee_no"] == "EMP_API"
    assert body["items"][0]["totp"] == "789012"


def test_sec_uid_api_returns_404_when_no_login_account(tmp_path):
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)

    response = client.get("/api/douyin/web/resolve_user_sec_uid?keyword=target_id")

    assert response.status_code == 404
    assert "没有可用的抖音登录账号" in response.json()["detail"]


def test_resolve_user_sec_uid_api_returns_target_user_shape(tmp_path):
    app = create_app(data_dir=tmp_path)
    store = app.state.account_store
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
                    {
                        "user_info": {
                            "uid": "416073474129276",
                            "sec_uid": "MS4wLjABAAAA-target",
                            "unique_id": "40863376123",
                            "short_id": "40863376123",
                            "nickname": "aabhd",
                        }
                    }
                ]
            },
        )

    app.state.sec_uid_resolver.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = TestClient(app)

    response = client.get("/api/douyin/web/resolve_user_sec_uid?keyword=40863376123")

    assert response.status_code == 200
    body = response.json()
    assert body["uid"] == "416073474129276"
    assert body["sec_uid"] == "MS4wLjABAAAA-target"
    assert body["unique_id"] == "40863376123"
    assert body["short_id"] == "40863376123"
    assert body["nickname"] == "aabhd"


def test_resolve_user_sec_uid_api_passes_require_uid(tmp_path):
    app = create_app(data_dir=tmp_path)
    seen = {}

    async def fake_resolve(keyword: str, require_uid: bool = False):
        seen["keyword"] = keyword
        seen["require_uid"] = require_uid
        return ResolvedUser(
            uid="1883939202669508",
            sec_uid="MS4wLjABAAAA-target",
            unique_id=keyword,
            short_id=keyword,
            nickname="目标用户",
            source_account_id="query_account",
        )

    app.state.sec_uid_resolver.resolve_user_sec_uid = fake_resolve
    client = TestClient(app)

    response = client.get("/api/douyin/web/resolve_user_sec_uid?keyword=75507974362&require_uid=1")

    assert response.status_code == 200
    assert seen == {"keyword": "75507974362", "require_uid": True}
    assert response.json()["uid"] == "1883939202669508"


def test_qrcode_poll_prints_full_cookie_after_login(tmp_path, capsys):
    app = create_app(data_dir=tmp_path)

    class FakeLoginManager:
        async def poll(self, session_id: str):
            assert session_id == "qr_session"
            return {
                "status": "confirmed",
                "storage_state": {
                    "cookies": [
                        {"name": "sessionid", "value": "sid", "domain": ".douyin.com", "path": "/"},
                        {"name": "ttwid", "value": "tw", "domain": ".douyin.com", "path": "/"},
                    ]
                },
                "user_info": {"user_id": "query_account", "name": "查询账号"},
            }

    app.state.login_manager = FakeLoginManager()
    client = TestClient(app)

    response = client.get("/api/auth/qrcode/poll?session_id=qr_session")

    assert response.status_code == 200
    assert response.json()["status"] == "confirmed"
    output = capsys.readouterr().out
    assert "完整Cookie: sessionid=sid; ttwid=tw" in output


def test_user_stats_preview_api_returns_normalized_counts_and_ids(tmp_path):
    app = create_app(data_dir=tmp_path)

    async def fake_stats(sec_user_id: str):
        assert sec_user_id == "MS4wLjABAAAA-target"
        return {
            "sec_uid": sec_user_id,
            "source_account_id": "query_account",
            "stats": {
                "following_count": 4,
                "follower_count": 5,
                "favoriting_count": 6,
                "total_favorited": 7,
            },
            "user": {"nickname": "目标用户"},
            "like_aweme_ids": ["like_1", "like_2"],
            "like_source": "target_sec_uid",
            "collection_aweme_ids": ["collect_1", "collect_2"],
            "collection_source": "login_account",
        }

    app.state.sec_uid_resolver.fetch_user_stats_preview = fake_stats
    client = TestClient(app)

    response = client.get("/api/douyin/web/user_stats_preview?sec_user_id=MS4wLjABAAAA-target")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["stats"]["following_count"] == 4
    assert body["data"]["like_aweme_ids"] == ["like_1", "like_2"]
    assert body["data"]["collection_source"] == "login_account"


def test_guest_handler_user_profile_api_returns_full_profile(tmp_path):
    app = create_app(data_dir=tmp_path)

    async def fake_profile(sec_user_id: str):
        assert sec_user_id == "MS4wLjABAAAA-target"
        return {"status_code": 0, "user": {"nickname": "临时用户"}}

    app.state.sec_uid_resolver.guest_handler_user_profile = fake_profile
    client = TestClient(app)

    response = client.get("/api/douyin/web/guest_handler_user_profile?sec_user_id=MS4wLjABAAAA-target")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["user"]["nickname"] == "临时用户"


def test_guest_user_stats_preview_api_returns_preview(tmp_path):
    app = create_app(data_dir=tmp_path)

    async def fake_stats(sec_user_id: str):
        assert sec_user_id == "MS4wLjABAAAA-target"
        return {
            "cookie_source": "guest",
            "source_account_id": None,
            "profile": {"status_code": 0, "user": {"nickname": "临时用户"}},
            "stats": {"following_count": 4, "follower_count": 5, "favoriting_count": 6, "total_favorited": 7},
            "like_aweme_ids": ["like_1", "like_2"],
            "collection_aweme_ids": [],
            "collection_status_code": 5,
        }

    app.state.sec_uid_resolver.fetch_guest_user_stats_preview = fake_stats
    client = TestClient(app)

    response = client.get("/api/douyin/web/guest_user_stats_preview?sec_user_id=MS4wLjABAAAA-target")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["cookie_source"] == "guest"
    assert body["data"]["stats"]["favoriting_count"] == 6
    assert body["data"]["collection_status_code"] == 5


def test_guest_cookie_api_returns_http_cookie(tmp_path):
    app = create_app(data_dir=tmp_path)

    async def fake_cookie():
        return {
            "source": "http",
            "cookie": "ttwid=guest_ttwid; msToken=guest_ms_token; s_v_web_id=verify_guest",
            "cookie_names": ["ttwid", "msToken", "s_v_web_id"],
            "user_agent": "UA",
            "expires_in": 600,
        }

    app.state.sec_uid_resolver.build_http_guest_cookie = fake_cookie
    client = TestClient(app)

    response = client.get("/api/douyin/web/guest_cookie")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["source"] == "http"
    assert "ttwid=guest_ttwid" in body["data"]["cookie"]


def test_download_touchsprite_module_returns_zip(tmp_path):
    module_dir = tmp_path / "module"
    module_dir.mkdir()
    zip_path = module_dir / "touchsprite.zip"
    zip_path.write_bytes(b"touchsprite zip")
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)

    response = client.get("/api/module/touchsprite/download")

    assert response.status_code == 200
    assert response.content == b"touchsprite zip"
    assert response.headers["content-type"] == "application/zip"
    assert 'filename="touchsprite.zip"' in response.headers["content-disposition"]


def test_download_touchsprite_module_returns_404_when_missing(tmp_path):
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)

    response = client.get("/api/module/touchsprite/download")

    assert response.status_code == 404
    assert "touchsprite.zip 不存在" in response.json()["detail"]


def test_touchsprite_module_version_returns_config_v(tmp_path):
    module_dir = tmp_path / "module"
    module_dir.mkdir()
    (module_dir / "config.json").write_text('{"v":"1.0.0"}', encoding="utf-8")
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)

    response = client.get("/api/module/touchsprite/version")

    assert response.status_code == 200
    assert response.json() == {"success": True, "v": "1.0.0"}


def test_touchsprite_module_version_returns_404_when_config_missing(tmp_path):
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)

    response = client.get("/api/module/touchsprite/version")

    assert response.status_code == 404
    assert "config.json 不存在" in response.json()["detail"]


def test_touchsprite_module_version_returns_422_when_v_missing(tmp_path):
    module_dir = tmp_path / "module"
    module_dir.mkdir()
    (module_dir / "config.json").write_text('{"name":"touchsprite"}', encoding="utf-8")
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)

    response = client.get("/api/module/touchsprite/version")

    assert response.status_code == 422
    assert "config.json 缺少 v 字段" in response.json()["detail"]
