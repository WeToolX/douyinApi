from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional, Union

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .account_store import AccountStore
from .login import DouyinQRCodeLogin
from .sec_uid_resolver import DouyinVerificationRequiredError, NoAvailableAccountError, SecUidNotFoundError, SecUidResolver
from .status_checker import LoginStatusChecker


class LoginGenerateRequest(BaseModel):
    note: Optional[str] = None
    headless: bool = True


class AccountUpdateRequest(BaseModel):
    note: Optional[str] = None
    name: Optional[str] = None


class LoginStatusRequest(BaseModel):
    account_ids: Optional[list[str]] = None


def default_data_dir() -> Path:
    return Path(os.getenv("DOUYIN_API_DATA_DIR", "data")).resolve()


def create_app(data_dir: Optional[Union[str, Path]] = None) -> FastAPI:
    app = FastAPI(title="Douyin Account Library", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    store = AccountStore(data_dir or default_data_dir())
    app.state.account_store = store
    app.state.login_manager = DouyinQRCodeLogin()
    app.state.sec_uid_resolver = SecUidResolver(store)
    app.state.status_checker = LoginStatusChecker(store)

    @app.get("/api/health")
    async def health():
        return {"success": True, "status": "ok"}

    @app.get("/api/accounts")
    async def list_accounts():
        items = store.list_accounts()
        return {"success": True, "total": len(items), "items": items}

    @app.put("/api/accounts/{account_id}")
    async def update_account(account_id: str, payload: AccountUpdateRequest):
        try:
            account = store.update_account_note(account_id, payload.note, payload.name)
            return {"success": True, "data": account}
        except KeyError:
            raise HTTPException(status_code=404, detail="账号不存在")

    @app.delete("/api/accounts/invalid")
    async def delete_invalid_accounts():
        count = store.delete_invalid_accounts()
        return {"success": True, "count": count, "message": f"已删除 {count} 个异常账号"}

    @app.delete("/api/accounts/{account_id}")
    async def delete_account(account_id: str):
        if not store.delete_account(account_id):
            raise HTTPException(status_code=404, detail="账号不存在")
        return {"success": True}

    @app.post("/api/auth/qrcode/generate")
    async def generate_qrcode(payload: Optional[LoginGenerateRequest] = None, note: Optional[str] = Query(default=None)):
        payload = payload or LoginGenerateRequest(note=note)
        try:
            data = await app.state.login_manager.generate(note=payload.note or note, headless=payload.headless)
            return {"success": True, "qr_id": data["session_id"], "qr_image": data["qr_image"], "expires_in": data["expires_in"]}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/api/auth/qrcode/poll")
    async def poll_qrcode(session_id: str):
        data = await app.state.login_manager.poll(session_id)
        if data.get("status") == "confirmed":
            storage_state = data.get("storage_state") or {}
            account = store.save_login_state(
                note=None,
                storage_state=storage_state,
                user_info=data.get("user_info") or {},
            )
            cookie_header = SecUidResolver._cookie_header(storage_state)
            if cookie_header:
                print(f"[抖音登录成功] account_id={account['account_id']} 完整Cookie: {cookie_header}", flush=True)
            return {"success": True, "status": "confirmed", "account": account}
        return {"success": True, **data}

    @app.post("/api/creator/check-login-status")
    async def check_login_status(payload: LoginStatusRequest):
        return await app.state.status_checker.check_many(payload.account_ids)

    @app.post("/api/accounts/{account_id}/check")
    async def check_account(account_id: str):
        try:
            return {"success": True, "data": await app.state.status_checker.check_account(account_id)}
        except KeyError:
            raise HTTPException(status_code=404, detail="账号不存在")

    @app.get("/api/sec-uid/uid/{uid}")
    async def resolve_uid(uid: str):
        return await _resolve(app.state.sec_uid_resolver.resolve_by_uid(uid))

    @app.get("/api/douyin/web/resolve_user_sec_uid")
    async def resolve_user_sec_uid(
        keyword: str = Query(..., description="抖音号或 uid"),
        require_uid: bool = Query(False, description="是否必须返回真实 uid"),
    ):
        try:
            result = await app.state.sec_uid_resolver.resolve_user_sec_uid(keyword, require_uid=require_uid)
            return result.__dict__
        except NoAvailableAccountError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except DouyinVerificationRequiredError as exc:
            raise HTTPException(status_code=429, detail=str(exc))
        except SecUidNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc))

    @app.get("/api/douyin/web/handler_user_profile")
    async def handler_user_profile(sec_user_id: str = Query(..., description="用户 sec_user_id")):
        try:
            data = await app.state.sec_uid_resolver.handler_user_profile(sec_user_id)
            return {"success": True, "data": data}
        except NoAvailableAccountError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except DouyinVerificationRequiredError as exc:
            raise HTTPException(status_code=429, detail=str(exc))
        except SecUidNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc))

    @app.get("/api/douyin/web/guest_handler_user_profile")
    async def guest_handler_user_profile(sec_user_id: str = Query(..., description="用户 sec_user_id")):
        try:
            data = await app.state.sec_uid_resolver.guest_handler_user_profile(sec_user_id)
            return {"success": True, "data": data}
        except SecUidNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc))

    @app.get("/api/douyin/web/guest_cookie")
    async def guest_cookie():
        try:
            data = await app.state.sec_uid_resolver.build_http_guest_cookie()
            return {"success": True, "data": data}
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc))

    @app.get("/api/douyin/web/user_stats_preview")
    async def user_stats_preview(sec_user_id: str = Query(..., description="用户 sec_user_id")):
        try:
            data = await app.state.sec_uid_resolver.fetch_user_stats_preview(sec_user_id)
            return {"success": True, "data": data}
        except NoAvailableAccountError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except DouyinVerificationRequiredError as exc:
            raise HTTPException(status_code=429, detail=str(exc))
        except SecUidNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc))

    @app.get("/api/douyin/web/guest_user_stats_preview")
    async def guest_user_stats_preview(sec_user_id: str = Query(..., description="用户 sec_user_id")):
        try:
            data = await app.state.sec_uid_resolver.fetch_guest_user_stats_preview(sec_user_id)
            return {"success": True, "data": data}
        except SecUidNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc))

    @app.get("/api/module/touchsprite/download")
    async def download_touchsprite_module():
        zip_path = store.data_dir / "module" / "touchsprite.zip"
        if not zip_path.exists() or not zip_path.is_file():
            raise HTTPException(status_code=404, detail="touchsprite.zip 不存在")
        return FileResponse(
            zip_path,
            media_type="application/zip",
            filename="touchsprite.zip",
        )

    @app.get("/api/module/touchsprite/version")
    async def touchsprite_module_version():
        config_path = store.data_dir / "module" / "config.json"
        if not config_path.exists() or not config_path.is_file():
            raise HTTPException(status_code=404, detail="config.json 不存在")
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail=f"config.json 不是合法 JSON: {exc.msg}")
        version = config.get("v") if isinstance(config, dict) else None
        if version is None or str(version).strip() == "":
            raise HTTPException(status_code=422, detail="config.json 缺少 v 字段")
        return {"success": True, "v": str(version)}

    @app.get("/api/sec-uid/douyin-id/{douyin_id}")
    async def resolve_douyin_id(douyin_id: str):
        return await _resolve(app.state.sec_uid_resolver.resolve_by_douyin_id(douyin_id))

    @app.post("/api/sec-uid/uid")
    async def resolve_uid_post(payload: dict[str, Any]):
        uid = str(payload.get("uid") or "").strip()
        if not uid:
            raise HTTPException(status_code=400, detail="uid 不能为空")
        return await _resolve(app.state.sec_uid_resolver.resolve_by_uid(uid))

    @app.post("/api/sec-uid/douyin-id")
    async def resolve_douyin_id_post(payload: dict[str, Any]):
        douyin_id = str(payload.get("douyin_id") or payload.get("douyinId") or "").strip()
        if not douyin_id:
            raise HTTPException(status_code=400, detail="抖音号不能为空")
        return await _resolve(app.state.sec_uid_resolver.resolve_by_douyin_id(douyin_id))

    static_dir = Path(__file__).resolve().parent.parent / "frontend" / "dist"
    if static_dir.exists():
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

        @app.get("/")
        async def index():
            return FileResponse(static_dir / "index.html")

    return app


async def _resolve(awaitable):
    try:
        result = await awaitable
        return {"success": True, "data": result.__dict__}
    except NoAvailableAccountError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except DouyinVerificationRequiredError as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    except SecUidNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
