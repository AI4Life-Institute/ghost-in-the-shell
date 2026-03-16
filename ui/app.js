(() => {
  // src/data/agents.ts
  var AGENTS = {
    browser: {
      profiles: [
        {
          id: "personal-chrome",
          label: "Personal Chrome",
          agents: [
            {
              id: "nash-reporter",
              name: "Nash-AI Reporter",
              status: "running",
              sub: "step 8 of ~12 \xB7 43s",
              type: "Browser Agent",
              profile: "Personal Chrome",
              detail: {
                running: true,
                steps: 8,
                totalSteps: 12,
                elapsed: "43s",
                log: [
                  { ico: "\u{1F9ED}", action: "Navigate", desc: "nash-ai.cn/login", out: "\u2713 Already logged in (session active)", ts: "2:43:00", done: true },
                  { ico: "\u{1F4F8}", action: "Snapshot", desc: "found 47 reports", out: "", ts: "2:43:01", done: true },
                  { ico: "\u2B07", action: "Download", desc: "gs_q2_2024.pdf \xB7 2.4MB\u2026", out: "", ts: "2:43:10", done: false, pending: true }
                ],
                hitl: null
              }
            },
            {
              id: "hn-digest",
              name: "HN Daily Digest",
              status: "done",
              sub: "ran 2h ago",
              type: "Browser Agent",
              profile: "Personal Chrome",
              detail: {
                running: false,
                steps: 6,
                totalSteps: 6,
                elapsed: "18s",
                log: [
                  { ico: "\u{1F9ED}", action: "Navigate", desc: "news.ycombinator.com", out: "", ts: "12:01:00", done: true },
                  { ico: "\u{1F4F8}", action: "Snapshot", desc: "found 30 stories", out: "", ts: "12:01:01", done: true },
                  { ico: "\u{1F4BE}", action: "Save to DB", desc: "inserted 10 top links", out: "\u2192 saved", ts: "12:01:04", done: true }
                ],
                hitl: null
              }
            }
          ]
        },
        {
          id: "work-chrome",
          label: "Work Chrome",
          agents: [
            {
              id: "fanvue-cloner",
              name: "FanVue Cloner",
              status: "running",
              sub: "step 3 of ~8 \xB7 12s",
              type: "Browser Agent",
              profile: "Work Chrome",
              detail: {
                running: true,
                steps: 3,
                totalSteps: 8,
                elapsed: "12s",
                log: [
                  { ico: "\u{1F9ED}", action: "Navigate", desc: "fanvue.com/login", out: "\u2713 Logged in", ts: "23:01:00", done: true },
                  { ico: "\u{1F4F8}", action: "Snapshot", desc: "found 24 creator profiles", out: "", ts: "23:01:02", done: true },
                  { ico: "\u2B07", action: "Scrape", desc: "extracting profile metadata\u2026", out: "", ts: "23:01:10", done: false, pending: true }
                ],
                hitl: null
              }
            }
          ]
        },
        {
          id: "research-chrome",
          label: "Research",
          agents: [
            {
              id: "market-crawler",
              name: "Market Crawler",
              status: "done",
              sub: "ran 30m ago \xB7 6 sources",
              type: "Browser Agent",
              profile: "Research",
              detail: {
                running: false,
                steps: 9,
                totalSteps: 9,
                elapsed: "34s",
                log: [
                  { ico: "\u{1F9ED}", action: "Navigate", desc: "bloomberg.com/markets", out: "", ts: "22:30:00", done: true },
                  { ico: "\u26A1", action: "Extract", desc: "pulled 14 market headlines", out: "\u2192 saved", ts: "22:30:08", done: true },
                  { ico: "\u{1F4BE}", action: "Save to DB", desc: "INSERT 14 rows \u2192 market_news", out: "\u2192 ok", ts: "22:30:10", done: true }
                ],
                hitl: null
              }
            }
          ]
        }
      ]
    },
    loop: [
      {
        id: "btc-monitor",
        name: "BTC Price Monitor",
        status: "running",
        sub: "Every 60 min \xB7 next 4m",
        type: "Loop Agent",
        detail: {
          running: true,
          steps: 3,
          totalSteps: 3,
          elapsed: "2s",
          log: [
            { ico: "\u26A1", action: "Evaluate", desc: "GET /api/btc/price", out: "\u2192 $67,432.18", ts: "14:00:00", done: true },
            { ico: "\u{1F4BE}", action: "Save", desc: "INSERT btc_price", out: "\u2192 ok", ts: "14:00:01", done: true }
          ],
          hitl: null
        }
      },
      {
        id: "hn-digest-loop",
        name: "HN Digest",
        status: "done",
        sub: "Daily 9am \xB7 ran 2h ago",
        type: "Loop Agent",
        detail: {
          running: false,
          steps: 6,
          totalSteps: 6,
          elapsed: "18s",
          log: [
            { ico: "\u{1F9ED}", action: "Navigate", desc: "news.ycombinator.com", out: "", ts: "09:00:00", done: true },
            { ico: "\u{1F4BE}", action: "Save", desc: "10 links saved", out: "\u2192 ok", ts: "09:00:04", done: true }
          ],
          hitl: null
        }
      },
      {
        id: "weather-reporter",
        name: "Weather Reporter",
        status: "running",
        autoRepaired: true,
        sub: "Every 4h \xB7 next 2h",
        type: "Loop Agent",
        detail: {
          running: true,
          steps: 2,
          totalSteps: 4,
          elapsed: "1s",
          log: [
            { ico: "\u26A1", action: "Evaluate", desc: "GET /api/weather/today", out: "\u2192 18\xB0C, partly cloudy", ts: "16:00:00", done: true },
            { ico: "\u{1F4BE}", action: "Save", desc: "INSERT weather_log", out: "\u2192 ok", ts: "16:00:01", done: true }
          ],
          hitl: null
        }
      },
      {
        id: "discord-digest",
        name: "Discord Digest",
        status: "done",
        sub: "Every 6h \xB7 ran 1h ago",
        type: "Loop Agent",
        detail: {
          running: false,
          steps: 4,
          totalSteps: 4,
          elapsed: "8s",
          log: [
            { ico: "\u{1F4E1}", action: "Fetch", desc: "read #dev last 200 messages", out: "\u2192 47 msgs", ts: "22:00:00", done: true },
            { ico: "\u{1F916}", action: "Summarize", desc: "Claude summarized channel activity", out: "\u2192 3 action items", ts: "22:00:05", done: true },
            { ico: "\u{1F4BE}", action: "Save", desc: "INSERT discord_digest", out: "\u2192 ok", ts: "22:00:08", done: true }
          ],
          hitl: null
        }
      }
    ],
    reactive: [
      {
        id: "discord-webhook",
        name: "Discord Webhook",
        status: "listening",
        sub: "Trigger: Discord \xB7 #dev \xB7 \u25CF Connected",
        type: "Reactive Agent",
        detail: {
          running: false,
          steps: 0,
          totalSteps: 0,
          elapsed: "\u2014",
          log: [],
          hitl: null
        }
      },
      {
        id: "notion-trigger",
        name: "Notion Page Watcher",
        status: "waiting",
        sub: "Trigger: Notion webhook \xB7 \u26A0 Needs approval",
        type: "Reactive Agent",
        detail: {
          running: false,
          steps: 1,
          totalSteps: 3,
          elapsed: "4s",
          log: [
            { ico: "\u{1F514}", action: "Triggered", desc: 'Page "Week 12" updated in Notion', out: "", ts: "14:45:10", done: true }
          ],
          hitl: { msg: 'Export "Week 12" page as PDF and save to Data?', pending: true }
        }
      },
      {
        id: "github-pr-watcher",
        name: "GitHub PR Watcher",
        status: "listening",
        sub: "Trigger: GitHub webhook \xB7 ghost-in-the-shell \xB7 \u25CF Active",
        type: "Reactive Agent",
        detail: {
          running: false,
          steps: 0,
          totalSteps: 0,
          elapsed: "\u2014",
          log: [],
          hitl: null
        }
      }
    ]
  };

  // src/utils.ts
  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  function fmt(t) {
    t = t.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, l, c) => `<pre><code>${esc(c.trim())}</code><button class="ccbtn" onclick="cpc(this)">Copy</button></pre>`);
    t = t.replace(/`([^`]+)`/g, "<code>$1</code>");
    return t.split("\n").map((l) => `<p>${l || "&nbsp;"}</p>`).join("");
  }

  // src/state.ts
  var state = {
    activeSessId: null,
    sessTerminal: null,
    allSessions: [],
    tmuxSession: "ghost",
    panes: [
      { channelId: null, terminal: null, fitAddon: null, ro: null },
      { channelId: null, terminal: null, fitAddon: null, ro: null }
    ],
    activePaneIdx: 0,
    devMode: false,
    sessPickerTargetPane: 0,
    curMode: "build",
    curAgentId: "nash-reporter",
    curProfileId: "personal-chrome",
    curSkill: "market",
    curTableId: "btc_prices",
    sortCol: null,
    sortDir: 1,
    filterText: "",
    slashMenuOpen: false,
    toastTimer: null,
    skillRunExpanded: {},
    runnerAgents: {},
    skillDefs: {}
  };

  // src/views/mode.ts
  function setMode(mode) {
    state.curMode = mode;
    document.querySelectorAll(".mode").forEach((m) => m.classList.remove("on"));
    const modeEl = document.querySelector(`.mode[data-mode="${mode}"]`);
    if (modeEl)
      modeEl.classList.add("on");
    document.querySelectorAll(".view").forEach((v) => v.classList.remove("on"));
    const viewEl = document.getElementById("view-" + mode);
    if (viewEl)
      viewEl.classList.add("on");
    updateAgentBadge();
  }
  function updateAgentBadge() {
    const badge = document.getElementById("agents-badge");
    if (!badge)
      return;
    const running = countRunningAgents();
    badge.textContent = running > 0 ? `[${running}]` : "";
  }
  function countRunningAgents() {
    let n = 0;
    AGENTS.browser.profiles.forEach((p) => p.agents.forEach((a) => {
      if (a.status === "running")
        n++;
    }));
    AGENTS.loop.forEach((a) => {
      if (a.status === "running")
        n++;
    });
    AGENTS.reactive.forEach((a) => {
      if (a.status === "listening" || a.status === "running")
        n++;
    });
    return n;
  }
  function countWaitingAgents() {
    let n = 0;
    AGENTS.browser.profiles.forEach((p) => p.agents.forEach((a) => {
      if (a.status === "waiting")
        n++;
    }));
    AGENTS.loop.forEach((a) => {
      if (a.status === "waiting")
        n++;
    });
    AGENTS.reactive.forEach((a) => {
      if (a.status === "waiting")
        n++;
    });
    return n;
  }
  function updateAgentsWarnBadge() {
    const warnEl = document.querySelector('.mode[data-mode="agents"] .mode-badge.warn');
    if (warnEl)
      warnEl.style.display = countWaitingAgents() > 0 ? "" : "none";
  }

  // src/views/build.ts
  function _sessLabel(sess) {
    if (sess.window_name && sess.window_name !== "1" && !/^\d+$/.test(sess.window_name))
      return sess.window_name;
    return sess.work_dir ? sess.work_dir.split("/").pop() : sess.coding_cli || "session";
  }
  function _shortDir(work_dir) {
    if (!work_dir)
      return "~";
    let p = work_dir.replace(/^\/Users\/[^/]+/, "~");
    p = p.replace(/^\/Volumes\/[^/]+/, "");
    if (!p)
      p = "/";
    return p;
  }
  function _projectName(work_dir) {
    if (!work_dir)
      return "~";
    const parts = work_dir.split("/").filter(Boolean);
    if (parts.length === 0)
      return "~";
    return parts.length >= 2 ? parts.slice(-2).join("/") : parts[0];
  }
  function _groupDir(work_dir) {
    if (!work_dir)
      return "~";
    const p = _shortDir(work_dir);
    const parts = p.replace(/^~\/?/, "").split("/").filter(Boolean);
    const key = parts.slice(0, 3).join("/");
    return key || p;
  }
  function buildSessionTree(sessions) {
    const alive = sessions.filter((s) => s.alive !== false);
    const dead = sessions.filter((s) => s.alive === false);
    alive.sort((a, b) => {
      if (a.platform === "desktop" && b.platform !== "desktop")
        return -1;
      if (b.platform === "desktop" && a.platform !== "desktop")
        return 1;
      return (b.created_at || "").localeCompare(a.created_at || "");
    });
    const byDir = /* @__PURE__ */ new Map();
    const channelIds = new Set(alive.map((s) => s.channel_id));
    alive.forEach((sess) => {
      const dir = _groupDir(sess.work_dir);
      if (!byDir.has(dir))
        byDir.set(dir, { roots: [], childMap: /* @__PURE__ */ new Map() });
      const group = byDir.get(dir);
      if (sess.parent_channel_id && channelIds.has(sess.parent_channel_id)) {
        if (!group.childMap.has(sess.parent_channel_id))
          group.childMap.set(sess.parent_channel_id, []);
        group.childMap.get(sess.parent_channel_id).push(sess);
      } else {
        group.roots.push(sess);
      }
    });
    return { byDir, dead };
  }
  function renderSessionTabs() {
    const bar = document.getElementById("ws-tabbar");
    if (!bar)
      return;
    const addBtn = bar.querySelector(".ws-tab-add");
    const rightEl = bar.querySelector(".ws-tabbar-right");
    bar.innerHTML = "";
    let lastDir = null;
    state.panes.forEach((pane, idx) => {
      const sess = pane.channelId ? state.allSessions.find((s) => s.channel_id === pane.channelId) : null;
      const dir = sess ? _groupDir(sess.work_dir) : null;
      if (dir && lastDir !== null && dir !== lastDir) {
        const div = document.createElement("div");
        div.className = "ws-tab-divider";
        bar.appendChild(div);
      }
      if (dir)
        lastDir = dir;
      const tab = document.createElement("div");
      tab.className = "ws-tab" + (idx === state.activePaneIdx ? " on" : "");
      if (sess) {
        const status = _paneStatus(pane);
        const dotCls = status === "active" ? "g" : status === "busy" ? "" : "d";
        const deadCls = sess.alive === false ? " dead" : status === "busy" ? " busy" : "";
        tab.className += deadCls;
        const platIco = sess.platform === "desktop" ? "\u{1F5A5}" : "\u{1F4AC}";
        tab.innerHTML = `<div class="ws-tab-dot ${dotCls}"></div><span>${platIco} ${esc(_sessLabel(sess))}</span>`;
      } else {
        tab.innerHTML = `<span style="opacity:.35">Empty pane</span>`;
      }
      tab.onclick = () => focusPane(idx);
      bar.appendChild(tab);
    });
    if (addBtn)
      bar.appendChild(addBtn);
    else {
      const a = document.createElement("div");
      a.className = "ws-tab-add";
      a.title = "Open session in new pane";
      a.textContent = "\uFF0B";
      a.onclick = () => openSessionPicker(-1);
      bar.appendChild(a);
    }
    if (rightEl)
      bar.appendChild(rightEl);
    else {
      const r = document.createElement("div");
      r.className = "ws-tabbar-right";
      r.innerHTML = `<div class="devmode-btn${state.devMode ? " on" : ""}" id="devmode-btn" onclick="toggleDevMode()"><div class="devmode-dot"></div><span>>_ Dev</span></div>`;
      bar.appendChild(r);
    }
  }
  function _paneStatus(pane) {
    if (!pane.channelId)
      return "empty";
    const s = state.allSessions.find((ss) => ss.channel_id === pane.channelId);
    if (!s || s.alive === false)
      return "stopped";
    return s._status || "idle";
  }
  function renderGrid() {
    const grid = document.getElementById("ws-grid");
    if (!grid)
      return;
    grid.style.gridTemplateColumns = state.panes.length === 1 ? "1fr" : "1fr 1fr";
    while (grid.children.length < state.panes.length) {
      const idx = grid.children.length;
      grid.appendChild(_makePanelEl(idx));
    }
    while (grid.children.length > state.panes.length) {
      grid.removeChild(grid.lastChild);
    }
    state.panes.forEach((pane, idx) => {
      const panelEl = grid.children[idx];
      if (!panelEl)
        return;
      panelEl.classList.toggle("active", idx === state.activePaneIdx);
      _updatePanelHeader(panelEl, pane, idx);
      _ensurePaneTerm(panelEl, pane, idx);
    });
    renderSessionTabs();
  }
  function _makePanelEl(idx) {
    const el = document.createElement("div");
    el.className = "ws-panel" + (idx === state.activePaneIdx ? " active" : "");
    el.dataset.paneIdx = idx;
    el.innerHTML = _panelHeadHTML(null, idx) + '<div class="wsp-term-wrap"></div>';
    el.querySelector(".wsp-head").addEventListener("click", () => focusPane(idx));
    return el;
  }
  function _panelHeadHTML(pane, idx) {
    const sess = pane && pane.channelId ? state.allSessions.find((s) => s.channel_id === pane.channelId) : null;
    if (!sess) {
      return `<div class="wsp-head" onclick="focusPane(${idx})"><div class="pane-status"><div class="pane-dot stopped"></div><span class="pane-status-stopped">Empty</span></div><div class="wsp-name" style="opacity:.35">No session</div><button class="wsp-status-lbl" style="background:none;border:none;cursor:pointer;color:rgba(0,0,0,.38);font-size:11px" onclick="event.stopPropagation();openSessionPicker(${idx})">Open\u2026</button></div>`;
    }
    const status = _paneStatus(pane);
    const dotCls = status === "active" ? "active" : status === "busy" ? "idle" : "stopped";
    const lblCls = status === "active" ? "pane-status-active" : status === "busy" ? "pane-status-idle" : "pane-status-stopped";
    const lblTxt = status === "active" ? "\u25B6 Active" : status === "busy" ? "\u23F3 Busy" : status === "stopped" ? "\u2B1C Stopped" : "\u23F8 Idle";
    const cli = (sess.coding_cli || "claude").toLowerCase();
    const dir = sess.work_dir ? sess.work_dir.split("/").pop() : "";
    return `<div class="wsp-head" onclick="focusPane(${idx})"><div class="pane-status"><div class="pane-dot ${dotCls}"></div><span class="${lblCls}">${lblTxt}</span></div><div class="wsp-name">${esc(_sessLabel(sess))}</div><div class="wsp-ai">${esc(cli)}${dir ? " \xB7 " + esc(dir) : ""}</div><button class="wsp-status-lbl" style="background:none;border:none;cursor:pointer;color:rgba(0,0,0,.38);font-size:11px" onclick="event.stopPropagation();openSessionPicker(${idx})">Change</button></div>`;
  }
  function _updatePanelHeader(panelEl, pane, idx) {
    const existingHead = panelEl.querySelector(".wsp-head");
    if (existingHead) {
      const newHead = document.createElement("div");
      newHead.innerHTML = _panelHeadHTML(pane, idx);
      panelEl.replaceChild(newHead.firstChild, existingHead);
    }
  }
  function _ensurePaneTerm(panelEl, pane, idx) {
    let wrap = panelEl.querySelector(".wsp-term-wrap");
    if (!wrap) {
      wrap = document.createElement("div");
      wrap.className = "wsp-term-wrap";
      panelEl.appendChild(wrap);
    }
    if (!pane.channelId) {
      if (!wrap.querySelector(".wsp-empty")) {
        wrap.innerHTML = `<div class="wsp-empty" onclick="openSessionPicker(${idx})"><div class="wsp-empty-ico">\u2317</div><span>Click to open a session</span></div>`;
      }
      return;
    }
    if (pane.terminal && pane.channelId)
      return;
    wrap.innerHTML = "";
    _initPaneTerm(wrap, pane, idx);
  }
  function _initPaneTerm(wrap, pane, idx) {
    const term = new Terminal({
      fontFamily: '"SF Mono", "JetBrains Mono", "Menlo", monospace',
      fontSize: 13,
      lineHeight: 1.45,
      theme: {
        background: "transparent",
        foreground: "rgba(255,255,255,0.85)",
        cursor: "rgba(255,255,255,0.75)",
        selectionBackground: "rgba(99,102,241,0.35)",
        black: "#1a1a1a",
        red: "#ff6b6b",
        green: "#51cf66",
        yellow: "#ffd43b",
        blue: "#74c0fc",
        magenta: "#cc5de8",
        cyan: "#3bc9db",
        white: "#e9ecef",
        brightBlack: "#868e96",
        brightRed: "#ff8787",
        brightGreen: "#8ce99a",
        brightYellow: "#ffe066",
        brightBlue: "#91d0ff",
        brightMagenta: "#da77f2",
        brightCyan: "#66d9e8",
        brightWhite: "#f8f9fa"
      },
      allowTransparency: true,
      cursorBlink: true,
      scrollback: 5e3
    });
    const fitAddon = new FitAddon.FitAddon();
    term.loadAddon(fitAddon);
    term.open(wrap);
    fitAddon.fit();
    term.focus();
    pane.terminal = term;
    pane.fitAddon = fitAddon;
    if (idx === 0) {
      state.sessTerminal = term;
      state.activeSessId = pane.channelId;
    }
    term.onData((data) => {
      const encoded = btoa(unescape(encodeURIComponent(data)));
      (window.__TAURI__.core?.invoke ?? window.__TAURI__.invoke)("pty_input", {
        channel_id: pane.channelId,
        data: encoded
      }).catch(() => {
      });
    });
    const binding = state.allSessions.find((s) => s.channel_id === pane.channelId);
    if (binding) {
      (window.__TAURI__.core?.invoke ?? window.__TAURI__.invoke)("open_pty", {
        channel_id: pane.channelId,
        tmux_session: state.tmuxSession,
        window_id: binding.window_id,
        rows: term.rows,
        cols: term.cols
      }).catch(() => {
      });
    }
    const ro = new ResizeObserver(() => {
      fitAddon.fit();
      if (term.rows && term.cols) {
        (window.__TAURI__.core?.invoke ?? window.__TAURI__.invoke)("resize_pty", {
          channel_id: pane.channelId,
          rows: term.rows,
          cols: term.cols
        }).catch(() => {
        });
      }
    });
    ro.observe(wrap);
    pane.ro = ro;
  }
  function focusPane(idx) {
    state.activePaneIdx = idx;
    state.activeSessId = state.panes[idx]?.channelId || null;
    const grid = document.getElementById("ws-grid");
    if (grid) {
      Array.from(grid.children).forEach((el, i) => el.classList.toggle("active", i === idx));
    }
    renderSessionTabs();
  }
  function assignSessionToPane(channelId, paneIdx) {
    const pane = state.panes[paneIdx];
    if (!pane)
      return;
    if (pane.channelId && window.__TAURI__) {
      (window.__TAURI__.core?.invoke ?? window.__TAURI__.invoke)("close_pty", { channel_id: pane.channelId }).catch(() => {
      });
    }
    if (pane.terminal) {
      pane.terminal.dispose();
      pane.terminal = null;
    }
    if (pane.ro) {
      pane.ro.disconnect();
      pane.ro = null;
    }
    pane.fitAddon = null;
    pane.channelId = channelId;
    if (paneIdx === 0) {
      state.activeSessId = channelId;
      state.sessTerminal = null;
    }
    renderGrid();
    focusPane(paneIdx);
  }
  function openSessionPicker(paneIdx) {
    state.sessPickerTargetPane = paneIdx >= 0 ? paneIdx : 0;
    renderSessPickerTree();
    document.getElementById("sess-picker-modal").classList.add("on");
  }
  function closeSessPickerModal(event) {
    if (event && event.target !== document.getElementById("sess-picker-modal"))
      return;
    document.getElementById("sess-picker-modal").classList.remove("on");
  }
  function renderSessPickerTree() {
    const body = document.getElementById("sess-picker-body");
    if (!body)
      return;
    const { byDir, dead } = buildSessionTree(state.allSessions);
    let html = "";
    byDir.forEach((group, dir) => {
      const repSess = group.roots[0] || [...group.childMap.values()][0]?.[0];
      const projName = repSess ? _projectName(repSess.work_dir) : dir;
      html += `<div class="sp-dir-hd">\u{1F4C1} ${esc(projName)}</div>`;
      group.roots.forEach((sess) => {
        html += _spRowHTML(sess, 0);
        const children = group.childMap.get(sess.channel_id) || [];
        children.forEach((child) => {
          html += _spRowHTML(child, 1);
        });
      });
    });
    if (dead.length) {
      html += `<div class="sp-dead-section">Stopped</div>`;
      dead.forEach((sess) => {
        html += _spRowHTML(sess, 0, true);
      });
    }
    if (!html) {
      html = `<div style="padding:24px;text-align:center;color:rgba(0,0,0,.3);font-size:13px">No sessions found.<br>Start a new one below.</div>`;
    }
    body.innerHTML = html;
  }
  function _spRowHTML(sess, depth, isDead) {
    const name = _sessLabel(sess);
    const cli = (sess.coding_cli || "claude").toLowerCase();
    const platIco = sess.platform === "desktop" ? "\u{1F5A5}" : "\u{1F4AC}";
    const statusCls = isDead ? "stopped" : sess._status === "active" ? "alive" : "idle";
    const statusIco = isDead ? "\u2B1C" : sess._status === "active" ? "\u25B6" : "\u23F8";
    const deadCls = isDead ? " sp-dead" : "";
    const depthCls = depth === 1 ? " sp-thread" : depth >= 2 ? " sp-thread2" : "";
    return `<div class="sp-row${depthCls}${deadCls}" onclick="pickSession('${esc(sess.channel_id)}')"><div class="sp-status ${statusCls}">${statusIco}</div><div class="sp-name">${platIco} ${esc(name)}</div><div class="sp-meta"><span class="sp-cli">${esc(cli)}</span></div></div>`;
  }
  function pickSession(channelId) {
    document.getElementById("sess-picker-modal").classList.remove("on");
    assignSessionToPane(channelId, state.sessPickerTargetPane);
  }
  function newSessionFromPicker() {
    document.getElementById("sess-picker-modal").classList.remove("on");
    const name = prompt("Session name:", "ghost");
    if (!name)
      return;
    const refSess = state.allSessions[0];
    const work_dir = refSess?.work_dir || "~";
    if (window.ghost)
      window.ghost.send("new_session", { name, work_dir, cli: "claude" }).catch(() => {
      });
  }
  function renderSessions(sessions) {
    state.allSessions = sessions;
    const aliveSorted = sessions.filter((s) => s.alive !== false).sort((a, b) => {
      if (a.platform === "desktop" && b.platform !== "desktop")
        return -1;
      if (b.platform === "desktop" && a.platform !== "desktop")
        return 1;
      return (b.created_at || "").localeCompare(a.created_at || "");
    });
    let assigned = 0;
    state.panes.forEach((pane, idx) => {
      if (!pane.channelId && aliveSorted[assigned]) {
        pane.channelId = aliveSorted[assigned].channel_id;
        if (idx === 0) {
          state.activeSessId = pane.channelId;
        }
        assigned++;
      }
    });
    renderGrid();
  }
  function toggleDevMode() {
    state.devMode = !state.devMode;
    const btn = document.getElementById("devmode-btn");
    if (btn)
      btn.classList.toggle("on", state.devMode);
    const grid = document.getElementById("ws-grid");
    if (grid)
      grid.classList.toggle("devmode", state.devMode);
  }
  function ar(ta) {
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 100) + "px";
  }
  function hk(_e) {
  }
  function send() {
  }
  function addm(role, text) {
    const now = (/* @__PURE__ */ new Date()).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    const d = document.createElement("div");
    d.className = "msg " + role;
    d.innerHTML = `<div class="mav">${role === "ai" ? "\u{1F916}" : "W"}</div><div><div class="bbl">${fmt(text)}</div><div class="mt">${now}</div></div>`;
    const msgs = document.getElementById("build-msgs");
    if (msgs)
      msgs.insertBefore(d, document.getElementById("typing"));
    scrl();
  }
  function scrl() {
    const c = document.getElementById("build-msgs");
    if (c)
      setTimeout(() => c.scrollTop = c.scrollHeight, 10);
  }
  function cpc(btn) {
    navigator.clipboard.writeText(btn.previousElementSibling.textContent).catch(() => {
    });
    btn.textContent = "Copied!";
    setTimeout(() => btn.textContent = "Copy", 1500);
  }

  // src/views/agents.ts
  function _flatAgent(id) {
    for (const p of AGENTS.browser.profiles) {
      for (const a of p.agents) {
        if (a.id === id)
          return a;
      }
    }
    for (const a of AGENTS.loop) {
      if (a.id === id)
        return a;
    }
    for (const a of AGENTS.reactive) {
      if (a.id === id)
        return a;
    }
    return null;
  }
  function _fleetCardHTML(a) {
    const badgeMap = {
      running: ["fleet-badge-running", "\u25B6 Running"],
      done: ["fleet-badge-done", "\u2713 Done"],
      idle: ["fleet-badge-idle", "\u23F8 Idle"],
      listening: ["fleet-badge-listening", "\u25CF Listening"],
      waiting: ["fleet-badge-waiting", "\u26A0 Waiting"]
    };
    const [badgeCls, badgeTxt] = badgeMap[a.status] || ["fleet-badge-idle", a.status];
    const repairBadge = a.autoRepaired ? `<div class="fleet-repaired-badge">\u{1F916} Auto-repaired by Build Agent</div>` : "";
    return `<div class="fleet-card ${a.status}${state.curAgentId === a.id ? " on" : ""}" onclick="openFleetDrawer(this,'${a.id}')">
    <div class="fleet-card-top">
      <div class="fleet-card-name">${esc(a.name)}</div>
      <div class="fleet-card-badge ${badgeCls}">${badgeTxt}</div>
    </div>
    <div class="fleet-card-sub">${esc(a.sub)}</div>
    <div class="fleet-card-type">${esc(a.type)}</div>
    ${repairBadge}
  </div>`;
  }
  function renderFleet() {
    const totalAgents = AGENTS.browser.profiles.reduce((n, p) => n + p.agents.length, 0) + AGENTS.loop.length + AGENTS.reactive.length;
    const emptyEl = document.getElementById("agents-empty");
    const scrollEl = document.getElementById("fleet-scroll");
    if (emptyEl)
      emptyEl.style.display = totalAgents === 0 ? "flex" : "none";
    if (scrollEl)
      scrollEl.style.display = totalAgents === 0 ? "none" : "";
    const profileRow = document.getElementById("profile-row");
    if (profileRow) {
      let html = "";
      AGENTS.browser.profiles.forEach((p) => {
        const running = p.agents.filter((a) => a.status === "running").length;
        const count = p.agents.length;
        const isOn = p.id === state.curProfileId;
        const statusCls = running > 0 ? "running" : "idle";
        const statusTxt = running > 0 ? `\u25B6 ${running} running` : count > 0 ? "\u23F8 Idle" : "\u25CF No agents";
        html += `<div class="profile-card${isOn ? " on" : ""}" onclick="selProfile(this,'${p.id}')">
        <div class="profile-card-ico">\u{1F310}</div>
        <div class="profile-card-name">${esc(p.label)}</div>
        <div class="profile-card-count">${count} agent${count !== 1 ? "s" : ""}</div>
        <div class="profile-card-status ${statusCls}">${statusTxt}</div>
      </div>`;
      });
      html += `<div class="profile-card add" onclick="showToast('Add Chrome profile \u2014 coming soon')">\uFF0B<br>Add Profile</div>`;
      profileRow.innerHTML = html;
    }
    renderProfileAgents(state.curProfileId);
    renderTriggerGrid();
  }
  function renderProfileAgents(profileId) {
    const profile = AGENTS.browser.profiles.find((p) => p.id === profileId);
    const row = document.getElementById("profile-agents-row");
    if (!row || !profile)
      return;
    if (profile.agents.length === 0) {
      row.innerHTML = `<div style="padding:6px 10px;font-size:12px;color:rgba(0,0,0,.35);font-style:italic">No agents in this profile. Ask the Build agent to create one.</div>`;
      return;
    }
    row.innerHTML = profile.agents.map(_fleetCardHTML).join("");
  }
  function selProfile(el, profileId) {
    state.curProfileId = profileId;
    document.querySelectorAll(".profile-card").forEach((c) => c.classList.remove("on"));
    if (el)
      el.classList.add("on");
    renderProfileAgents(profileId);
  }
  function openFleetDrawer(el, agentId) {
    state.curAgentId = agentId;
    document.querySelectorAll(".fleet-card").forEach((c) => c.classList.remove("on"));
    if (el)
      el.classList.add("on");
    document.getElementById("fleet-drawer").classList.add("on");
    _renderFleetDrawer(agentId);
  }
  function closeFleetDrawer() {
    document.getElementById("fleet-drawer").classList.remove("on");
    document.querySelectorAll(".fleet-card").forEach((c) => c.classList.remove("on"));
    state.curAgentId = null;
  }
  function _renderFleetDrawer(agentId) {
    const a = _flatAgent(agentId);
    const titleEl = document.getElementById("fleet-drawer-title");
    const bodyEl = document.getElementById("fleet-drawer-body");
    if (!a || !bodyEl)
      return;
    if (titleEl)
      titleEl.textContent = a.name + " \xB7 " + a.type;
    const d = a.detail;
    const runProgress = d.running ? `\u25B6 Running \xB7 step ${d.steps} of ~${d.totalSteps} \xB7 ${d.elapsed} elapsed` : a.status === "done" ? `\u2713 Done \xB7 ${d.steps} steps \xB7 ${d.elapsed}` : a.status === "listening" ? "\u25CF Listening for events\u2026" : "\u23F8 Idle";
    const logRows = d.log.map((l) => {
      const out = l.out ? `<div class="ag-log-out">${esc(l.out)}</div>` : "";
      const pending = l.pending ? `<div class="ag-log-pending">\u23F3 in progress\u2026</div>` : "";
      return `<div class="ag-log-row">
      <div class="ag-log-ico">${l.ico}</div>
      <div class="ag-log-body">
        <div class="ag-log-action">${l.action}</div>
        <div class="ag-log-desc">${esc(l.desc)}</div>${out}${pending}
      </div>${l.ts ? `<div class="ag-log-ts">${l.ts}</div>` : ""}
    </div>`;
    }).join("");
    const liveView = a.type === "Browser Agent" ? _mockBrowserScreen(a) : "";
    const chromeBanner = a.type === "Browser Agent" ? `
    <div class="ag-chrome-banner">
      <div class="ag-chrome-banner-inner">
        <span>\u{1F310}</span>
        <strong>Real Chrome \xB7 ${esc(a.profile || "Personal Chrome")}</strong>
      </div>
      <div style="font-size:11px;color:rgba(0,0,0,.45);margin-top:3px">Your sessions. Your cookies. No re-logging in.</div>
    </div>` : "";
    const colLeft = `
    <div class="drawer-col-left">
      ${chromeBanner}
      <div class="ag-run-bar"><div class="ag-run-status">${runProgress}</div></div>
      <div class="ag-actions">
        <button class="ag-btn">\u23F8 Pause</button>
        <button class="ag-btn">\u25B6 Run Now</button>
        <button class="ag-btn" style="color:#dc2626;border-color:rgba(220,38,38,.25)" onclick="showToast('Agent deleted (simulated)')">\u{1F5D1} Delete</button>
        <button class="ag-btn link" onclick="setMode('data')">View in Data \u2192</button>
      </div>
    </div>`;
    const colRight = `
    <div class="drawer-col-right">
      <div class="ag-log-hd">\u2014 Execution Log</div>
      <div class="ag-log-scroll">${logRows || '<div style="color:rgba(0,0,0,.30);font-size:12px;padding:8px 0">No log entries yet.</div>'}</div>
    </div>`;
    if (liveView) {
      bodyEl.innerHTML = `
      <div class="drawer-live-wrap">
        ${liveView}
        <div class="drawer-bottom-row">${colLeft}${colRight}</div>
      </div>`;
    } else {
      bodyEl.innerHTML = colLeft + colRight;
    }
  }
  function _mockBrowserScreen(a) {
    const isRunning = a.detail.running;
    const currentUrl = isRunning ? "nash-ai.cn/reports/list" : "nash-ai.cn/reports";
    const liveLbl = isRunning ? `<span class="live-dot"></span> Live \xB7 2s ago` : `<span style="color:rgba(0,0,0,.35)">Last frame \xB7 18s ago</span>`;
    const pageContent = isRunning ? `
    <div class="mock-site-header">
      <div class="mock-site-logo">Nash<span>AI</span></div>
      <div class="mock-site-nav">Reports &nbsp;\xB7&nbsp; Portfolio &nbsp;\xB7&nbsp; Settings</div>
    </div>
    <div class="mock-site-body">
      <div class="mock-site-title">Research Reports</div>
      <div class="mock-site-row sel">
        <div class="mock-site-row-ico">\u{1F4C4}</div>
        <div class="mock-site-row-name">Goldman Sachs Q2 2024 Analysis</div>
        <div class="mock-site-row-meta">2.4 MB \xB7 PDF</div>
        <div class="mock-download-bar"><div class="mock-download-fill"></div></div>
      </div>
      <div class="mock-site-row dim"><div class="mock-site-row-ico">\u{1F4C4}</div><div class="mock-site-row-name">Morgan Stanley Q2 2024</div><div class="mock-site-row-meta">1.8 MB</div></div>
      <div class="mock-site-row dim"><div class="mock-site-row-ico">\u{1F4C4}</div><div class="mock-site-row-name">JP Morgan Macro Outlook</div><div class="mock-site-row-meta">3.1 MB</div></div>
    </div>` : `
    <div class="mock-site-header">
      <div class="mock-site-logo">Nash<span>AI</span></div>
    </div>
    <div class="mock-site-body" style="opacity:.7">
      <div class="mock-site-title">Research Reports \xB7 47 items</div>
      <div class="mock-site-row dim"><div class="mock-site-row-ico">\u{1F4C4}</div><div class="mock-site-row-name">Goldman Sachs Q2 2024 Analysis</div><div class="mock-site-row-meta">\u2713 Downloaded</div></div>
    </div>`;
    return `<div class="live-browser">
    <div class="live-browser-bar">
      <div class="live-browser-dots"><span></span><span></span><span></span></div>
      <div class="live-browser-url">\u{1F512} ${currentUrl}</div>
      <div class="live-browser-badge">${liveLbl}</div>
    </div>
    <div class="live-browser-screen">${pageContent}</div>
  </div>`;
  }
  function renderTriggerGrid() {
    const grid = document.getElementById("trigger-grid");
    if (!grid)
      return;
    const addBtn = `<div class="fleet-card-add" onclick="showToast('Type /agent in Build to create a Trigger Agent')">\uFF0B New Trigger Agent</div>`;
    if (Object.keys(state.skillDefs).length > 0) {
      const skills = Object.values(state.skillDefs);
      grid.innerHTML = skills.map((sk) => {
        const run = state.runnerAgents[sk.name] || { skill_name: sk.name, status: sk.paused ? "idle" : "\u2014" };
        return _runnerCardHTML(run);
      }).join("") + addBtn;
    } else {
      const all = [...AGENTS.loop || [], ...AGENTS.reactive || []];
      grid.innerHTML = all.map(_fleetCardHTML).join("") + addBtn;
    }
  }
  function _runnerCardHTML(run) {
    const name = run.skill_name || "Unknown Skill";
    const dotColorMap = {
      success: "#22c55e",
      done: "#22c55e",
      failed: "#ef4444",
      fail: "#ef4444",
      running: "#eab308",
      guarded: "#f97316"
    };
    const dotColor = dotColorMap[run.status] || "rgba(0,0,0,.25)";
    const badgeMap = {
      success: ["fleet-badge-done", "\u2713 Success"],
      done: ["fleet-badge-done", "\u2713 Done"],
      failed: ["fleet-badge-waiting", "\u2717 Failed"],
      fail: ["fleet-badge-waiting", "\u2717 Failed"],
      running: ["fleet-badge-running", "\u25B6 Running"],
      guarded: ["fleet-badge-waiting", "\u26A0 Guarded"]
    };
    const [badgeCls, badgeTxt] = badgeMap[run.status] || ["fleet-badge-idle", run.status || "\u2014"];
    let lastRunTxt = "\u2014";
    if (run.started_at) {
      try {
        lastRunTxt = new Date(run.started_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      } catch (e) {
        lastRunTxt = run.started_at;
      }
    }
    let durationTxt = "";
    if (run.started_at && run.finished_at) {
      try {
        durationTxt = ` \xB7 ${Math.round((new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()) / 1e3)}s`;
      } catch (e) {
      }
    }
    const sk = state.skillDefs[name];
    let triggerBadge = "";
    if (sk && sk.trigger) {
      const ttype = (sk.trigger.type || sk.trigger).toString().toLowerCase();
      const trigLabel = ttype === "loop" ? "Loop" : ttype === "reactive" ? "Reactive" : String(sk.trigger.type || sk.trigger);
      triggerBadge = `<span class="runner-trigger-badge runner-trigger-${ttype}">${trigLabel}</span>`;
    }
    const isPaused = run._paused === true;
    return `<div class="fleet-card runner-card ${run.status || "idle"}" onclick="openRunnerDrawer(this,'${esc(name)}')">
    <div class="fleet-card-top">
      <div class="fleet-card-name" style="display:flex;align-items:center;gap:6px">
        <span class="runner-status-dot" style="background:${dotColor};width:8px;height:8px;border-radius:50%;flex-shrink:0;display:inline-block"></span>
        ${esc(name)}
      </div>
      <div class="fleet-card-badge ${badgeCls}">${badgeTxt}</div>
    </div>
    <div class="fleet-card-sub">Last run: ${lastRunTxt}${durationTxt}</div>
    <div style="display:flex;align-items:center;gap:5px;margin-top:4px">
      <div class="fleet-card-type">Runner Agent</div>
      ${triggerBadge}
    </div>
    <div class="runner-card-actions" onclick="event.stopPropagation()">
      <button class="runner-btn" onclick="runnerRunNow('${esc(name)}')">\u25B6 Run Now</button>
      <button class="runner-btn runner-btn-sec" onclick="runnerTogglePause('${esc(name)}',this)">
        ${isPaused ? "\u25B6 Resume" : "\u23F8 Pause"}
      </button>
    </div>
  </div>`;
  }

  // src/ui/toast.ts
  function showToast(msg, onView) {
    const toast = document.getElementById("toast");
    const msgEl = document.getElementById("toast-msg");
    if (!toast || !msgEl)
      return;
    msgEl.textContent = msg;
    const viewLink = document.getElementById("toast-view-link");
    if (viewLink) {
      if (onView) {
        viewLink.style.display = "inline";
        viewLink.onclick = onView;
      } else {
        viewLink.style.display = "none";
      }
    }
    toast.classList.add("on");
    if (state.toastTimer)
      clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(() => dismissToast(), 4e3);
  }
  function dismissToast() {
    const el = document.getElementById("toast");
    if (el)
      el.classList.remove("on");
  }

  // src/views/runner.ts
  function renderRunnerCard(run) {
    const name = run.skill_name || "Unknown Skill";
    const dotColorMap = {
      success: "#22c55e",
      done: "#22c55e",
      failed: "#ef4444",
      fail: "#ef4444",
      running: "#eab308",
      guarded: "#f97316"
    };
    const dotColor = dotColorMap[run.status] || "rgba(0,0,0,.25)";
    const badgeMap = {
      success: ["fleet-badge-done", "\u2713 Success"],
      done: ["fleet-badge-done", "\u2713 Done"],
      failed: ["fleet-badge-waiting", "\u2717 Failed"],
      fail: ["fleet-badge-waiting", "\u2717 Failed"],
      running: ["fleet-badge-running", "\u25B6 Running"],
      guarded: ["fleet-badge-waiting", "\u26A0 Guarded"]
    };
    const [badgeCls, badgeTxt] = badgeMap[run.status] || ["fleet-badge-idle", run.status || "\u2014"];
    let lastRunTxt = "\u2014";
    if (run.started_at) {
      try {
        const d = new Date(run.started_at);
        lastRunTxt = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      } catch (e) {
        lastRunTxt = run.started_at;
      }
    }
    let durationTxt = "";
    if (run.started_at && run.finished_at) {
      try {
        const dur = Math.round((new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()) / 1e3);
        durationTxt = ` \xB7 ${dur}s`;
      } catch (e) {
      }
    }
    const sk = state.skillDefs[name];
    let triggerBadge = "";
    if (sk && sk.trigger) {
      const ttype = (sk.trigger.type || sk.trigger).toString().toLowerCase();
      const trigLabel = ttype === "loop" ? "Loop" : ttype === "reactive" ? "Reactive" : String(sk.trigger.type || sk.trigger);
      triggerBadge = `<span class="runner-trigger-badge runner-trigger-${ttype}">${trigLabel}</span>`;
    }
    const isPaused = run._paused === true;
    return `<div class="fleet-card runner-card ${run.status || "idle"}" onclick="openRunnerDrawer(this,'${esc(name)}')">
    <div class="fleet-card-top">
      <div class="fleet-card-name" style="display:flex;align-items:center;gap:6px">
        <span class="runner-status-dot" style="background:${dotColor};width:8px;height:8px;border-radius:50%;flex-shrink:0;display:inline-block"></span>
        ${esc(name)}
      </div>
      <div class="fleet-card-badge ${badgeCls}">${badgeTxt}</div>
    </div>
    <div class="fleet-card-sub">Last run: ${lastRunTxt}${durationTxt}</div>
    <div style="display:flex;align-items:center;gap:5px;margin-top:4px">
      <div class="fleet-card-type">Runner Agent</div>
      ${triggerBadge}
    </div>
    <div class="runner-card-actions" onclick="event.stopPropagation()">
      <button class="runner-btn" onclick="runnerRunNow('${esc(name)}')">\u25B6 Run Now</button>
      <button class="runner-btn runner-btn-sec" onclick="runnerTogglePause('${esc(name)}',this)">
        ${isPaused ? "\u25B6 Resume" : "\u23F8 Pause"}
      </button>
    </div>
  </div>`;
  }
  function renderRunnerGrid() {
    const grid = document.getElementById("runner-grid");
    if (!grid)
      return;
    const runs = Object.values(state.runnerAgents);
    if (runs.length === 0) {
      grid.innerHTML = '<div style="font-size:12px;color:rgba(0,0,0,.35);padding:6px 0;font-style:italic">No runner agents yet.</div>';
      return;
    }
    grid.innerHTML = runs.map(renderRunnerCard).join("");
  }
  function openRunnerDrawer(el, skillName) {
    document.querySelectorAll(".fleet-card").forEach((c) => c.classList.remove("on"));
    if (el)
      el.classList.add("on");
    const run = state.runnerAgents[skillName];
    const drawer = document.getElementById("fleet-drawer");
    const titleEl = document.getElementById("fleet-drawer-title");
    const bodyEl = document.getElementById("fleet-drawer-body");
    if (!drawer || !bodyEl)
      return;
    drawer.classList.add("on");
    if (titleEl)
      titleEl.textContent = skillName + " \xB7 Runner Agent";
    const sk = state.skillDefs[skillName];
    let metaRows = "";
    if (sk) {
      const ttype = sk.trigger ? sk.trigger.type || sk.trigger : "\u2014";
      const onFail = sk.on_failure || "\u2014";
      const guard = sk.guard ? sk.guard.enabled ? "\u2713 Enabled" : "\u2717 Disabled" : "\u2014";
      const steps = Array.isArray(sk.steps) ? sk.steps.length : "\u2014";
      metaRows = `
      <div class="runner-meta-row">
        <span class="runner-meta-key">Trigger</span><span class="runner-meta-val">${esc(String(ttype))}</span>
        <span class="runner-meta-key">On failure</span><span class="runner-meta-val">${esc(String(onFail))}</span>
        <span class="runner-meta-key">Guard</span><span class="runner-meta-val">${esc(String(guard))}</span>
        <span class="runner-meta-key">Steps</span><span class="runner-meta-val">${esc(String(steps))}</span>
      </div>`;
    }
    const runId = run ? run.run_id : null;
    const logPanelId = "runner-log-panel-" + skillName.replace(/[^a-z0-9]/gi, "_");
    bodyEl.innerHTML = `
    <div class="drawer-col-left">
      <div class="ag-run-bar">
        <div class="ag-run-status">${run ? esc(run.status || "\u2014") : "No runs yet"}</div>
      </div>
      ${metaRows}
      <div class="ag-actions">
        <button class="ag-btn" onclick="runnerRunNow('${esc(skillName)}')">\u25B6 Run Now</button>
        <button class="ag-btn" onclick="runnerTogglePauseById('${esc(skillName)}')">\u23F8 Pause / \u25B6 Resume</button>
      </div>
    </div>
    <div class="drawer-col-right">
      <div class="ag-log-hd">\u2014 Live Log</div>
      <div class="ag-log-scroll runner-log-scroll" id="${logPanelId}">
        <div style="color:rgba(0,0,0,.30);font-size:12px;padding:8px 0">Fetching log\u2026</div>
      </div>
    </div>`;
    if (window.ghost && skillName && runId) {
      window.ghost.send("agent_log", { skill_name: skillName, run_id: runId });
    } else {
      const lp = document.getElementById(logPanelId);
      if (lp)
        lp.innerHTML = '<div style="color:rgba(0,0,0,.30);font-size:12px;padding:8px 0">No log available (not connected to backend).</div>';
    }
  }
  function runnerRunNow(skillName) {
    if (window.ghost) {
      window.ghost.send("skill_run", { skill_name: skillName });
      showToast(`\u25B6 Running "${skillName}"\u2026`);
    } else {
      showToast(`\u25B6 Run Now: ${skillName} (no backend connected)`);
    }
  }
  function runnerTogglePause(skillName, btn) {
    const run = state.runnerAgents[skillName];
    if (!run)
      return;
    const wasPaused = run._paused === true;
    run._paused = !wasPaused;
    if (btn)
      btn.textContent = run._paused ? "\u25B6 Resume" : "\u23F8 Pause";
    if (window.ghost) {
      window.ghost.send(run._paused ? "skill_pause" : "skill_resume", { skill_name: skillName });
      showToast(run._paused ? `\u23F8 Paused "${skillName}"` : `\u25B6 Resumed "${skillName}"`);
    } else {
      showToast((run._paused ? "\u23F8 Paused " : "\u25B6 Resumed ") + skillName + " (no backend)");
    }
  }
  function runnerTogglePauseById(skillName) {
    const run = state.runnerAgents[skillName];
    if (!run)
      return;
    runnerTogglePause(skillName, null);
    renderRunnerGrid();
  }

  // src/data/db.ts
  var DB_COLLECTIONS = {
    fromAgents: [
      { id: "btc_prices", name: "btc_prices", rows: 128, updated: "2m ago", icon: "\u{1F4CA}", table: "btc_prices", sourceAgent: "btc-monitor" },
      { id: "hn_links", name: "hn_links", rows: 340, updated: "2h ago", icon: "\u{1F517}", table: "hn_links", sourceAgent: "hn-digest-loop" },
      { id: "nash_reports", name: "nash_reports", rows: 47, updated: "10m ago", icon: "\u{1F4C4}", table: "nash_reports", sourceAgent: "nash-reporter" }
    ],
    fromSkills: [
      { id: "market_scans", name: "market_scans", rows: 86, updated: "14m ago", icon: "\u{1F4C8}", table: "market_scans", sourceSkill: "market" },
      { id: "screenshots", name: "screenshots", rows: 12, updated: "1h ago", icon: "\u{1F5BC}", table: "screenshots", sourceSkill: "screenshot" }
    ],
    manual: [
      { id: "notes", name: "notes", rows: 8, updated: "3d ago", icon: "\u{1F4DD}", table: "notes" }
    ]
  };
  var TABLE_SOURCE_MAP = {};
  DB_COLLECTIONS.fromAgents.forEach((c) => {
    TABLE_SOURCE_MAP[c.table] = { type: "agent", id: c.sourceAgent };
  });
  DB_COLLECTIONS.fromSkills.forEach((c) => {
    TABLE_SOURCE_MAP[c.table] = { type: "skill", id: c.sourceSkill };
  });
  var DB = {
    tasks: {
      cols: ["id", "goal", "status", "profile", "created_at", "summary"],
      rows: [
        { id: "tsk_01hw8m", goal: "Find the current BTC price on CoinGecko and save it", status: "done", profile: "Personal", created_at: "2026-03-14 14:41:02", summary: "BTC price $67,432.18 extracted and saved" },
        { id: "tsk_01hw9k", goal: "Download Goldman Sachs Q2 report from Nash-AI", status: "running", profile: "nash-ai", created_at: "2026-03-14 14:43:00", summary: null },
        { id: "tsk_01hwaq", goal: 'Log in to Notion and export "Week 12" page as PDF', status: "needs_review", profile: "Work", created_at: "2026-03-14 14:45:00", summary: null },
        { id: "tsk_01hwbr", goal: 'Search HackerNews for "AI agents" and save top 10 links', status: "queued", profile: "Personal", created_at: "2026-03-14 14:47:00", summary: null }
      ]
    },
    btc_prices: {
      cols: ["id", "price", "ts"],
      rows: Array.from({ length: 10 }, (_, i) => ({ id: "btc_" + i, price: "$" + (67e3 + i * 10) + ".00", ts: `2026-03-15 ${String(14 - i).padStart(2, "0")}:00:00` }))
    },
    hn_links: {
      cols: ["id", "title", "url", "score"],
      rows: [
        { id: 1, title: "Show HN: Ghost agent fleet", url: "https://news.ycombinator.com/item?id=1", score: 342 },
        { id: 2, title: "LLM agents in production", url: "https://news.ycombinator.com/item?id=2", score: 287 },
        { id: 3, title: "Browser automation with real Chrome", url: "https://news.ycombinator.com/item?id=3", score: 201 }
      ]
    },
    nash_reports: {
      cols: ["id", "filename", "size_kb", "downloaded_at"],
      rows: [
        { id: "rpt_1", filename: "gs_q2_2024.pdf", size_kb: 2345, downloaded_at: "2026-03-15 14:43:10" }
      ]
    },
    market_scans: {
      cols: ["id", "symbol", "price", "ts"],
      rows: [
        { id: 1, symbol: "BTC", price: "$67,432.18", ts: "2026-03-15 14:41:00" },
        { id: 2, symbol: "ETH", price: "$3,210.55", ts: "2026-03-15 14:41:00" }
      ]
    },
    screenshots: { cols: ["id", "url", "ts"], rows: [] },
    notes: { cols: ["id", "text", "created_at"], rows: [{ id: 1, text: "Check Nash-AI reports weekly", created_at: "2026-03-12" }] }
  };
  var DATA_FILES = [
    {
      type: "folder",
      id: "f-agents",
      name: "agents",
      open: true,
      children: [
        {
          type: "sqlite",
          id: "db-btc",
          name: "btc_monitor.db",
          open: false,
          tables: [{ id: "btc_prices", name: "btc_prices", rows: 128 }]
        },
        {
          type: "sqlite",
          id: "db-hn",
          name: "hn_digest.db",
          open: true,
          tables: [
            { id: "hn_links", name: "hn_links", rows: 340 },
            { id: "nash_reports", name: "nash_reports", rows: 47 }
          ]
        }
      ]
    },
    {
      type: "folder",
      id: "f-skills",
      name: "skills",
      open: false,
      children: [
        {
          type: "sqlite",
          id: "db-market",
          name: "market.db",
          open: false,
          tables: [{ id: "market_scans", name: "market_scans", rows: 86 }]
        },
        { type: "folder", id: "f-screenshots", name: "screenshots", open: false, children: [] }
      ]
    },
    { type: "csv", id: "csv-notes", name: "notes.csv", tableId: "notes", rows: 8 }
  ];

  // src/views/data-tree.ts
  function _findDataNode(nodes, id) {
    for (const n of nodes) {
      if (n.id === id)
        return n;
      if (n.children) {
        const f = _findDataNode(n.children, id);
        if (f)
          return f;
      }
    }
    return null;
  }
  function _renderTreeNodes(nodes, depth) {
    const pad = (d) => `padding-left:${10 + d * 16}px`;
    let html = "";
    nodes.forEach((n) => {
      if (n.type === "folder") {
        const arrow = n.children && n.children.length ? `<span class="dtree-arrow${n.open ? " open" : ""}" style="margin-left:auto">\u203A</span>` : "";
        html += `<div class="dtree-node" style="${pad(depth)}" onclick="toggleDataNode('${n.id}')">
        <span class="dtree-ico">\u{1F4C1}</span><span class="dtree-name">${esc(n.name)}</span>${arrow}</div>`;
        if (n.children && n.children.length) {
          html += `<div class="dtree-children${n.open ? " open" : ""}" id="dtree-ch-${n.id}">`;
          html += _renderTreeNodes(n.children, depth + 1);
          html += "</div>";
        }
      } else if (n.type === "sqlite") {
        const arrow = `<span class="dtree-arrow${n.open ? " open" : ""}" style="margin-left:auto">\u203A</span>`;
        html += `<div class="dtree-node" style="${pad(depth)}" onclick="toggleDataNode('${n.id}')">
        <span class="dtree-ico">\u{1F5C4}</span><span class="dtree-name">${esc(n.name)}</span>${arrow}</div>`;
        html += `<div class="dtree-children${n.open ? " open" : ""}" id="dtree-ch-${n.id}">`;
        (n.tables || []).forEach((t) => {
          const sel = state.curTableId === t.id;
          html += `<div class="dtree-node table-item${sel ? " sel" : ""}" style="${pad(depth + 1)}"
          onclick="selDataTable(this,'${t.id}')">
          <span class="dtree-ico" style="font-size:10px;color:rgba(0,0,0,.30)">\u21B3</span>
          <span class="dtree-name">${esc(t.name)}</span>
          <span class="dtree-count">${t.rows}</span>
        </div>`;
        });
        html += "</div>";
      } else if (n.type === "csv") {
        const sel = state.curTableId === n.tableId;
        html += `<div class="dtree-node${sel ? " sel" : ""}" style="${pad(depth)}"
        onclick="selDataTable(this,'${n.tableId}')">
        <span class="dtree-ico">\u{1F4C4}</span>
        <span class="dtree-name">${esc(n.name)}</span>
        <span class="dtree-count">${n.rows}</span>
      </div>`;
      }
    });
    return html;
  }
  function renderDataTree() {
    const inner = document.getElementById("data-tree-inner");
    if (inner)
      inner.innerHTML = _renderTreeNodes(DATA_FILES, 0);
  }
  function toggleDataNode(nodeId) {
    const node = _findDataNode(DATA_FILES, nodeId);
    if (node) {
      node.open = !node.open;
      renderDataTree();
    }
  }

  // src/data/skills.ts
  var SKILLS = {
    market: {
      name: "Market Scanner",
      desc: "Fetches latest price data for a list of ticker symbols and saves results to the database.",
      params: [
        { key: "symbols", label: "Symbols", placeholder: "BTC,ETH,AAPL,TSLA" },
        { key: "interval", label: "Interval (min)", placeholder: "60" }
      ],
      runs: [
        { status: "done", ts: "Today 14:41", params: "symbols=BTC,ETH interval=60", error: null },
        {
          status: "fail",
          ts: "Today 11:22",
          params: "symbols=BTC,ETH,XRP interval=30",
          error: {
            msg: "KeyError: 'XRP' not found in price feed\n  at fetch_price.py:42",
            ai: "The symbol 'XRP' isn't supported by the current price feed adapter. Either remove it from the list or add a fallback handler in `fetch_price.py` for unsupported symbols."
          }
        },
        { status: "done", ts: "Yesterday 09:00", params: "symbols=BTC,ETH interval=60", error: null }
      ]
    },
    screenshot: {
      name: "Screenshot Monitor",
      desc: "Captures a page screenshot at a given interval and saves diffs as artifacts.",
      params: [
        { key: "url", label: "URL", placeholder: "https://example.com" },
        { key: "interval_s", label: "Interval (sec)", placeholder: "300" }
      ],
      runs: [
        { status: "run", ts: "Now", params: "url=https://nash-ai.cn interval_s=300", error: null }
      ]
    },
    csvproc: {
      name: "CSV Processor",
      desc: "Loads a CSV file, applies a transform script, and outputs a cleaned version.",
      params: [
        { key: "input", label: "Input path", placeholder: "~/Downloads/data.csv" },
        { key: "script", label: "Transform", placeholder: "drop_duplicates, fill_nulls" }
      ],
      runs: []
    },
    report: {
      name: "Report Generator",
      desc: "Queries the local database and renders a formatted PDF report.",
      params: [
        { key: "query", label: "SQL query", placeholder: "SELECT * FROM tasks WHERE status='done'" },
        { key: "title", label: "Report title", placeholder: "Weekly Summary" }
      ],
      runs: [
        { status: "done", ts: "Yesterday 18:00", params: "title=Weekly Summary", error: null }
      ]
    },
    discord: {
      name: "Discord Notifier",
      desc: "Posts a formatted embed message to a specified Discord channel.",
      params: [
        { key: "channel", label: "Channel ID", placeholder: "1234567890" },
        { key: "message", label: "Message", placeholder: "Task completed successfully!" }
      ],
      runs: [
        {
          status: "fail",
          ts: "Today 10:05",
          params: "channel=123456",
          error: {
            msg: "HTTPError 403: Missing Permissions\n  at discord_notify.py:28",
            ai: "The bot token doesn't have the 'Send Messages' permission in that channel. Grant the permission in Discord server settings under Roles, or use a channel where the bot already has access."
          }
        }
      ]
    },
    github_pr: {
      name: "GitHub PR Reviewer",
      desc: "When a PR is opened, runs Claude to review diffs and posts a summary comment.",
      params: [
        { key: "repo", label: "Repo", placeholder: "owner/repo" },
        { key: "min_lines", label: "Min lines changed", placeholder: "10" }
      ],
      runs: [
        { status: "done", ts: "Today 09:12", params: "repo=ai4life/ghost-in-the-shell min_lines=10", error: null },
        { status: "done", ts: "Yesterday 16:44", params: "repo=ai4life/ghost-in-the-shell min_lines=10", error: null }
      ]
    },
    digest: {
      name: "Discord Digest",
      desc: "Reads recent channel messages, summarizes with Claude, and saves to DB.",
      params: [
        { key: "channel", label: "Channel ID", placeholder: "1234567890" },
        { key: "lookback_hours", label: "Lookback (hours)", placeholder: "6" }
      ],
      runs: [
        { status: "done", ts: "Today 22:00", params: "lookback_hours=6", error: null },
        { status: "done", ts: "Today 16:00", params: "lookback_hours=6", error: null }
      ]
    }
  };

  // src/views/data.ts
  function selTable(el, id) {
    document.querySelectorAll(".db-tbl-item").forEach((x) => x.classList.remove("on"));
    if (el)
      el.classList.add("on");
    state.curTableId = id;
    state.sortCol = null;
    state.sortDir = 1;
    state.filterText = "";
    const s = document.getElementById("db-search");
    if (s)
      s.value = "";
    closeDrawer();
    renderTable();
  }
  function selDataTable(el, tableId) {
    state.curTableId = tableId;
    state.sortCol = null;
    state.sortDir = 1;
    state.filterText = "";
    const s = document.getElementById("db-search");
    if (s)
      s.value = "";
    closeDrawer();
    renderDataTree();
    renderTable();
  }
  function renderTable() {
    const data = DB[state.curTableId];
    if (!data)
      return;
    const tname = document.getElementById("db-tname");
    if (tname)
      tname.textContent = state.curTableId;
    const sourceMeta = TABLE_SOURCE_MAP[state.curTableId];
    let sourceBadgeHtml = "";
    if (sourceMeta) {
      if (sourceMeta.type === "agent") {
        const a = _flatAgent(sourceMeta.id);
        const label = a ? esc(a.name) : esc(sourceMeta.id);
        sourceBadgeHtml = `<span class="db-source-badge">Source: Agent \u2014 <a onclick="setMode('agents')">${label}</a></span>`;
      } else if (sourceMeta.type === "skill") {
        const sk = SKILLS[sourceMeta.id];
        const label = sk ? esc(sk.name) : esc(sourceMeta.id);
        sourceBadgeHtml = `<span class="db-source-badge">Source: Skill \u2014 <a onclick="setMode('skill')">${label}</a></span>`;
      }
    }
    if (tname)
      tname.innerHTML = state.curTableId + sourceBadgeHtml;
    let rows = data.rows.filter(
      (r) => !state.filterText || Object.values(r).some((v) => v && String(v).toLowerCase().includes(state.filterText))
    );
    if (state.sortCol) {
      rows = [...rows].sort((a, b) => {
        const av = a[state.sortCol] ?? "", bv = b[state.sortCol] ?? "";
        return String(av).localeCompare(String(bv), void 0, { numeric: true }) * state.sortDir;
      });
    }
    const cnt = document.getElementById("db-count");
    if (cnt)
      cnt.textContent = `${rows.length} row${rows.length !== 1 ? "s" : ""}`;
    if (rows.length === 0 && !state.filterText) {
      const thead2 = document.getElementById("db-thead");
      if (thead2)
        thead2.innerHTML = "";
      const tbody2 = document.getElementById("db-tbody");
      if (tbody2)
        tbody2.innerHTML = `<tr class="db-empty-row"><td colspan="99">No data yet. Run an Agent or Skill to start collecting.</td></tr>`;
      return;
    }
    const thead = document.getElementById("db-thead");
    if (thead)
      thead.innerHTML = "<tr>" + data.cols.map((c) => {
        const ico = state.sortCol === c ? state.sortDir > 0 ? "\u2191" : "\u2193" : "";
        return `<th onclick="sortBy('${c}')">${esc(c)} <span class="sort-ico">${ico}</span></th>`;
      }).join("") + "</tr>";
    const STATUS_COLOR = { done: "#16a34a", running: "#4f46e5", queued: "rgba(0,0,0,.45)", needs_review: "#b45309", failed: "#dc2626" };
    const mono = /* @__PURE__ */ new Set(["id", "task_id", "ts", "created_at", "input", "output", "value", "size_bytes", "seq"]);
    const tbody = document.getElementById("db-tbody");
    if (tbody)
      tbody.innerHTML = rows.map(
        (r, ri) => `<tr class="row-click" onclick="openDrawer(${ri}, ${JSON.stringify(JSON.stringify(r))})">` + data.cols.map((c) => {
          const v = r[c];
          if (v === null || v === void 0)
            return `<td><span class="db-null">null</span></td>`;
          if (c === "status")
            return `<td class="status" style="color:${STATUS_COLOR[v] || "inherit"}">${esc(String(v))}</td>`;
          if (c === "size_kb" && v > 0)
            return `<td class="mono">${Number(v).toFixed(1)} KB</td>`;
          if (c === "size_bytes" && v > 0)
            return `<td class="mono">${(v / 1024).toFixed(1)} KB</td>`;
          if (c === "sensitive")
            return `<td>${v ? "\u{1F512}" : ""}</td>`;
          const s = String(v);
          const disp = s.length > 60 ? s.slice(0, 58) + "\u2026" : s;
          return `<td class="${mono.has(c) ? "mono" : ""}">${esc(disp)}</td>`;
        }).join("") + "</tr>"
      ).join("");
  }
  function sortBy(col) {
    if (state.sortCol === col)
      state.sortDir *= -1;
    else {
      state.sortCol = col;
      state.sortDir = 1;
    }
    renderTable();
  }
  function filterTable() {
    state.filterText = document.getElementById("db-search").value.toLowerCase();
    renderTable();
  }
  function openDrawer(ri, rjson) {
    const r = JSON.parse(rjson);
    const title = document.getElementById("drawer-title");
    if (title)
      title.textContent = state.curTableId + " \xB7 row";
    const body = document.getElementById("drawer-body");
    if (body)
      body.innerHTML = Object.entries(r).map(([k, v]) => {
        const isLong = v && String(v).length > 40;
        const disp = v === null || v === void 0 ? '<span class="db-null">null</span>' : `<div class="drawer-val${isLong ? " mono" : ""}">${esc(String(v))}</div>`;
        return `<div class="drawer-field"><div class="drawer-key">${esc(k)}</div>${disp}</div>`;
      }).join('<div class="drawer-sep"></div>');
    document.getElementById("db-drawer").classList.add("on");
  }
  function closeDrawer() {
    const d = document.getElementById("db-drawer");
    if (d)
      d.classList.remove("on");
  }
  function exportCSV() {
    const data = DB[state.curTableId];
    if (!data)
      return;
    const lines = [data.cols.join(","), ...data.rows.map((r) => data.cols.map((c) => JSON.stringify(r[c] ?? "")).join(","))];
    const blob = new Blob([lines.join("\n")], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = state.curTableId + ".csv";
    a.click();
  }
  function refreshTable() {
    renderTable();
  }
  function switchDbView(tab) {
    document.querySelectorAll(".db-view-tab").forEach((t) => t.classList.remove("on"));
    const el = document.querySelector(`.db-view-tab[data-view="${tab}"]`);
    if (el)
      el.classList.add("on");
    if (tab === "cards")
      showToast("Cards view coming soon");
  }

  // src/views/skills.ts
  function selSkill(el, id) {
    document.querySelectorAll(".ski").forEach((s) => s.classList.remove("on"));
    if (el)
      el.classList.add("on");
    state.curSkill = id;
    renderSkillDetail(id);
  }
  function renderSkillDetail(id) {
    const sk = SKILLS[id];
    if (!sk)
      return;
    const runs = sk.runs.map((r, ri) => {
      const runKey = id + "_" + ri;
      const isExpanded = !!state.skillRunExpanded[runKey];
      const statusLabel = r.status === "done" ? "\u2713 Done" : r.status === "fail" ? "\u2717 Failed" : "\u23F3 Running";
      const debugBlock = r.error ? `
      <div class="sk-debug">
        <div class="sk-debug-hd">\u{1F916} AI Debug</div>
        <div class="sk-debug-msg">${esc(r.error.msg)}</div>
        <div class="sk-debug-ai">${esc(r.error.ai)}</div>
        <button class="sk-debug-fix" onclick="setMode('build')">Apply fix in Build \u2192</button>
      </div>` : "";
      return `
    <div class="sk-run-item" onclick="toggleRunExpand('${runKey}','${id}')">
      <div class="sk-run-status sk-run-${r.status}"></div>
      <div class="sk-run-info">
        <div style="display:flex;align-items:center;gap:6px">
          <span style="font-size:11.5px;font-weight:600;color:${r.status === "done" ? "#16a34a" : r.status === "fail" ? "#dc2626" : "#4f46e5"}">${statusLabel}</span>
          <span class="sk-run-ts">${r.ts}</span>
        </div>
        <div class="sk-run-params-txt">${esc(r.params)}</div>
        <div class="sk-run-detail${isExpanded ? " open" : ""}">
          ${r.elapsed ? `<div style="font-size:11px;color:rgba(0,0,0,.38);margin-bottom:4px">Elapsed: ${r.elapsed || "\u2014"}</div>` : ""}
          ${debugBlock}
          <button class="sk-run-replay" onclick="replayRun(event,'${id}',${ri})">\u21BA Replay</button>
        </div>
      </div>
      <div class="sk-run-expand">${isExpanded ? "\u25B2" : "\u25BC"}</div>
    </div>`;
    }).join("");
    const detailEl = document.getElementById("sk-detail");
    if (!detailEl)
      return;
    detailEl.innerHTML = `
    <div class="sk-detail-head">
      <div class="sk-name">${esc(sk.name)}</div>
      <div class="sk-dsc">${esc(sk.desc)}</div>
    </div>
    <div class="sk-body">
      <div>
        <div class="sk-section-lbl">Parameters</div>
        <div class="sk-params">
          ${sk.params.map((p) => `
            <div class="sk-param">
              <div class="sk-param-lbl">${esc(p.label)}</div>
              <input class="sk-param-inp" placeholder="${esc(p.placeholder)}">
            </div>`).join("")}
        </div>
      </div>
      <button class="sk-run-btn" onclick="runSkill('${id}')">&#9654; Run</button>
      <div id="sk-stream-${id}" style="display:none">
        <div class="sk-section-lbl">Output</div>
        <div class="sk-stream-panel" id="sk-stream-panel-${id}"></div>
      </div>
      ${sk.runs.length ? `<div class="sk-runs-sep"></div><div class="sk-section-lbl">Recent runs</div>${runs}` : ""}
    </div>`;
  }
  function toggleRunExpand(runKey, skillId) {
    state.skillRunExpanded[runKey] = !state.skillRunExpanded[runKey];
    renderSkillDetail(skillId);
  }
  function replayRun(e, skillId, runIdx) {
    e.stopPropagation();
    const sk = SKILLS[skillId];
    const run = sk.runs[runIdx];
    if (!run)
      return;
    const inputs = document.querySelectorAll("#sk-detail .sk-param-inp");
    const pairs = (run.params || "").split(" ");
    inputs.forEach((inp, i) => {
      const pair = pairs[i];
      if (pair)
        inp.value = pair.split("=").slice(1).join("=");
    });
    showToast("Parameters pre-filled from past run. Click Run to execute.");
  }
  function runSkill(id) {
    const sk = SKILLS[id];
    const startTime = Date.now();
    sk.runs.unshift({ status: "run", ts: "Now", params: "...", error: null, elapsed: null });
    renderSkillDetail(id);
    const streamWrap = document.getElementById("sk-stream-" + id);
    const streamPanel = document.getElementById("sk-stream-panel-" + id);
    if (streamWrap)
      streamWrap.style.display = "block";
    const willFail = id === "csvproc";
    const lines = willFail ? [
      { text: "Initializing skill\u2026", cls: "dim" },
      { text: "Loading ~/Downloads/data.csv\u2026", cls: "" },
      { text: "ERROR: FileNotFoundError: ~/Downloads/data.csv not found", cls: "err" }
    ] : [
      { text: "Initializing skill\u2026", cls: "dim" },
      { text: "Fetching data\u2026", cls: "" },
      { text: "Processing rows\u2026", cls: "" },
      { text: "Saving to database\u2026", cls: "" },
      { text: "Done. 86 rows written.", cls: "ok" }
    ];
    let i = 0;
    const interval = setInterval(() => {
      if (!streamPanel || i >= lines.length) {
        clearInterval(interval);
        const elapsed = ((Date.now() - startTime) / 1e3).toFixed(1) + "s";
        if (willFail) {
          if (sk.runs[0]) {
            sk.runs[0].status = "fail";
            sk.runs[0].ts = "Just now";
            sk.runs[0].elapsed = elapsed;
            sk.runs[0].error = {
              msg: "FileNotFoundError: ~/Downloads/data.csv not found\n  at csv_processor.py:14",
              ai: "The file path does not exist. Make sure the CSV file is in your Downloads folder, or update the input path to point to the correct file location."
            };
          }
          if (streamPanel) {
            const sep = document.createElement("div");
            sep.className = "sk-stream-line err";
            sep.textContent = "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500";
            streamPanel.appendChild(sep);
            const dbg = document.createElement("div");
            dbg.style.cssText = "padding:8px 10px;margin-top:6px;background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.20);border-radius:7px";
            dbg.innerHTML = `<div style="font-size:11px;font-weight:600;color:#dc2626;margin-bottom:4px">\u{1F916} AI Debug</div>
