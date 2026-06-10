import httpx
import pytest

from douyin_api.guest_cookie import HttpGuestCookieProvider


@pytest.mark.asyncio
async def test_http_guest_cookie_provider_builds_cookie_without_browser():
    seen_paths = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        if request.url.path == "/":
            return httpx.Response(200, headers={"set-cookie": "__ac_nonce=guest_nonce; Path=/; Domain=.douyin.com"})
        if request.url.path == "/ttwid/union/register/":
            return httpx.Response(
                200,
                json={
                    "status_code": 0,
                    "message": "union register success",
                    "redirect_url": "https://www.douyin.com/ttwid/union/register/callback/?ticket=ticket",
                },
                headers={"set-cookie": "ttwid=guest_ttwid; Path=/"},
            )
        if request.url.path == "/ttwid/union/register/callback/":
            return httpx.Response(200, json={"status_code": 0, "message": "callback success"})
        if request.url.path == "/web/report":
            return httpx.Response(
                200,
                json={"dataType": 8},
                headers={"set-cookie": "msToken=guest_ms_token; Path=/"},
            )
        return httpx.Response(404, json={"error": "unexpected path"})

    provider = HttpGuestCookieProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    result = await provider.build()

    assert result.source == "http"
    assert "ttwid=guest_ttwid" in result.cookie
    assert "msToken=guest_ms_token" in result.cookie
    assert "s_v_web_id=" in result.cookie
    assert "/ttwid/union/register/" in seen_paths
    assert "/web/report" in seen_paths
