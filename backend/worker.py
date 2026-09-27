"""后台打招呼任务：搜索筛选后逐条 friend/add，并通过 SSE 推送进度。"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any

from .boss_api import BossAPI, SearchOptions
from .mcp_client import MiniappMCP

logger = logging.getLogger(__name__)


@dataclass
class GreetConfig:
    query: str
    require_online: bool = False
    active_within_days: int | None = None
    count: int = 30  # 本次要打的招呼数量（=搜索目标职位数）
    interval_min: float = 3.0
    interval_max: float = 8.0


@dataclass
class WorkerState:
    running: bool = False
    stop_requested: bool = False
    started_at: float | None = None
    config: GreetConfig | None = None
    greeted: int = 0
    skipped: int = 0
    failed: int = 0
    last_error: str = ""
    events: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    task: asyncio.Task | None = None
    greeted_ids: set[str] = field(default_factory=set)


class GreetWorker:
    def __init__(self, mcp: MiniappMCP | None = None):
        self.mcp = mcp or MiniappMCP()
        self.api = BossAPI(self.mcp)
        self.state = WorkerState()
        self._lock = asyncio.Lock()

    def snapshot(self) -> dict[str, Any]:
        s = self.state
        return {
            "running": s.running,
            "stop_requested": s.stop_requested,
            "started_at": s.started_at,
            "greeted": s.greeted,
            "skipped": s.skipped,
            "failed": s.failed,
            "last_error": s.last_error,
            "config": None
            if not s.config
            else {
                "query": s.config.query,
                "require_online": s.config.require_online,
                "active_within_days": s.config.active_within_days,
                "count": s.config.count,
                "interval_min": s.config.interval_min,
                "interval_max": s.config.interval_max,
            },
        }

    async def emit(self, event: str, **payload: Any) -> None:
        await self.state.events.put({"event": event, "ts": time.time(), **payload})

    async def start(self, config: GreetConfig) -> dict[str, Any]:
        async with self._lock:
            if self.state.running:
                return {"ok": False, "msg": "已有任务在运行"}
            self.state = WorkerState(
                running=True,
                stop_requested=False,
                started_at=time.time(),
                config=config,
            )
            self.state.task = asyncio.create_task(self._run(config))
            return {"ok": True, "msg": "已开始"}

    async def stop(self) -> dict[str, Any]:
        async with self._lock:
            if not self.state.running:
                return {"ok": False, "msg": "当前没有运行中的任务"}
            self.state.stop_requested = True
            await self.emit("stopping", message="正在停止…")
            return {"ok": True, "msg": "已请求停止"}

    async def _run(self, config: GreetConfig) -> None:
        try:
            await self.emit(
                "started",
                message=f"开始搜索「{config.query}」，本次目标 {config.count} 个打招呼",
                config=self.snapshot()["config"],
            )
            result = await self.api.search(
                SearchOptions(
                    query=config.query,
                    require_online=config.require_online,
                    active_within_days=config.active_within_days,
                    limit=config.count,
                )
            )
            if not result.get("ok"):
                msg = result.get("msg") or "搜索失败"
                self.state.last_error = msg
                await self.emit("error", message=msg, code=result.get("code"))
                return

            jobs = (result.get("jobs") or [])[: config.count]
            await self.emit(
                "search_done",
                message=f"已凑齐 {len(jobs)}/{config.count} 个待打招呼职位",
                total=len(jobs),
                target=config.count,
                city=result.get("city"),
                jobs=jobs,
            )

            for idx, job in enumerate(jobs):
                if self.state.stop_requested:
                    await self.emit("stopped", message="用户停止")
                    break
                if self.state.greeted >= config.count:
                    await self.emit(
                        "limit_reached",
                        message=f"已完成本次 {config.count} 个打招呼",
                    )
                    break

                job_id = job.get("encrypt_job_id") or job.get("security_id") or ""
                if job_id and job_id in self.state.greeted_ids:
                    self.state.skipped += 1
                    await self.emit(
                        "skip",
                        reason="duplicate",
                        job=job,
                        index=idx,
                    )
                    continue
                if job.get("contact") or job.get("friend"):
                    self.state.skipped += 1
                    await self.emit("skip", reason="already_contacted", job=job, index=idx)
                    continue

                sec = job.get("security_id") or ""
                if not sec:
                    self.state.skipped += 1
                    await self.emit("skip", reason="no_security_id", job=job, index=idx)
                    continue

                await self.emit(
                    "greeting",
                    message=f"正在打招呼：{job.get('job_name')} / {job.get('brand_name')}",
                    job=job,
                    index=idx,
                )
                try:
                    resp = await self.api.greet(sec, job.get("lid") or "")
                except Exception as exc:  # noqa: BLE001
                    self.state.failed += 1
                    self.state.last_error = str(exc)
                    await self.emit("fail", message=str(exc), job=job, index=idx)
                    continue

                if resp.get("limit"):
                    self.state.failed += 1
                    await self.emit(
                        "quota_exceeded",
                        message=resp.get("msg") or "今日沟通已达上限",
                        job=job,
                        resp=resp,
                    )
                    break

                if resp.get("ok"):
                    self.state.greeted += 1
                    if job_id:
                        self.state.greeted_ids.add(job_id)
                    await self.emit(
                        "success",
                        message=f"成功：{job.get('job_name')}",
                        job=job,
                        resp=resp,
                        greeted=self.state.greeted,
                    )
                else:
                    self.state.failed += 1
                    msg = resp.get("msg") or f"code={resp.get('code')}"
                    await self.emit(
                        "fail",
                        message=msg,
                        job=job,
                        resp=resp,
                        index=idx,
                    )
                    if resp.get("code") in (1, 7) and "上限" in msg:
                        break

                delay = random.uniform(config.interval_min, config.interval_max)
                await self.emit("wait", seconds=round(delay, 1))
                # 可中断等待
                end = time.time() + delay
                while time.time() < end:
                    if self.state.stop_requested:
                        break
                    await asyncio.sleep(min(0.3, end - time.time()))

            await self.emit(
                "finished",
                message="任务结束",
                stats=self.snapshot(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("greet worker failed")
            self.state.last_error = str(exc)
            await self.emit("error", message=str(exc))
        finally:
            self.state.running = False
            self.state.stop_requested = False