<div style="font-size:11.5px;color:rgba(0,0,0,.65);line-height:1.5">The file path does not exist. Make sure the CSV file is in your Downloads folder, or update the <code style="background:rgba(0,0,0,.07);padding:0 3px;border-radius:3px">input</code> path parameter to point to the correct file location.</div>
<button class="sk-debug-fix" style="margin-top:8px" onclick="setMode('build')">Apply fix in Build \u2192</button>`;
            streamPanel.appendChild(dbg);
            streamPanel.scrollTop = streamPanel.scrollHeight;
          }
        } else {
          if (sk.runs[0]) {
            sk.runs[0].status = "done";
            sk.runs[0].ts = "Just now";
            sk.runs[0].elapsed = elapsed;
          }
          if (streamPanel) {
            const output = streamPanel.textContent || "";
            const actRow = document.createElement("div");
            actRow.className = "sk-stream-actions";
            actRow.innerHTML = `<span class="sk-done-label">\u2713 Done \xB7 ${elapsed}</span>
            <button class="sk-copy-btn" onclick="navigator.clipboard.writeText(${JSON.stringify(output)}).then(()=>showToast('Output copied!'))">Copy output</button>
            <button class="sk-data-link" onclick="setMode('data')">View in Data \u2192</button>`;
            streamPanel.after(actRow);
          }
        }
        renderSkillDetail(id);
        return;
      }
      const d = document.createElement("div");
      d.className = "sk-stream-line" + (lines[i].cls ? " " + lines[i].cls : "");
      d.textContent = "> " + lines[i].text;
      if (streamPanel) {
        streamPanel.appendChild(d);
        streamPanel.scrollTop = streamPanel.scrollHeight;
      }
      i++;
    }, 350);
  }
  function renderSkillPanel(skills) {
    const container = document.getElementById("runner-skills-list");
    if (!container)
      return;
    if (!skills || skills.length === 0) {
      container.innerHTML = '<div style="font-size:12px;color:rgba(0,0,0,.35);padding:8px 0;font-style:italic">No skills loaded from ~/.gits/skills/.</div>';
      return;
    }
    container.innerHTML = skills.map((sk) => {
      const name = sk.name || "\u2014";
      const ttype = sk.trigger ? ((sk.trigger.type || sk.trigger) + "").toLowerCase() : "unknown";
      const trigLabel = ttype === "loop" ? "Loop" : ttype === "reactive" ? "Reactive" : ttype;
      const onFail = sk.on_failure || "\u2014";
      const guardEnabled = sk.guard ? !!sk.guard.enabled : false;
      const guardTxt = guardEnabled ? "\u2713 Guard on" : "\u2717 Guard off";
      const stepCount = Array.isArray(sk.steps) ? sk.steps.length : 0;
      const stepsHtml = Array.isArray(sk.steps) && sk.steps.length > 0 ? sk.steps.map((s, i) => `<div class="runner-step-item">${i + 1}. ${esc(s.tool || s.name || s.cmd || String(s))}</div>`).join("") : '<div class="runner-step-item" style="color:rgba(0,0,0,.32)">No steps defined</div>';
      return `<div class="runner-skill-card">
      <div class="runner-skill-card-head">
        <div class="runner-skill-name">${esc(name)}</div>
        <span class="runner-trigger-badge runner-trigger-${ttype}">${trigLabel}</span>
      </div>
      <div class="runner-skill-meta">
        <span class="runner-meta-chip">On fail: ${esc(String(onFail))}</span>
        <span class="runner-meta-chip${guardEnabled ? " runner-meta-chip-on" : ""}">${guardTxt}</span>
        <span class="runner-meta-chip">${stepCount} step${stepCount !== 1 ? "s" : ""}</span>
      </div>
      <div class="runner-steps-list">${stepsHtml}</div>
    </div>`;
    }).join("");
  }
  function openNewSkillModal() {
    document.getElementById("new-skill-modal").classList.add("on");
    const ta = document.getElementById("new-skill-desc");
    if (ta) {
      ta.value = "";
      setTimeout(() => ta.focus(), 80);
    }
    const preview = document.getElementById("new-skill-preview");
    if (preview)
      preview.style.display = "none";
  }
  function closeNewSkillModal(e) {
    if (e && e.target !== document.getElementById("new-skill-modal"))
      return;
    document.getElementById("new-skill-modal").classList.remove("on");
  }
  function generateNewSkill() {
    const desc = document.getElementById("new-skill-desc").value.trim();
    if (!desc)
      return;
    const btn = document.querySelector(".modal-gen-btn");
    if (btn)
      btn.textContent = "\u23F3 Generating\u2026";
    setTimeout(() => {
      if (btn)
        btn.textContent = "\u2728 Generate Skill";
      const words = desc.split(" ");
      const name = words.slice(0, 3).map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
      const preview = document.getElementById("new-skill-preview");
      const content = document.getElementById("new-skill-preview-content");
      if (content)
        content.innerHTML = `<strong>Name:</strong> ${esc(name)}<br>
