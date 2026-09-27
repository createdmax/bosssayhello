"""BOSS 直聘小程序业务接口（经本机通道 evaluate + service/ajax）。

搜索 / 详情 / 打招呼均在微信小程序 appservice 上下文中执行，
依赖小程序自身已登录；本模块不读写本地 session，也不注入凭证。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from .mcp_client import MiniappMCP, unwrap_evaluate

SEARCH_JOBLIST = "/wapi/zpgeek/miniapp/search/joblist.json"
JOB_DETAIL = "/wapi/zpgeek/miniapp/job/detail.json"
FRIEND_ADD = "/wapi/zpgeek/miniapp/friend/add.json"

# activeTimeDesc → 大致「几天内」
ACTIVE_DESC_DAYS: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"刚刚|在线"), 0),
    (re.compile(r"今日|今天"), 1),
    (re.compile(r"3日内|三日内"), 3),
    (re.compile(r"本周|7日内|一周内"), 7),
    (re.compile(r"半月|15日"), 15),
    (re.compile(r"本月|30日"), 30),
]


@dataclass
class JobItem:
    job_name: str = ""
    brand_name: str = ""
    boss_name: str = ""
    boss_title: str = ""
    salary: str = ""
    encrypt_job_id: str = ""
    security_id: str = ""
    lid: str = ""
    online: bool = False
    active_msg: str = ""
    active_time_desc: str = ""
    active_within_days: int | None = None
    contact: bool = False
    friend: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SearchOptions:
    query: str
    require_online: bool = False
    active_within_days: int | None = None  # None=不限; 1/3/7
    limit: int = 30  # 目标职位数（=本次打招呼数量）
    page_size: int = 15
    city: int | None = None


def _js_escape(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def _build_search_script(opts: SearchOptions) -> str:
    """在 appservice 里用 service/ajax 搜职位，可选拉详情补活跃信息。"""
    need_detail = opts.require_online or opts.active_within_days is not None
    limit = max(1, int(opts.limit))
    # 有筛选时多翻几页，凑够 limit 条；无筛选时页数 = ceil(limit/pageSize)
    page_size = max(1, int(opts.page_size))
    base_pages = (limit + page_size - 1) // page_size
    max_pages = min(40, base_pages * (5 if need_detail else 1) + (2 if need_detail else 0))
    max_pages = max(max_pages, base_pages)
    return f"""
(async () => {{
  const ajax = require('service/ajax').default || require('service/ajax');
  const query = {_js_escape(opts.query)};
  const pageSize = {page_size};
  const maxPages = {max_pages};
  const limit = {limit};
  const needDetail = {str(need_detail).lower()};
  const requireOnline = {str(opts.require_online).lower()};
  const activeWithinDays = {('null' if opts.active_within_days is None else int(opts.active_within_days))};
  const forcedCity = {('null' if opts.city is None else int(opts.city))};

  function isOnline(j) {{
    if (!j) return false;
    if (j.bossOnline === true) return true;
    if (j.bossOnline && typeof j.bossOnline === 'object') return true;
    return false;
  }}

  function activeDaysFromDesc(desc) {{
    const d = String(desc || '');
    if (!d) return null;
    if (/刚刚|在线/.test(d)) return 0;
    if (/今日|今天/.test(d)) return 1;
    if (/3日内|三日内/.test(d)) return 3;
    if (/本周|7日内|一周内/.test(d)) return 7;
    if (/半月|15日/.test(d)) return 15;
    if (/本月|30日/.test(d)) return 30;
    return null;
  }}

  function activeDaysFromTs(ts) {{
    if (!ts) return null;
    const diff = Date.now() - Number(ts);
    if (diff < 0) return 0;
    return Math.floor(diff / 86400000);
  }}

  let city = forcedCity;
  if (!city) {{
    try {{
      const pages = getCurrentPages();
      for (let i = pages.length - 1; i >= 0; i--) {{
        const p = pages[i];
        if (p && typeof p.getParams === 'function') {{
          const gp = p.getParams();
          if (gp && gp.city) {{ city = gp.city; break; }}
        }}
        const expect = (p && p.data && (p.data.expectList || [])[0]) || (p && p.data && p.data.selectExpect);
        if (expect && expect.location) {{ city = expect.location; break; }}
      }}
    }} catch (e) {{}}
  }}
  if (!city) city = 101270100;

  const collected = [];
  let hasMore = true;
  for (let page = 1; page <= maxPages && hasMore && collected.length < limit; page++) {{
    const res = await ajax({{
      url: '{SEARCH_JOBLIST}',
      method: 'GET',
      data: {{
        query,
        city: String(city),
        page,
        pageSize,
        source: 0,
        sortType: 0,
        subwayLineId: '',
        subwayStationId: '',
        districtCode: '',
        businessCode: '',
        longitude: '',
        latitude: '',
        position: '',
        expectId: '',
        expectPosition: '',
        encryptExpectId: ''
      }}
    }});
    if (!res || res.code !== 0) {{
      return {{ ok: false, code: res && res.code, msg: (res && (res.message || res.msg)) || 'search failed', jobs: collected, city, limit }};
    }}
    const zp = res.zpData || {{}};
    const list = zp.list || zp.jobList || [];
    hasMore = !!zp.hasMore;
    for (const j of list) {{
      if (collected.length >= limit) break;
      let online = isOnline(j);
      let activeDesc = j.activeMsg || '';
      let activeDays = activeDaysFromDesc(activeDesc);
      let friend = false;
      let securityId = j.securityId || '';
      let contact = !!j.contact;

      if (needDetail && j.securityId) {{
        try {{
          const detail = await ajax({{
            url: '{JOB_DETAIL}',
            method: 'GET',
            data: {{ securityId: j.securityId, lid: j.lid || '' }}
          }});
          if (detail && detail.code === 0 && detail.zpData) {{
            const boss = detail.zpData.bossBaseInfoVO || {{}};
            const rel = detail.zpData.relationInfoVO || {{}};
            if (typeof boss.bossOnline === 'boolean') online = boss.bossOnline || online;
            activeDesc = boss.activeTimeDesc || activeDesc || '';
            const fromTs = activeDaysFromTs(boss.activeTime);
            const fromDesc = activeDaysFromDesc(activeDesc);
            activeDays = fromTs != null ? fromTs : fromDesc;
            friend = !!rel.friend;
            if (boss.securityId) securityId = boss.securityId;
          }}
        }} catch (e) {{}}
      }}

      if (contact || friend) continue;
      if (requireOnline && !online) continue;
      if (activeWithinDays != null) {{
        if (activeDays == null) continue;
        if (activeDays > activeWithinDays) continue;
      }}

      collected.push({{
        jobName: j.jobName || '',
        brandName: j.brandName || '',
        bossName: j.bossName || '',
        bossTitle: j.bossTitle || '',
        salary: j.salaryDesc || '',
        encryptJobId: j.encryptJobId || '',
        securityId,
        lid: j.lid || '',
        online,
        activeMsg: j.activeMsg || '',
        activeTimeDesc: activeDesc,
        activeWithinDays: activeDays,
        contact,
        friend
      }});
    }}
  }}
  return {{ ok: true, code: 0, jobs: collected, city, total: collected.length, limit }};
}})()
""".strip()


def _build_greet_script(security_id: str, lid: str) -> str:
    return f"""
