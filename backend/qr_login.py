"""Boss 官网微信扫码登录（小程序码）。

流程与 www.zhipin.com 登录页「微信扫一扫」一致：
randkey → getMpCode（小程序码图）→ scanByMp 轮询 → loginConfirm + fp → 写入本地 session。
不依赖小程序调试通道。
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from typing import Any

import httpx

from .http_util import aes_zp, boss_client, merge_cookies
from .session_store import save_session, session_public

logger = logging.getLogger(__name__)

# uuid(shortRandKey) -> 内存中的扫码会话（cookies / qr_id 等）
_qr_lock = threading.Lock()
_qr_sessions: dict[str, dict[str, Any]] = {}


def _fingerprint_plain() -> str:
    """构造近似官网 zpFingerPrint 的明文：4 段 md5 用 '.' 拼接。"""
    parts = [
        hashlib.md5(b"boss-greet-web|canvas|v1").hexdigest(),
        hashlib.md5(b"boss-greet-web|webgl|v1").hexdigest(),
        hashlib.md5(
            b"deviceMemory=8|hardwareConcurrency=8|pdfViewerEnabled=true|"
            b"platform=MacIntel|product=Gecko|vendor=Google Inc."
        ).hexdigest(),
        hashlib.md5(b"20240314").hexdigest(),
    ]
    return ".".join(parts)


def _put_session(uuid: str, data: dict[str, Any]) -> None:
    with _qr_lock:
        _qr_sessions[uuid] = data


def _get_session(uuid: str) -> dict[str, Any] | None:
    with _qr_lock:
        return _qr_sessions.get(uuid)


def _pop_session(uuid: str) -> dict[str, Any] | None:
    with _qr_lock:
        return _qr_sessions.pop(uuid, None)


def _cleanup_old(max_age: float = 600.0) -> None:
    now = time.time()
    with _qr_lock:
        dead = [k for k, v in _qr_sessions.items() if now - float(v.get("created_at") or 0) > max_age]
        for k in dead:
            _qr_sessions.pop(k, None)


async def qr_start(*, pk: str = "header-login") -> dict[str, Any]:
    """创建扫码会话，返回 shortRandKey(uuid) 与小程序码图片 URL。"""
    _cleanup_old()
    async with boss_client() as client:
        try:
            await client.get("/web/user/")
        except Exception:  # noqa: BLE001
            pass
        r = await client.post("/wapi/zppassport/captcha/randkey")
        data = r.json()
        cookies = merge_cookies(dict(r.cookies), dict(client.cookies))
        if data.get("code") != 0:
            return {
                "ok": False,
                "msg": data.get("message") or data.get("msg") or "创建二维码失败",
                "code": data.get("code"),
            }
        zp = data.get("zpData") or {}
        qr_id = zp.get("qrId") or ""
        short = zp.get("shortRandKey") or ""
        if not short:
            return {"ok": False, "msg": "未返回 shortRandKey"}

        r2 = await client.get(
            "/wapi/zppassport/qrcode/getMpCode",
            params={"uuid": short, "pk": pk},
        )
        data2 = r2.json()
        cookies = merge_cookies(cookies, dict(r2.cookies), dict(client.cookies))
        if data2.get("code") != 0:
            return {
                "ok": False,
                "msg": data2.get("message") or data2.get("msg") or "获取二维码图片失败",
                "code": data2.get("code"),
            }
        mp_url = ((data2.get("zpData") or {}).get("mpCodeUrl")) or ""
        if not mp_url:
            return {"ok": False, "msg": "未返回二维码图片地址"}

    _put_session(
        short,
        {
            "cookies": cookies,
            "qr_id": qr_id,
            "short_rand_key": short,
            "rand_key": zp.get("randKey") or "",
            "pk": pk,
            "created_at": time.time(),
        },
    )
    return {
        "ok": True,
        "uuid": short,
        "qr_id": qr_id,
        "image_url": mp_url,
        "msg": "请使用微信扫描二维码，在手机上确认登录",
        "expires_in": 180,
    }


async def qr_poll(uuid: str) -> dict[str, Any]:
    """轮询是否已扫码；未扫时返回 waiting，已扫则自动走 loginConfirm。"""
    uuid = (uuid or "").strip()
    sess = _get_session(uuid)
    if not sess:
        return {"ok": False, "status": "expired", "msg": "二维码已失效，请刷新"}

    async with boss_client(sess.get("cookies") or {}) as client:
        try:
            r = await client.get(
                "/wapi/zppassport/qrcode/scanByMp",
                params={"uuid": uuid},
                timeout=20.0,
            )
            cookies = merge_cookies(sess.get("cookies") or {}, dict(r.cookies), dict(client.cookies))
            sess["cookies"] = cookies
            _put_session(uuid, sess)
            try:
                data = r.json()
            except Exception:  # noqa: BLE001
                return {"ok": True, "status": "waiting", "msg": "等待扫码…"}
        except httpx.TimeoutException:
            return {"ok": True, "status": "waiting", "msg": "等待扫码…"}
        except Exception as exc:  # noqa: BLE001
            logger.warning("qr_poll error: %s", exc)
            return {"ok": True, "status": "waiting", "msg": "等待扫码…"}

    scanned = bool(data.get("scaned") or data.get("scanned") or (data.get("zpData") or {}).get("scaned"))
    if data.get("code") not in (0, None) and not scanned:
        msg = data.get("message") or data.get("msg") or ""
        if "timeout" in str(msg).lower() or data.get("scaned") is False:
            return {"ok": True, "status": "waiting", "msg": "等待扫码…"}
        return {
            "ok": False,
            "status": "error",
            "msg": msg or f"轮询失败({data.get('code')})",
            "code": data.get("code"),
        }

    if not scanned:
        return {"ok": True, "status": "waiting", "msg": "等待扫码…"}

    return await qr_confirm(uuid)


async def qr_confirm(uuid: str) -> dict[str, Any]:
    """扫码后带 fp 调用 loginConfirm，成功则写入 data/session.json。"""
    uuid = (uuid or "").strip()
    sess = _get_session(uuid)
    if not sess:
        return {"ok": False, "status": "expired", "msg": "二维码已失效，请刷新"}

    pk = sess.get("pk") or "header-login"
    fp = aes_zp(_fingerprint_plain())
    params = {"uuid": uuid, "pk": pk, "fp": fp}

    async with boss_client(sess.get("cookies") or {}) as client:
        r = await client.get("/wapi/zppassport/qrcode/loginConfirm", params=params)
        try:
            data = r.json()
        except Exception:  # noqa: BLE001
            return {"ok": False, "status": "error", "msg": f"登录确认失败（HTTP {r.status_code}）"}
        cookies = merge_cookies(sess.get("cookies") or {}, dict(r.cookies), dict(client.cookies))

    code = data.get("code")
    msg = data.get("message") or data.get("msg") or ""
    zp = data.get("zpData") or {}
    if code != 0:
        return {
            "ok": False,
            "status": "error",
            "msg": msg or f"登录失败({code})",
            "code": code,
            "zp_data": zp,
        }

    wt2 = zp.get("wt2") or zp.get("wt") or cookies.get("wt2") or cookies.get("bst") or ""
    mpt = zp.get("mpt") or ""
    saved = save_session(
        {
            "source": "qr",
            "identity": zp.get("identity", 0),
            "wt2": wt2,
            "wt": wt2,
            "mpt": mpt,
            "show_name": zp.get("showName") or zp.get("name") or "",
            "encrypt_user_id": zp.get("encryptUserId") or "",
            "user_id": zp.get("userId") or zp.get("uid") or "",
            "zp_at": zp.get("zpAt") or cookies.get("zp_at") or "",
            "cookies": cookies,
            "raw_login": {
                k: zp.get(k)
                for k in (
                    "wt2",
                    "mpt",
                    "showName",
                    "name",
                    "identity",
                    "encryptUserId",
                    "userId",
                    "uid",
                    "zpAt",
                    "tiny",
                    "large",
                    "token",
                )
                if zp.get(k) is not None
            },
        }
    )
    _pop_session(uuid)
    return {
        "ok": True,
        "status": "confirmed",
        "msg": "扫码登录成功",
        "session": session_public(saved),
    }
