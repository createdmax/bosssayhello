"""Boss 直聘本地打招呼工具 — FastAPI 入口。

职责：静态前端、微信扫码登录 API、经本机小程序通道的搜索/打招呼任务与 SSE。
搜索前只探测小程序是否已登录，不写本地 session、不注入小程序。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from .auth import AuthService
from .boss_api import BossAPI, SearchOptions
from .mcp_client import MiniappMCP
from . import qr_login
from .worker import GreetConfig, GreetWorker

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"

mcp = MiniappMCP()
auth = AuthService(mcp)
api = BossAPI(mcp)
worker = GreetWorker(mcp)

app = FastAPI(title="Boss 打招呼", version="1.0.0")


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="职位关键字")
    require_online: bool = False
    active_within_days: int | None = Field(
        default=None,
        description="最近 N 天活跃：1/3/7，null 表示不限",
    )
    count: int = Field(default=30, ge=1, le=200, description="目标职位数（=打招呼数量）")


class GreetStartRequest(BaseModel):
    query: str = Field(..., min_length=1)
    require_online: bool = False
    active_within_days: int | None = None
    count: int = Field(default=30, ge=1, le=200, description="本次打招呼数量")
    interval_min: float = Field(default=3.0, ge=1.0, le=60.0)
    interval_max: float = Field(default=8.0, ge=1.0, le=120.0)


@app.get("/api/status")
async def status() -> dict[str, Any]:
    """小程序通道连通性 + 本地登录摘要 + 打招呼任务快照。"""
    ping = await mcp.ping()
    return {
        **ping,
        "auth": auth.status(),
        "worker": worker.snapshot(),
    }


@app.get("/api/auth/status")
async def auth_status() -> dict[str, Any]:
    return auth.status()


@app.post("/api/auth/qr/start")
async def auth_qr_start() -> dict[str, Any]:
    """向 Boss 申请微信小程序码并返回图片 URL。"""
    return await qr_login.qr_start(pk="header-login")


@app.get("/api/auth/qr/poll")
async def auth_qr_poll(uuid: str) -> dict[str, Any]:
    """轮询扫码状态；成功时写入本地 session。"""
    if not uuid:
        raise HTTPException(400, "缺少 uuid")
    return await qr_login.qr_poll(uuid)


@app.post("/api/auth/qr/confirm")
async def auth_qr_confirm(uuid: str = "") -> dict[str, Any]:
    """显式确认登录（一般由 poll 在已扫码时自动调用）。"""
    if not uuid:
        raise HTTPException(400, "缺少 uuid")
    return await qr_login.qr_confirm(uuid)


@app.post("/api/auth/logout")
async def auth_logout() -> dict[str, Any]:
    return auth.logout()


async def _ensure_miniapp_ready() -> None:
    """搜索/打招呼前确认微信小程序已登录；失败抛 401。"""
    probed = await auth.probe_miniapp_login()
    if probed.get("ok"):
        return
    raise HTTPException(401, probed.get("msg") or "微信小程序未登录")


@app.post("/api/search")
async def search(body: SearchRequest) -> dict[str, Any]:
    if body.active_within_days is not None and body.active_within_days not in (1, 3, 7, 15, 30):
        raise HTTPException(400, "active_within_days 仅支持 1/3/7/15/30 或不传")
    try:
        await _ensure_miniapp_ready()
        return await api.search(
            SearchOptions(
                query=body.query.strip(),
                require_online=body.require_online,
                active_within_days=body.active_within_days,
                limit=body.count,
            )
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"小程序调用失败: {exc}") from exc


@app.post("/api/greet/start")
async def greet_start(body: GreetStartRequest) -> dict[str, Any]:
    if body.interval_max < body.interval_min:
        raise HTTPException(400, "interval_max 不能小于 interval_min")
    if body.active_within_days is not None and body.active_within_days not in (1, 3, 7, 15, 30):
        raise HTTPException(400, "active_within_days 仅支持 1/3/7/15/30 或不传")
    try:
        await _ensure_miniapp_ready()
    except HTTPException:
        raise
    return await worker.start(
        GreetConfig(
            query=body.query.strip(),
            require_online=body.require_online,
            active_within_days=body.active_within_days,
            count=body.count,
            interval_min=body.interval_min,
            interval_max=body.interval_max,
        )
    )


@app.post("/api/greet/stop")
async def greet_stop() -> dict[str, Any]:
    return await worker.stop()


@app.get("/api/greet/state")
async def greet_state() -> dict[str, Any]:
    return worker.snapshot()


@app.get("/api/greet/stream")
async def greet_stream() -> EventSourceResponse:
    """打招呼任务事件流（SSE）。"""

    async def gen():
        yield {
            "event": "state",
            "data": json.dumps(worker.snapshot(), ensure_ascii=False),
        }
        while True:
            try:
                item = await asyncio.wait_for(worker.state.events.get(), timeout=15.0)
                yield {
                    "event": item.get("event") or "message",
                    "data": json.dumps(item, ensure_ascii=False),
                }
            except asyncio.TimeoutError:
                yield {
                    "event": "ping",
                    "data": json.dumps({"ts": asyncio.get_event_loop().time()}),
                }

    return EventSourceResponse(gen())


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(FRONTEND / "index.html")


if FRONTEND.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND)), name="static")
