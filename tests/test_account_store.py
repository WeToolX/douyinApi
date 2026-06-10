import json

from douyin_api.account_store import AccountStore


def test_save_login_state_persists_account_and_cookie_file(tmp_path):
    store = AccountStore(tmp_path)
    storage_state = {
        "cookies": [
            {"name": "sessionid", "value": "sid-value", "domain": ".douyin.com", "path": "/"},
        ],
        "origins": [],
    }

    account = store.save_login_state(
        note="主账号",
        storage_state=storage_state,
        user_info={
            "user_id": "dy_test",
            "employee_no": "EMP001",
            "totp": "123456",
            "name": "测试账号",
            "avatar": "https://example.com/a.png",
            "sec_uid": "MS4wLjABAAAA-test",
        },
    )

    assert account["account_id"] == "dy_test"
    assert account["status"] == "valid"
    assert account["login_status"] == "logged_in"
    assert account["sec_uid"] == "MS4wLjABAAAA-test"
    assert account["employee_no"] == "EMP001"
    assert account["totp"] == "123456"

    cookie_path = tmp_path / "cookies" / "douyin_dy_test.json"
    assert cookie_path.exists()
    assert json.loads(cookie_path.read_text())["user_info"]["name"] == "测试账号"

    listed = store.list_accounts()
    assert len(listed) == 1
    assert listed[0]["name"] == "测试账号"


def test_get_available_account_skips_expired_accounts(tmp_path):
    store = AccountStore(tmp_path)
    store.save_login_state(
        note="失效",
        storage_state={"cookies": []},
        user_info={"user_id": "expired_user", "name": "失效账号"},
    )
    store.update_login_status("expired_user", "session_expired")
    store.save_login_state(
        note="正常",
        storage_state={"cookies": [{"name": "sessionid", "value": "ok", "domain": ".douyin.com"}]},
        user_info={"user_id": "valid_user", "name": "正常账号"},
    )

    account = store.get_available_account()

    assert account is not None
    assert account["account_id"] == "valid_user"
