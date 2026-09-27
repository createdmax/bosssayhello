"""本地会话存储：扫码登录成功后写入 data/session.json。

只持久化本工具侧凭证摘要；搜索/打招呼仍使用微信小程序内登录态。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SESSION_PATH = DATA_DIR / "session.json"

_lock = threading.Lock()


def _ensure_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_session() -> dict[str, Any]:
    with _lock:
        if not SESSION_PATH.exists():
            return {}
        try:
            return json.loads(SESSION_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}


def save_session(data: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        _ensure_dir()
        current = {}
        if SESSION_PATH.exists():
            try:
                current = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                current = {}
        current.update(data)
        current["updated_at"] = time.time()
        SESSION_PATH.write_text(
            json.dumps(current, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return current


def clear_session() -> None:
    with _lock:
        if SESSION_PATH.exists():
            SESSION_PATH.unlink()


def session_public(sess: dict[str, Any] | None = None) -> dict[str, Any]:
    """返回可给前端看的会话摘要（不含完整 token）。"""
    s = sess if sess is not None else load_session()
    if not s:
        return {"logged_in": False}
    wt2 = s.get("wt2") or s.get("wt") or ""
    mpt = s.get("mpt") or ""
    return {
        "logged_in": bool(wt2 or mpt),
        "source": s.get("source") or "",
        "show_name": s.get("show_name") or s.get("showName") or "",
        "phone": s.get("phone") or "",
        "identity": s.get("identity", 0),
        "has_wt2": bool(wt2),
        "has_mpt": bool(mpt),
        "updated_at": s.get("updated_at"),
    }
