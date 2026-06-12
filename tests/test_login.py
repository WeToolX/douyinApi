import base64

import pytest

from douyin_api.login import DouyinQRCodeLogin, LoginSession


def test_extract_storage_user_info_collects_userid_employee_no_and_totp():
    storage_state = {
        "cookies": [
            {"name": "passport_auth_id", "value": "passport_user"},
            {"name": "sessionid", "value": "sid"},
        ],
        "origins": [
            {
                "origin": "https://creator.douyin.com",
                "localStorage": [
                    {
                        "name": "account",
                        "value": '{"userId":"user_100","employeeNo":"EMP100","totpCode":"654321","nickname":"员工账号"}',
                    },
                    {
                        "name": "profile",
                        "value": '{"secUid":"MS4wLjABAAAA-login","uniqueId":"douyin_100"}',
                    },
                ],
            }
        ],
    }

    login = DouyinQRCodeLogin()
    result = login._merge_user_info(
        {"name": "页面账号"},
        login._extract_storage_user_info(storage_state),
        login._extract_cookie_user_info(storage_state["cookies"]),
    )

    assert result["user_id"] == "user_100"
    assert result["employee_no"] == "EMP100"
    assert result["totp"] == "654321"
    assert result["name"] == "页面账号"
    assert result["sec_uid"] == "MS4wLjABAAAA-login"
    assert result["douyin_id"] == "douyin_100"
    assert result["extra"]["cookies"]["passport_auth_id"] == "passport_user"


@pytest.mark.asyncio
async def test_extract_qr_image_uses_qr_element_screenshot_when_src_missing():
    class FakeNode:
        async def get_attribute(self, name):
            assert name == "src"
            return None

        async def screenshot(self):
            return b"qr-element"

    class FakePage:
        async def wait_for_selector(self, selector, timeout):
            return FakeNode()

        async def screenshot(self, full_page=False):
            return b"full-page"

    login = DouyinQRCodeLogin()

    result = await login._extract_qr_image(FakePage())

    assert result == f"data:image/png;base64,{base64.b64encode(b'qr-element').decode('utf-8')}"


@pytest.mark.asyncio
async def test_extract_qr_image_raises_instead_of_returning_full_page_screenshot():
    class FakePage:
        url = "https://creator.douyin.com/creator-micro/login?enter_from=qr"

        async def wait_for_selector(self, selector, timeout):
            raise TimeoutError("not found")

        async def title(self):
            return "风控验证"

        async def inner_text(self, selector):
            return "请完成验证"

    login = DouyinQRCodeLogin()

    with pytest.raises(RuntimeError) as exc_info:
        await login._extract_qr_image(FakePage())

    assert "未找到抖音登录二维码" in str(exc_info.value)
    assert "creator.douyin.com" in str(exc_info.value)


@pytest.mark.asyncio
async def test_poll_confirms_when_auth_cookie_exists_even_if_url_still_login():
    class FakePage:
        url = "https://creator.douyin.com/creator-micro/login?enter_from=qr"

        async def evaluate(self, script):
            return {}

        async def inner_text(self, selector):
            return ""

        async def title(self):
            return "扫码登录"

    class FakeContext:
        async def cookies(self):
            return [{"name": "sessionid", "value": "sid", "domain": ".douyin.com", "path": "/"}]

        async def storage_state(self):
            return {"cookies": await self.cookies(), "origins": []}

    class FakeClosable:
        async def close(self):
            return None

        async def stop(self):
            return None

    login = DouyinQRCodeLogin()
    login.sessions["qr_session"] = LoginSession(
        session_id="qr_session",
        page=FakePage(),
        context=FakeContext(),
        browser=FakeClosable(),
        playwright=FakeClosable(),
        note=None,
    )

    result = await login.poll("qr_session")

    assert result["status"] == "confirmed"
    assert result["storage_state"]["cookies"][0]["name"] == "sessionid"
    assert "qr_session" not in login.sessions