(async () => {{
  const ajax = require('service/ajax').default || require('service/ajax');
  const securityId = {_js_escape(security_id)};
  const lid = {_js_escape(lid)};
  const res = await ajax({{
    url: '{FRIEND_ADD}',
    method: 'POST',
    data: {{ securityId, lid, cid: 0 }}
  }});
  const code = res && res.code;
  const zp = (res && res.zpData) || {{}};
  const msg = (res && (res.message || res.msg)) || '';
  const text = JSON.stringify(res || {{}});
  const limit = /上限|次数已用完|沟通次数|今日沟通/.test(msg + text);
  return {{
    ok: code === 0,
    code,
    msg,
    bizCode: zp.bizCode,
    limit,
    greeting: zp.greeting || '',
    securityId: zp.securityId || securityId
  }};
}})()
""".strip()


def _parse_job(raw: dict[str, Any]) -> JobItem:
    return JobItem(
        job_name=raw.get("jobName") or "",
        brand_name=raw.get("brandName") or "",
        boss_name=raw.get("bossName") or "",
        boss_title=raw.get("bossTitle") or "",
        salary=raw.get("salary") or "",
        encrypt_job_id=raw.get("encryptJobId") or "",
        security_id=raw.get("securityId") or "",
        lid=raw.get("lid") or "",
        online=bool(raw.get("online")),
        active_msg=raw.get("activeMsg") or "",
        active_time_desc=raw.get("activeTimeDesc") or "",
        active_within_days=raw.get("activeWithinDays"),
        contact=bool(raw.get("contact")),
        friend=bool(raw.get("friend")),
    )


class BossAPI:
    def __init__(self, mcp: MiniappMCP | None = None):
        self.mcp = mcp or MiniappMCP()

    async def search(self, opts: SearchOptions) -> dict[str, Any]:
        raw = unwrap_evaluate(await self.mcp.evaluate(_build_search_script(opts)))
        if not isinstance(raw, dict):
            return {"ok": False, "msg": f"unexpected evaluate result: {raw!r}", "jobs": []}
        jobs = [_parse_job(j) for j in (raw.get("jobs") or []) if isinstance(j, dict)]
        # 跳过已沟通
        jobs = [j for j in jobs if not j.contact and not j.friend]
        # 再截断一次，保证与 limit 一致
        jobs = jobs[: max(1, int(opts.limit))]
        code = raw.get("code")
        msg = raw.get("msg") or ""
        if code in (35, 36):
            msg = (
                (msg or "您的IP地址存在异常行为")
                + "。请到微信 BOSS 小程序内完成「验证」解锁后再搜索。"
            )
        return {
            "ok": bool(raw.get("ok")),
            "code": code,
            "msg": msg,
            "city": raw.get("city"),
            "limit": opts.limit,
            "total": len(jobs),
            "jobs": [j.to_dict() for j in jobs],
            "filter_note": "在线/活跃为客户端筛选（列表+详情 activeTimeDesc/activeTime）",
            "need_clear_risk": code in (35, 36),
        }

    async def greet(self, security_id: str, lid: str = "") -> dict[str, Any]:
        raw = unwrap_evaluate(
            await self.mcp.evaluate(_build_greet_script(security_id, lid or ""))
        )
        if not isinstance(raw, dict):
            return {"ok": False, "msg": f"unexpected evaluate result: {raw!r}"}
        return raw
