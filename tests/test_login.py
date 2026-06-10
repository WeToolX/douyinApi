from douyin_api.login import DouyinQRCodeLogin


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