<strong>Description:</strong> ${esc(desc)}<br><br>
<strong>Parameters:</strong><br>
&nbsp; \u2022 <code>symbol</code> \u2014 ticker symbol (e.g. AAPL)<br>
&nbsp; \u2022 <code>interval_min</code> \u2014 fetch interval in minutes<br><br>
<strong>Actions:</strong><br>
&nbsp; 1. Fetch data from API<br>
&nbsp; 2. Transform and clean rows<br>
&nbsp; 3. Save to Data`;
      if (preview)
        preview.style.display = "block";
    }, 900);
  }
  function saveNewSkill() {
    const desc = document.getElementById("new-skill-desc").value.trim();
    const words = desc.split(" ");
    const name = words.slice(0, 3).map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
    const newId = "custom_" + Date.now();
    SKILLS[newId] = {
      name,
      desc,
      params: [{ key: "symbol", label: "Symbol", placeholder: "AAPL" }],
      runs: []
    };
    const scroll = document.getElementById("sk-scroll");
    if (scroll) {
      const el = document.createElement("div");
      el.className = "ski";
      el.innerHTML = `<div class="ski-name">${esc(name)}</div><div class="ski-desc">${esc(desc)}</div><span class="ski-tag">custom</span>`;
      el.onclick = () => {
        selSkill(el, newId);
      };
      scroll.appendChild(el);
    }
    closeNewSkillModal();
    showToast(`\u2713 Skill "${name}" saved`);
    setTimeout(() => {
      setMode("skill");
      selSkill(null, newId);
    }, 400);
  }
  function renderSkillsList() {
    const scroll = document.getElementById("sk-scroll");
    if (!scroll || Object.keys(state.skillDefs).length === 0)
      return;
    const skills = Object.values(state.skillDefs);
    scroll.innerHTML = skills.map((sk, i) => {
      const ttype = (sk.trigger?.type || sk.trigger || "").toString().toLowerCase();
      const isOn = state.curSkill === sk.name;
      return `<div class="ski${isOn ? " on" : ""}" onclick="selSkillByName(this,'${esc(sk.name)}')">
      <div class="ski-name">${esc(sk.name)}</div>
      <div class="ski-desc">${esc(sk.description || sk.desc || sk.name)}</div>
      <span class="ski-tag">${esc(ttype)}</span>
    </div>`;
    }).join("");
    if (!state.skillDefs[state.curSkill] && skills[0])
      state.curSkill = skills[0].name;
    if (state.skillDefs[state.curSkill])
      renderSkillDetailReal(state.curSkill);
  }
  function selSkillByName(el, name) {
    document.querySelectorAll(".ski").forEach((s) => s.classList.remove("on"));
    if (el)
      el.classList.add("on");
    state.curSkill = name;
    renderSkillDetailReal(name);
  }
  function renderSkillDetailReal(name) {
    const sk = state.skillDefs[name];
    if (!sk)
      return;
    const run = state.runnerAgents[name];
    const ttype = (sk.trigger?.type || sk.trigger || "").toString().toLowerCase();
    const trigLabel = ttype === "loop" ? "Loop" : ttype === "reactive" ? "Reactive" : ttype || "\u2014";
    const stepsHtml = Array.isArray(sk.steps) && sk.steps.length ? sk.steps.map((s, i) => `<div style="font-size:12px;padding:3px 0;border-bottom:1px solid rgba(0,0,0,.05)">
        <span style="color:rgba(0,0,0,.3);margin-right:6px">${i + 1}.</span>${esc(s.tool || s.name || s.cmd || JSON.stringify(s))}
      </div>`).join("") : '<div style="font-size:12px;color:rgba(0,0,0,.3);font-style:italic">No steps defined</div>';
    let lastRunHtml = '<div style="font-size:12px;color:rgba(0,0,0,.3);font-style:italic;padding:6px 0">No runs yet</div>';
    if (run) {
      const ts = run.started_at ? new Date(run.started_at).toLocaleString() : "\u2014";
      const dur = run.started_at && run.finished_at ? Math.round((new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()) / 1e3) + "s" : "\u2014";
      const sc = { success: "done", done: "done", failed: "fail", fail: "fail", running: "run" };
      const scCls = sc[run.status] || "run";
      lastRunHtml = `<div class="sk-run-item">
      <div class="sk-run-status sk-run-${scCls}"></div>
      <div class="sk-run-info">
        <div style="font-size:11.5px;font-weight:600">${esc(run.status || "\u2014")}</div>
        <div class="sk-run-ts">${esc(ts)} \xB7 ${esc(dur)}</div>
      </div>
    </div>`;
    }
    const metaChips = [
      sk.on_failure ? `On fail: ${esc(String(sk.on_failure))}` : null,
      sk.guard?.enabled ? "\u{1F6E1} Guard on" : null,
      sk.paused ? "\u23F8 Paused" : null
    ].filter(Boolean).map((t) => `<span class="runner-meta-chip">${t}</span>`).join("");
    const detailEl = document.getElementById("sk-detail");
    if (!detailEl)
      return;
    detailEl.innerHTML = `
    <div class="sk-detail-head">
      <div class="sk-name">${esc(name)}</div>
      <div class="sk-dsc" style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
        <span class="runner-trigger-badge runner-trigger-${ttype}">${trigLabel}</span>
        ${metaChips}
      </div>
    </div>
    <div class="sk-body">
      <div>
        <div class="sk-section-lbl">Steps</div>
        <div style="padding:4px 0">${stepsHtml}</div>
      </div>
      <div>
        <div class="sk-section-lbl">Last Run</div>
        <div class="sk-runs">${lastRunHtml}</div>
      </div>
      <div class="sk-actions">
        <button class="sk-run-btn" onclick="runnerRunNow('${esc(name)}')">\u25B6 Run Now</button>
        <button class="sk-run-btn" style="background:rgba(0,0,0,.08);color:rgba(0,0,0,.6)"
          onclick="runnerTogglePauseById('${esc(name)}')">${run?._paused ? "\u25B6 Resume" : "\u23F8 Pause"}</button>
      </div>
    </div>`;
  }

  // src/ui/screenshot.ts
  async function takeScreenshot() {
    if (!window.__TAURI__) {
      showToast("Screenshot only available in the desktop app");
      return;
    }
    const invoke = window.__TAURI__.core?.invoke ?? window.__TAURI__.invoke;
    showToast("\u{1F4F8} Capturing\u2026");
    if (!window.html2canvas) {
      await new Promise((resolve, reject) => {
        const s = document.createElement("script");
        s.src = "https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js";
        s.onload = () => resolve();
        s.onerror = reject;
        document.head.appendChild(s);
      });
    }
    try {
      const canvas = await window.html2canvas(document.body, {
        backgroundColor: null,
        scale: window.devicePixelRatio || 1,
        logging: false,
        useCORS: true
      });
      const dataUrl = canvas.toDataURL("image/png");
      const path = await invoke("take_screenshot", { data: dataUrl });
      showToast(`\u{1F4F8} Saved: ${path}`);
      console.log("Screenshot saved to", path);
    } catch (err) {
      showToast(`Screenshot failed: ${err}`);
      console.error("Screenshot error:", err);
    }
  }

  // src/ipc.ts
  function installTauriShim() {
    if (window.ghost || !window.__TAURI__)
      return;
    const invoke = window.__TAURI__.core?.invoke ?? window.__TAURI__.invoke;
    const tauriEvent = window.__TAURI__.event;
    function _dbg(msg) {
      const ts = (/* @__PURE__ */ new Date()).toISOString().slice(11, 23);
      try {
        invoke("debug_log", { msg: `[${ts}] ${msg}` }).catch(() => {
        });
      } catch (e) {
      }
    }
    _dbg("installTauriShim: start, tauriEvent=" + typeof tauriEvent);
    const _listeners = [];
    function _dispatch(event, data) {
      _listeners.forEach((l) => {
        if (l.event === "*" || l.event === event)
          l.cb(data);
      });
    }
    const listenPromise = tauriEvent.listen("python-event", (e) => {
      const data = e.payload;
      _dbg("python-event received: " + JSON.stringify(data).slice(0, 120));
      if (data?.event)
        _dispatch(data.event, data);
    });
    listenPromise.then(() => {
      _dbg("tauriEvent.listen python-event: registered OK");
    }).catch((err) => {
      _dbg("tauriEvent.listen python-event ERROR: " + err);
      showToast("IPC error: " + err);
    });
    tauriEvent.listen("pty-output", (e) => {
      _dispatch("pty-output", e.payload);
    }).catch((err) => {
      _dbg("tauriEvent.listen pty-output ERROR: " + err);
    });
    window.ghost = {
      send(cmd, payload = {}) {
        _dbg("ghost.send: cmd=" + cmd);
        return invoke("python_cmd", { cmd, payload });
      },
      on(event, cb) {
        const entry = { event, cb };
        _listeners.push(entry);
        return entry;
      },
      off(handle) {
        const idx = _listeners.indexOf(handle);
        if (idx !== -1)
          _listeners.splice(idx, 1);
      },
      onAny(cb) {
        return window.ghost.on("*", cb);
      },
      _dbg
    };
    _dbg("installTauriShim: window.ghost set");
  }
  function sendPane(_idx) {
  }
  function initPtyTerminal(channelId) {
    assignSessionToPane(channelId, state.activePaneIdx);
  }
  function ghostSetup() {
    if (typeof window === "undefined" || !window.ghost)
      return;
    const _dbg = window.ghost._dbg || (() => {
    });
    _dbg("ghostSetup: start");
    window.ghost.on("ready", () => {
      _dbg("ghostSetup: ready event received");
      window.ghost.send("sessions");
      window.ghost.send("agents", {});
      window.ghost.send("skills", {});
    });
    window.ghost.on("sessions", (data) => {
      _dbg("ghostSetup: sessions count=" + (data.sessions || []).length);
      if (data.tmux_session)
        state.tmuxSession = data.tmux_session;
      renderSessions(data.sessions || []);
    });
    window.ghost.on("pane_update", (data) => {
      const sess = state.allSessions.find((s) => s.channel_id === data.channel_id);
      if (sess) {
        sess._status = data.status === "busy" ? "busy" : "idle";
      }
      renderSessionTabs();
      const grid = document.getElementById("ws-grid");
      if (grid) {
        state.panes.forEach((pane, idx) => {
          if (pane.channelId !== data.channel_id)
            return;
          const panelEl = grid.children[idx];
          if (panelEl)
            _updatePanelHeader(panelEl, pane, idx);
        });
      }
    });
    window.ghost.on("pty-output", (data) => {
      const pane = state.panes.find((p) => p.channelId === data.channel_id);
      if (!pane || !pane.terminal)
        return;
      if (data.closed) {
        pane.terminal.write("\r\n[terminal closed]\r\n");
        return;
      }
      if (data.data) {
        try {
          const bytes = Uint8Array.from(atob(data.data), (c) => c.charCodeAt(0));
          pane.terminal.write(bytes);
        } catch (e) {
          pane.terminal.write(data.data);
        }
      }
    });
    window.ghost.on("agents_list", (data) => {
      const runs = data.runs || [];
      state.runnerAgents = {};
      runs.forEach((run) => {
        const key = run.skill_name;
        if (!key)
          return;
        const existing = state.runnerAgents[key];
        if (!existing) {
          state.runnerAgents[key] = run;
          return;
        }
        const existingTs = existing.started_at ? new Date(existing.started_at).getTime() : 0;
        const newTs = run.started_at ? new Date(run.started_at).getTime() : 0;
        if (newTs > existingTs)
          state.runnerAgents[key] = run;
      });
      renderRunnerGrid();
      renderTriggerGrid();
      updateAgentBadge();
    });
    window.ghost.on("skills_list", (data) => {
      const skills = data.skills || [];
      state.skillDefs = {};
      skills.forEach((sk) => {
        if (sk.name)
          state.skillDefs[sk.name] = sk;
      });
      renderRunnerGrid();
      renderTriggerGrid();
      renderSkillsList();
      renderSkillPanel(skills);
    });
    window.ghost.on("agent_log", (data) => {
      const skillName = data.skill_name;
      if (!skillName)
        return;
      const logPanelId = "runner-log-panel-" + skillName.replace(/[^a-z0-9]/gi, "_");
      const lp = document.getElementById(logPanelId);
      if (!lp)
        return;
      const line = data.line || data.text || "";
      if (!line)
        return;
      if (lp.querySelector("div[style]"))
        lp.innerHTML = "";
      const d = document.createElement("div");
      d.className = "ag-log-row";
      d.style.cssText = "font-size:11.5px;font-family:monospace;color:rgba(0,0,0,.7);padding:2px 0;border-bottom:1px solid rgba(0,0,0,.04)";
      d.textContent = line;
      lp.appendChild(d);
      lp.scrollTop = lp.scrollHeight;
    });
    function _requestSessions() {
      _dbg("ghostSetup: sending sessions command");
      window.ghost.send("sessions").then(() => {
        _dbg("ghostSetup: sessions send OK (invoke returned)");
      }).catch((err) => {
        _dbg("ghostSetup: sessions send ERROR: " + err);
        showToast("Sessions IPC error: " + err);
      });
    }
    _requestSessions();
    window.ghost.send("agents", {}).catch(() => {
    });
    window.ghost.send("skills", {}).catch(() => {
    });
    const _sessRetry = setInterval(() => {
      if (state.allSessions.length > 0) {
        clearInterval(_sessRetry);
        return;
      }
      _requestSessions();
    }, 3e3);
  }

  // src/main.ts
  function folderPickerClick() {
    showToast("\u{1F4C1} Folder picker \u2014 ~/myproject selected");
  }
  function toggleAgentsPopover(e) {
    e.stopPropagation();
    document.getElementById("agents-popover").classList.toggle("on");
  }
  function closeAgentsPopover() {
    document.getElementById("agents-popover").classList.remove("on");
  }
  (function init() {
    renderGrid();
    renderFleet();
    const pop = document.getElementById("agents-popover-list");
    if (pop) {
      let html = "";
      AGENTS.browser.profiles.forEach((p) => p.agents.filter((a) => a.status === "running").forEach((a) => {
        html += `<div class="ap-item"><span>\u25B6</span><span class="ap-name">${esc(a.name)}</span><span class="ap-status">running</span></div>`;
      }));
      AGENTS.loop.filter((a) => a.status === "running").forEach((a) => {
        html += `<div class="ap-item"><span>\u25B6</span><span class="ap-name">${esc(a.name)}</span><span class="ap-status">running</span></div>`;
      });
      pop.innerHTML = html || '<div class="ap-item" style="color:rgba(0,0,0,.38)">No active agents</div>';
    }
    renderDataTree();
    renderSkillDetail("market");
    renderTable();
    updateAgentBadge();
    updateAgentsWarnBadge();
    function _trySetup(attempts) {
      installTauriShim();
      if (window.ghost) {
        ghostSetup();
      } else if (attempts > 0) {
        setTimeout(() => _trySetup(attempts - 1), 100);
      } else {
        showToast("No bridge \u2014 running in browser mode");
        renderGrid();
      }
    }
    _trySetup(20);
  })();
  document.addEventListener("keydown", (e) => {
    if (e.metaKey && e.shiftKey && e.key === "s") {
      e.preventDefault();
      takeScreenshot();
    }
  });
  document.addEventListener("click", (e) => {
    const target = e.target;
    if (!target.closest(".slash-menu") && !target.closest(".wsp-ta") && !target.closest(".irow")) {
      document.querySelectorAll(".slash-menu").forEach((m) => m.classList.remove("on"));
    }
    if (!target.closest("#agents-popover") && !target.closest("#tb-agents-btn")) {
      closeAgentsPopover();
    }
  });
  window.setMode = setMode;
  window.folderPickerClick = folderPickerClick;
  window.toggleAgentsPopover = toggleAgentsPopover;
  window.closeAgentsPopover = closeAgentsPopover;
  window.toggleDevMode = toggleDevMode;
  window.focusPane = focusPane;
  window.openSessionPicker = openSessionPicker;
  window.closeSessPickerModal = closeSessPickerModal;
  window.newSessionFromPicker = newSessionFromPicker;
  window.pickSession = pickSession;
  window.openFleetDrawer = openFleetDrawer;
  window.closeFleetDrawer = closeFleetDrawer;
  window.selProfile = selProfile;
  window.openRunnerDrawer = openRunnerDrawer;
  window.runnerRunNow = runnerRunNow;
  window.runnerTogglePause = runnerTogglePause;
  window.runnerTogglePauseById = runnerTogglePauseById;
  window.toggleDataNode = toggleDataNode;
  window.selDataTable = selDataTable;
  window.selTable = selTable;
  window.openDrawer = openDrawer;
  window.closeDrawer = closeDrawer;
  window.exportCSV = exportCSV;
  window.refreshTable = refreshTable;
  window.switchDbView = switchDbView;
  window.filterTable = filterTable;
  window.sortBy = sortBy;
  window.selSkill = selSkill;
  window.runSkill = runSkill;
  window.toggleRunExpand = toggleRunExpand;
  window.replayRun = replayRun;
  window.openNewSkillModal = openNewSkillModal;
  window.closeNewSkillModal = closeNewSkillModal;
  window.generateNewSkill = generateNewSkill;
  window.saveNewSkill = saveNewSkill;
  window.showToast = showToast;
  window.dismissToast = dismissToast;
  window.takeScreenshot = takeScreenshot;
  window.sendPane = sendPane;
  window.initPtyTerminal = initPtyTerminal;
  window.cpc = cpc;
  window.ar = ar;
  window.hk = hk;
  window.send = send;
  window.addm = addm;
  window.selSkillByName = selSkillByName;
  window.renderSkillDetailReal = renderSkillDetailReal;
})();
