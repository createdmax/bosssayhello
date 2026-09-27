(() => {
  const $ = (id) => document.getElementById(id);

  const els = {
    status: $("status"),
    authInfo: $("authInfo"),
    loginPanel: $("loginPanel"),
    btnLogout: $("btnLogout"),
    btnQrStart: $("btnQrStart"),
    btnQrRefresh: $("btnQrRefresh"),
    qrImage: $("qrImage"),
    qrPlaceholder: $("qrPlaceholder"),
    qrSuccess: $("qrSuccess"),
    qrStatus: $("qrStatus"),
    qrSteps: $("qrSteps"),
    query: $("query"),
    requireOnline: $("requireOnline"),
    activeDays: $("activeDays"),
    count: $("count"),
    intervalMin: $("intervalMin"),
    intervalMax: $("intervalMax"),
    btnSearch: $("btnSearch"),
    btnStart: $("btnStart"),
    btnStop: $("btnStop"),
    jobBody: $("jobBody"),
    jobCount: $("jobCount"),
    log: $("log"),
  };

  let qrUuid = "";
  let qrPolling = false;
  let qrAbort = false;

  function formPayload() {
    const active = els.activeDays.value;
    return {
      query: els.query.value.trim(),
      require_online: els.requireOnline.checked,
      active_within_days: active === "" ? null : Number(active),
      count: Number(els.count.value) || 10,
      interval_min: Number(els.intervalMin.value) || 8,
      interval_max: Number(els.intervalMax.value) || 15,
    };
  }

  function log(line, cls = "") {
    const row = document.createElement("div");
    if (cls) row.className = cls;
    const ts = new Date().toLocaleTimeString();
    row.textContent = `[${ts}] ${line}`;
    els.log.appendChild(row);
    els.log.scrollTop = els.log.scrollHeight;
  }

  function setStatus(ok, text) {
    els.status.textContent = text;
    els.status.className = `status ${ok === true ? "status-ok" : ok === false ? "status-bad" : "status-unknown"}`;
  }

  function setRunning(running) {
    els.btnStart.disabled = running;
    els.btnStop.disabled = !running;
    els.btnSearch.disabled = running;
  }

  function setQrStep(step) {
    if (!els.qrSteps) return;
    const order = ["idle", "waiting", "done"];
    const idx = order.indexOf(step);
    els.qrSteps.querySelectorAll("li").forEach((li, i) => {
      li.classList.toggle("is-active", i === idx);
      li.classList.toggle("is-done", i < idx || (step === "done" && i === idx));
    });
  }

  function showQrIdle() {
    if (els.qrImage) {
      els.qrImage.hidden = true;
      els.qrImage.removeAttribute("src");
    }
    if (els.qrPlaceholder) els.qrPlaceholder.hidden = false;
    if (els.qrSuccess) els.qrSuccess.hidden = true;
    setQrStep("idle");
  }

  function showQrImage(url) {
    if (els.qrPlaceholder) els.qrPlaceholder.hidden = true;
    if (els.qrSuccess) els.qrSuccess.hidden = true;
    if (els.qrImage) {
      els.qrImage.hidden = false;
      els.qrImage.src = url;
    }
    setQrStep("waiting");
  }

  function showQrSuccess() {
    if (els.qrImage) els.qrImage.hidden = true;
    if (els.qrPlaceholder) els.qrPlaceholder.hidden = true;
    if (els.qrSuccess) els.qrSuccess.hidden = false;
    setQrStep("done");
  }

  function renderAuth(auth) {
    const loggedIn = !!(auth && auth.logged_in);
    if (els.loginPanel) {
      els.loginPanel.classList.toggle("is-logged-in", loggedIn);
    }
    if (!loggedIn) {
      els.authInfo.textContent = "未登录";
      els.authInfo.className = "auth-chip muted";
      return;
    }
    const name = auth.show_name || "已登录";
    const src = auth.source === "qr" ? "微信扫码" : auth.source || "";
    els.authInfo.textContent = src ? `${name} · ${src}` : name;
    els.authInfo.className = "auth-chip ok";
  }

  function renderJobs(jobs) {
    els.jobCount.textContent = jobs && jobs.length ? `(${jobs.length})` : "";
    if (!jobs || !jobs.length) {
      els.jobBody.innerHTML = `<tr><td colspan="7" class="empty">无匹配职位</td></tr>`;
      return;
    }
    els.jobBody.innerHTML = jobs
      .map((j) => {
        const online = j.online
          ? `<span class="tag tag-on">在线</span>`
          : `<span class="tag tag-off">离线</span>`;
        const active = j.active_time_desc || j.active_msg || "—";
        const contacted = j.contact || j.friend ? "是" : "否";
        return `<tr>
          <td>${esc(j.job_name)}</td>
          <td>${esc(j.brand_name)}</td>
          <td>${esc(j.boss_name)}${j.boss_title ? `<div class="muted">${esc(j.boss_title)}</div>` : ""}</td>
          <td>${esc(j.salary)}</td>
          <td>${online}</td>
          <td>${esc(active)}</td>
          <td>${contacted}</td>
        </tr>`;
      })
      .join("");
  }

  function esc(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  async function refreshStatus() {
    try {
      const res = await fetch("/api/status");
      const data = await res.json();
      if (data.ok) {
        setStatus(true, `${data.name || "小程序通道已连接"} · ${data.route || ""}`);
      } else {
        setStatus(false, data.error || "小程序通道未连接");
      }
      renderAuth(data.auth);
      if (data.auth && data.auth.logged_in && !qrPolling) {
        showQrSuccess();
        setQrStatus("本地已登录", "ok");
      }
      if (data.worker) setRunning(!!data.worker.running);
    } catch (e) {
      setStatus(false, "后端不可用");
    }
  }

  function setQrStatus(text, cls) {
    if (!els.qrStatus) return;
    els.qrStatus.textContent = text;
    els.qrStatus.className = `qr-status ${cls || "muted"}`;
  }

  function stopQrPoll() {
    qrAbort = true;
    qrPolling = false;
  }

  async function pollQrLoop() {
    if (qrPolling) return;
    qrPolling = true;
    qrAbort = false;
    while (!qrAbort && qrUuid) {
      try {
        const res = await fetch(`/api/auth/qr/poll?uuid=${encodeURIComponent(qrUuid)}`);
        const data = await res.json();
        if (data.status === "confirmed" && data.ok) {
          showQrSuccess();
          setQrStatus("登录成功", "ok");
          log(data.msg || "扫码登录成功", "e-success");
          renderAuth(data.session);
          if (els.btnQrRefresh) els.btnQrRefresh.disabled = false;
          stopQrPoll();
          refreshStatus();
          return;
        }
        if (data.status === "expired" || (data.ok === false && data.status === "error")) {
          setQrStatus(data.msg || "二维码失效，请刷新", "warn");
          log(data.msg || "扫码失败", "e-fail");
          if (els.btnQrRefresh) els.btnQrRefresh.disabled = false;
          stopQrPoll();
          return;
        }
        setQrStatus(data.msg || "等待扫码…", "muted");
        setQrStep("waiting");
      } catch (e) {
        setQrStatus("网络异常，重试中…", "muted");
      }
      await new Promise((r) => setTimeout(r, 800));
    }
    qrPolling = false;
  }

  async function startQrLogin() {
    stopQrPoll();
    qrUuid = "";
    if (els.btnQrStart) els.btnQrStart.disabled = true;
    if (els.btnQrRefresh) els.btnQrRefresh.disabled = true;
    setQrStatus("正在获取二维码…", "muted");
    setQrStep("idle");
    log("正在获取微信登录二维码…");
    try {
      const res = await fetch("/api/auth/qr/start", { method: "POST" });
      const data = await res.json();
      if (!data.ok) {
        setQrStatus(data.msg || "获取失败", "warn");
        log(data.msg || "获取二维码失败", "e-fail");
        showQrIdle();
        return;
      }
      qrUuid = data.uuid;
      showQrImage(data.image_url);
      setQrStatus("请使用微信扫一扫，确认登录", "muted");
      log("二维码已生成，请用微信扫码", "e-success");
      if (els.btnQrRefresh) els.btnQrRefresh.disabled = false;
      pollQrLoop();
    } catch (e) {
      setQrStatus(String(e.message || e), "warn");
      log(String(e.message || e), "e-fail");
      showQrIdle();
    } finally {
      if (els.btnQrStart) els.btnQrStart.disabled = false;
    }
  }

  async function doLogout() {
    stopQrPoll();
    const res = await fetch("/api/auth/logout", { method: "POST" });
    const data = await res.json();
    log(data.msg || "已退出");
    renderAuth(data.session);
    showQrIdle();
    setQrStatus("尚未获取二维码", "muted");
  }

  async function doSearch() {
    const p = formPayload();
    if (!p.query) {
      log("请输入关键字", "e-fail");
      return;
    }
    els.btnSearch.disabled = true;
    log(`搜索「${p.query}」，目标 ${p.count} 个…`);
    try {
      const res = await fetch("/api/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query: p.query,
          require_online: p.require_online,
          active_within_days: p.active_within_days,
          count: p.count,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        log(data.detail || "搜索失败", "e-fail");
        renderJobs([]);
        return;
      }
      if (!data.ok) {
        log(`搜索失败: ${data.msg || data.code}`, "e-fail");
        if (data.need_clear_risk || data.code === 35 || data.code === 36) {
          log("请先在微信小程序内完成「验证」解锁，再搜索", "e-fail");
        }
        renderJobs([]);
        return;
      }
      renderJobs(data.jobs || []);
      log(
        `找到 ${data.total}/${data.limit || p.count} 个（城市 ${data.city}）${data.filter_note ? " · " + data.filter_note : ""}`,
        "e-success"
      );
    } catch (e) {
      log(String(e), "e-error");
    } finally {
      els.btnSearch.disabled = false;
      refreshStatus();
    }
  }

  async function doStart() {
    const p = formPayload();
    if (!p.query) {
      log("请输入关键字", "e-fail");
      return;
    }
    if (p.interval_max < p.interval_min) {
      log("间隔上限不能小于下限", "e-fail");
      return;
    }
    const res = await fetch("/api/greet/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(p),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      log(data.detail || "启动失败", "e-fail");
      return;
    }
    if (!data.ok) {
      log(data.msg || "启动失败", "e-fail");
      return;
    }
    setRunning(true);
    log(data.msg || "已开始", "e-started");
  }

  async function doStop() {
    const res = await fetch("/api/greet/stop", { method: "POST" });
    const data = await res.json();
    log(data.msg || "已请求停止");
  }

  function connectStream() {
    const es = new EventSource("/api/greet/stream");
    es.addEventListener("state", (ev) => {
      try {
        const s = JSON.parse(ev.data);
        setRunning(!!s.running);
      } catch (_) {}
    });
    [
      "started",
      "search_done",
      "greeting",
      "success",
      "fail",
      "skip",
      "wait",
      "stopping",
      "stopped",
      "finished",
      "error",
      "quota_exceeded",
      "limit_reached",
    ].forEach((name) => {
      es.addEventListener(name, (ev) => {
        try {
          handleEvent(name, JSON.parse(ev.data));
        } catch (_) {
          log(ev.data, `e-${name}`);
        }
      });
    });
  }

  function handleEvent(name, data) {
    const msg = data.message || data.reason || name;
    if (name === "search_done" && data.jobs) {
      renderJobs(data.jobs);
      log(`${msg}`, "e-started");
      return;
    }
    if (name === "wait") {
      log(`等待 ${data.seconds}s`);
      return;
    }
    if (name === "finished" || name === "stopped" || name === "quota_exceeded" || name === "limit_reached") {
      setRunning(false);
    }
    if (name === "started") setRunning(true);
    log(msg, `e-${name}`);
  }

  if (els.btnLogout) els.btnLogout.addEventListener("click", doLogout);
  if (els.btnQrStart) els.btnQrStart.addEventListener("click", startQrLogin);
  if (els.btnQrRefresh) els.btnQrRefresh.addEventListener("click", startQrLogin);
  els.btnSearch.addEventListener("click", doSearch);
  els.btnStart.addEventListener("click", doStart);
  els.btnStop.addEventListener("click", doStop);
  els.query.addEventListener("keydown", (e) => {
    if (e.key === "Enter") doSearch();
  });

  showQrIdle();
  refreshStatus();
  setInterval(refreshStatus, 8000);
  connectStream();
})();
