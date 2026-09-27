"""本地登录态摘要与小程序登录探测。

本模块不负责发短信、不负责把网页凭证写入小程序。
- status / logout：读写 data/session.json（通常由扫码登录写入）
- probe_miniapp_login：经本机小程序通道只读探测是否已登录，不写本地 session
"""

from __future__ import annotations

from typing import Any

from .mcp_client import MiniappMCP, unwrap_evaluate
from .session_store import clear_session, session_public

_PROBE_SCRIPT = """
(async () => {
  const login = require('common/js/login').default;
  const d = login.data || {};
  return {
    ok: !!(d.wt2 || d.wt || d.mpt),
    showName: d.showName || '',
    hasMpt: !!d.mpt,
    hasWt2: !!(d.wt2 || d.wt)
  };
})()
""".strip()


class AuthService:
    """会话状态查询 / 退出，以及搜索前的小程序登录探测。"""

    def __init__(self, mcp: MiniappMCP | None = None):
        self.mcp = mcp or MiniappMCP()

    def status(self) -> dict[str, Any]:
        """返回可给前端展示的本地会话摘要（不含完整 token）。"""
        return session_public()

    def logout(self) -> dict[str, Any]:
        """清除本地 session.json。"""
        clear_session()
        return {"ok": True, "msg": "已退出本地登录", "session": session_public()}

    async def probe_miniapp_login(self) -> dict[str, Any]:
        """探测微信小程序当前是否有可用登录态（只读，不写 session、不 inject）。"""
        try:
            raw = unwrap_evaluate(await self.mcp.evaluate(_PROBE_SCRIPT))
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "msg": f"无法连接小程序（请确认本机小程序通道可用，且微信中已打开 BOSS 小程序）: {exc}",
            }
        if not isinstance(raw, dict) or not raw.get("ok"):
            return {
                "ok": False,
                "msg": "微信小程序未登录：请在微信中打开 BOSS 直聘并完成登录后再搜索",
            }
        return {
            "ok": True,
            "msg": "小程序已登录",
            "show_name": raw.get("showName") or "",
            "has_mpt": bool(raw.get("hasMpt")),
            "has_wt2": bool(raw.get("hasWt2")),
        }
