// ════════════════════════════════════════════════════════════
//  Ghost Desktop App — app.js
// ════════════════════════════════════════════════════════════

// ── Mock Data ─────────────────────────────────────────────────────────────

const AGENTS = {
  browser: {
    profiles: [
      {
        id: 'personal-chrome',
        label: 'Personal Chrome',
        agents: [
          {
            id: 'nash-reporter',
            name: 'Nash-AI Reporter',
            status: 'running',
            sub: 'step 8 of ~12 · 43s',
            type: 'Browser Agent',
            profile: 'Personal Chrome',
            detail: {
              running: true,
              steps: 8,
              totalSteps: 12,
              elapsed: '43s',
              log: [
                {ico:'🧭', action:'Navigate', desc:'nash-ai.cn/login', out:'✓ Already logged in (session active)', ts:'2:43:00', done:true},
                {ico:'📸', action:'Snapshot', desc:'found 47 reports', out:'', ts:'2:43:01', done:true},
                {ico:'⬇', action:'Download', desc:'gs_q2_2024.pdf · 2.4MB…', out:'', ts:'2:43:10', done:false, pending:true},
              ],
              hitl: null,
            }
          },
          {
            id: 'hn-digest',
            name: 'HN Daily Digest',
            status: 'done',
            sub: 'ran 2h ago',
            type: 'Browser Agent',
            profile: 'Personal Chrome',
            detail: {
              running: false,
              steps: 6,
              totalSteps: 6,
              elapsed: '18s',
              log: [
                {ico:'🧭', action:'Navigate', desc:'news.ycombinator.com', out:'', ts:'12:01:00', done:true},
                {ico:'📸', action:'Snapshot', desc:'found 30 stories', out:'', ts:'12:01:01', done:true},
                {ico:'💾', action:'Save to DB', desc:'inserted 10 top links', out:'→ saved', ts:'12:01:04', done:true},
              ],
              hitl: null,
            }
          },
        ]
      },
      {
        id: 'work-chrome',
        label: 'Work Chrome',
        agents: []
      },
    ]
  },
  loop: [
    {
      id: 'btc-monitor',
      name: 'BTC Price Monitor',
      status: 'running',
      sub: 'Every 60 min · next 4m',
      type: 'Loop Agent',
      detail: {
        running: true,
        steps: 3, totalSteps: 3, elapsed: '2s',
        log: [
          {ico:'⚡', action:'Evaluate', desc:'GET /api/btc/price', out:'→ $67,432.18', ts:'14:00:00', done:true},
          {ico:'💾', action:'Save', desc:'INSERT btc_price', out:'→ ok', ts:'14:00:01', done:true},
        ],
        hitl: null,
      }
    },
    {
      id: 'hn-digest-loop',
      name: 'HN Digest',
      status: 'done',
      sub: 'Daily 9am · ran 2h ago',
      type: 'Loop Agent',
      detail: {
        running: false,
        steps: 6, totalSteps: 6, elapsed: '18s',
        log: [
          {ico:'🧭', action:'Navigate', desc:'news.ycombinator.com', out:'', ts:'09:00:00', done:true},
          {ico:'💾', action:'Save', desc:'10 links saved', out:'→ ok', ts:'09:00:04', done:true},
        ],
        hitl: null,
      }
    }
  ],
  reactive: [
    {
      id: 'discord-webhook',
      name: 'Discord Webhook',
      status: 'listening',
      sub: 'Listening · #dev channel',
      type: 'Reactive Agent',
      detail: {
        running: false,
        steps: 0, totalSteps: 0, elapsed: '—',
        log: [],
        hitl: null,
      }
    }
  ]
};

const SKILLS = {
  market: {
    name: 'Market Scanner',
    desc: 'Fetches latest price data for a list of ticker symbols and saves results to the database.',
    params: [
      {key:'symbols', label:'Symbols', placeholder:'BTC,ETH,AAPL,TSLA'},
      {key:'interval', label:'Interval (min)', placeholder:'60'},
    ],
    runs: [
      {status:'done', ts:'Today 14:41', params:'symbols=BTC,ETH interval=60', error:null},
      {status:'fail', ts:'Today 11:22', params:'symbols=BTC,ETH,XRP interval=30',
        error:{msg:"KeyError: 'XRP' not found in price feed\n  at fetch_price.py:42",
               ai:"The symbol 'XRP' isn't supported by the current price feed adapter. Either remove it from the list or add a fallback handler in `fetch_price.py` for unsupported symbols."}},
      {status:'done', ts:'Yesterday 09:00', params:'symbols=BTC,ETH interval=60', error:null},
    ]
  },
  screenshot: {
    name: 'Screenshot Monitor',
    desc: 'Captures a page screenshot at a given interval and saves diffs as artifacts.',
    params: [
      {key:'url', label:'URL', placeholder:'https://example.com'},
      {key:'interval_s', label:'Interval (sec)', placeholder:'300'},
    ],
    runs: [
      {status:'run', ts:'Now', params:'url=https://nash-ai.cn interval_s=300', error:null},
    ]
  },
  csvproc: {
    name: 'CSV Processor',
    desc: 'Loads a CSV file, applies a transform script, and outputs a cleaned version.',
    params: [
      {key:'input', label:'Input path', placeholder:'~/Downloads/data.csv'},
      {key:'script', label:'Transform', placeholder:'drop_duplicates, fill_nulls'},
    ],
    runs: []
  },
  report: {
    name: 'Report Generator',
    desc: 'Queries the local database and renders a formatted PDF report.',
    params: [
      {key:'query', label:'SQL query', placeholder:"SELECT * FROM tasks WHERE status='done'"},
      {key:'title', label:'Report title', placeholder:'Weekly Summary'},
    ],
    runs: [
      {status:'done', ts:'Yesterday 18:00', params:'title=Weekly Summary', error:null},
    ]
  },
  discord: {
    name: 'Discord Notifier',
    desc: 'Posts a formatted embed message to a specified Discord channel.',
    params: [
      {key:'channel', label:'Channel ID', placeholder:'1234567890'},
      {key:'message', label:'Message', placeholder:'Task completed successfully!'},
    ],
    runs: [
      {status:'fail', ts:'Today 10:05', params:'channel=123456',
        error:{msg:"HTTPError 403: Missing Permissions\n  at discord_notify.py:28",
               ai:"The bot token doesn't have the 'Send Messages' permission in that channel. Grant the permission in Discord server settings under Roles, or use a channel where the bot already has access."}},
    ]
  },
};

const DB_COLLECTIONS = {
  fromAgents: [
    {id:'btc_prices',    name:'btc_prices',    rows:128, updated:'2m ago', icon:'📊', table:'btc_prices'},
    {id:'hn_links',      name:'hn_links',       rows:340, updated:'2h ago', icon:'🔗', table:'hn_links'},
    {id:'nash_reports',  name:'nash_reports',   rows:47,  updated:'10m ago',icon:'📄', table:'nash_reports'},
  ],
  fromSkills: [
    {id:'market_scans',  name:'market_scans',   rows:86,  updated:'14m ago',icon:'📈', table:'market_scans'},
    {id:'screenshots',   name:'screenshots',    rows:12,  updated:'1h ago', icon:'🖼', table:'screenshots'},
  ],
  manual: [
    {id:'notes',         name:'notes',          rows:8,   updated:'3d ago', icon:'📝', table:'notes'},
  ]
};

const DB = {
  tasks: {
    cols: ['id','goal','status','profile','created_at','summary'],
    rows: [
      {id:'tsk_01hw8m',goal:'Find the current BTC price on CoinGecko and save it',status:'done',profile:'Personal',created_at:'2026-03-14 14:41:02',summary:'BTC price $67,432.18 extracted and saved'},
      {id:'tsk_01hw9k',goal:'Download Goldman Sachs Q2 report from Nash-AI',status:'running',profile:'nash-ai',created_at:'2026-03-14 14:43:00',summary:null},
      {id:'tsk_01hwaq',goal:'Log in to Notion and export "Week 12" page as PDF',status:'needs_review',profile:'Work',created_at:'2026-03-14 14:45:00',summary:null},
      {id:'tsk_01hwbr',goal:'Search HackerNews for "AI agents" and save top 10 links',status:'queued',profile:'Personal',created_at:'2026-03-14 14:47:00',summary:null},
    ]
  },
  btc_prices: {
    cols: ['id','price','ts'],
    rows: Array.from({length:10},(_,i)=>({id:'btc_'+i, price:'$'+(67000+i*10)+'.00', ts:`2026-03-15 ${String(14-i).padStart(2,'0')}:00:00`}))
  },
  hn_links: {
    cols: ['id','title','url','score'],
    rows: [
      {id:1,title:'Show HN: Ghost agent fleet',url:'https://news.ycombinator.com/item?id=1',score:342},
      {id:2,title:'LLM agents in production',url:'https://news.ycombinator.com/item?id=2',score:287},
      {id:3,title:'Browser automation with real Chrome',url:'https://news.ycombinator.com/item?id=3',score:201},
    ]
  },
  nash_reports: {
    cols: ['id','filename','size_kb','downloaded_at'],
    rows: [
      {id:'rpt_1',filename:'gs_q2_2024.pdf',size_kb:2345,downloaded_at:'2026-03-15 14:43:10'},
    ]
  },
  market_scans: {
    cols: ['id','symbol','price','ts'],
    rows: [
      {id:1,symbol:'BTC',price:'$67,432.18',ts:'2026-03-15 14:41:00'},
      {id:2,symbol:'ETH',price:'$3,210.55',ts:'2026-03-15 14:41:00'},
    ]
  },
  screenshots: {cols:['id','url','ts'],rows:[]},
  notes: {cols:['id','text','created_at'],rows:[{id:1,text:'Check Nash-AI reports weekly',created_at:'2026-03-12'}]},
};

// ── Data file tree ─────────────────────────────────────────────────────────
const DATA_FILES = [
  {
    type:'folder', id:'f-agents', name:'agents', open:true,
    children:[
      {
        type:'sqlite', id:'db-btc', name:'btc_monitor.db', open:false,
        tables:[{id:'btc_prices', name:'btc_prices', rows:128}]
      },
      {
        type:'sqlite', id:'db-hn', name:'hn_digest.db', open:true,
        tables:[
          {id:'hn_links', name:'hn_links', rows:340},
          {id:'nash_reports', name:'nash_reports', rows:47}
        ]
      }
    ]
  },
  {
    type:'folder', id:'f-skills', name:'skills', open:false,
    children:[
      {
        type:'sqlite', id:'db-market', name:'market.db', open:false,
        tables:[{id:'market_scans', name:'market_scans', rows:86}]
      },
      {type:'folder', id:'f-screenshots', name:'screenshots', open:false, children:[]}
    ]
  },
  {type:'csv', id:'csv-notes', name:'notes.csv', tableId:'notes', rows:8}
];

// ── State ─────────────────────────────────────────────────────────────────
let curMode = 'build';
let activeWsTab = 0;
let devMode = false;
let curAgentId = 'nash-reporter';
let curProfileId = 'personal-chrome';
let curSkill = 'market';
let curTableId = 'btc_prices';
let sortCol = null, sortDir = 1;
let filterText = '';
let slashMenuOpen = false;
let toastTimer = null;

// ── Utility ───────────────────────────────────────────────────────────────
function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── Mode switching ─────────────────────────────────────────────────────────
function setMode(mode) {
  curMode = mode;
  document.querySelectorAll('.mode').forEach(m => m.classList.remove('on'));
  const modeEl = document.querySelector(`.mode[data-mode="${mode}"]`);
  if (modeEl) modeEl.classList.add('on');
  document.querySelectorAll('.view').forEach(v => v.classList.remove('on'));
  const viewEl = document.getElementById('view-' + mode);
  if (viewEl) viewEl.classList.add('on');
  // update badge visibility
  updateAgentBadge();
}

function updateAgentBadge() {
  const badge = document.getElementById('agents-badge');
  if (!badge) return;
  const running = countRunningAgents();
  badge.textContent = running > 0 ? `[${running}]` : '';
}

function countRunningAgents() {
  let n = 0;
  AGENTS.browser.profiles.forEach(p => p.agents.forEach(a => { if (a.status === 'running') n++; }));
  AGENTS.loop.forEach(a => { if (a.status === 'running') n++; });
  AGENTS.reactive.forEach(a => { if (a.status === 'listening') n++; });
  return n;
}

// ── Titlebar ───────────────────────────────────────────────────────────────
function folderPickerClick() {
  showToast('📁 Folder picker — ~/myproject selected');
}

function toggleAgentsPopover(e) {
  e.stopPropagation();
  document.getElementById('agents-popover').classList.toggle('on');
}

function closeAgentsPopover() {
  document.getElementById('agents-popover').classList.remove('on');
}

// ── Build view ─────────────────────────────────────────────────────────────
function selectBuildTab(idx) {
  activeWsTab = idx;
  document.querySelectorAll('.ws-tab').forEach((t,i) => t.classList.toggle('on', i===idx));
  document.querySelectorAll('.ws-panel').forEach((p,i) => p.classList.toggle('active', i===idx));
}

function toggleDevMode() {
  devMode = !devMode;
  document.getElementById('ws-grid').classList.toggle('devmode', devMode);
  document.getElementById('devmode-btn').classList.toggle('on', devMode);
  if (devMode) {
    const inp = document.querySelector(`#wsp-${activeWsTab} .wsp-tinp`);
    if (inp) setTimeout(() => inp.focus(), 50);
  }
}

function addWindow() {
  const tabs = document.querySelectorAll('.ws-tab');
  const n = tabs.length;
  const tab = document.createElement('div');
  tab.className = 'ws-tab';
  tab.id = 'wstab-' + n;
  tab.innerHTML = `<span class="ws-tab-dot d"></span>window ${n+1}`;
  tab.onclick = () => selectBuildTab(n);
  document.querySelector('.ws-tab-add').before(tab);
  selectBuildTab(n);
}

function addPane() {
  const panels = document.querySelectorAll('.ws-panel');
  const n = panels.length;
  const addCell = document.querySelector('.ws-panel-add');
  const panel = document.createElement('div');
  panel.className = 'ws-panel';
  panel.id = 'wsp-' + n;
  panel.innerHTML = `
    <div class="wsp-head" onclick="selectBuildTab(${n})">
      <div class="pane-status">
        <span class="pane-dot stopped"></span>
        <span class="pane-status-stopped">&#9724; Stopped</span>
      </div>
      <span class="wsp-name" style="margin-left:6px">pane ${n+1}</span>
      <span class="wsp-ai">claude</span>
    </div>
    <div class="wsp-chat wsp-msgs" id="wspc-${n}">
      <div class="msg ai"><div class="mav">🤖</div><div>
        <div class="bbl"><p>Ready at <code>~/myproject</code>.</p></div>
      </div></div>
    </div>
    <div class="wsp-term" id="wspt-${n}">
      <div class="tl-dim">ghost · ~/myproject</div><div class="tl-dim">&#9607;</div>
    </div>
    <div class="wsp-input wsp-chat" style="position:relative">
      <textarea class="wsp-ta" placeholder="Build something in ~/myproject…" rows="1"
        oninput="ar(this)" onkeydown="buildPaneKey(event,${n})"></textarea>
      <button class="wsp-sbtn">&#8593;</button>
      <div class="slash-menu" id="slash-menu-${n}">
        <div class="slash-item" onclick="insertSlashCmd('/agent',${n})">/agent<span class="slash-desc"> Deploy a new agent</span></div>
        <div class="slash-item" onclick="insertSlashCmd('/skill',${n})">/skill<span class="slash-desc"> Run a saved skill</span></div>
        <div class="slash-item" onclick="insertSlashCmd('/data',${n})">/data<span class="slash-desc"> Query your data</span></div>
        <div class="slash-item" onclick="insertSlashCmd('/status',${n})">/status<span class="slash-desc"> Show fleet status</span></div>
      </div>
    </div>
    <div class="wsp-tinput">
      <span class="wsp-ps">myproject $</span>
      <input class="wsp-tinp" placeholder="command…" onkeydown="gridTermKey(event,${n})">
    </div>`;
  addCell.before(panel);
  selectBuildTab(n);
}

function buildPaneKey(e, idx) {
  const ta = e.target;
  const menu = document.getElementById('slash-menu-' + idx);
  if (e.key === 'Escape') {
    if (menu) menu.classList.remove('on');
    return;
  }
  if (e.key === '/' && ta.value === '') {
    setTimeout(() => { if (menu) menu.classList.add('on'); }, 0);
    return;
  }
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    if (menu) menu.classList.remove('on');
    // submit in panel 0 goes to full send()
    if (idx === 0) send();
  }
  // hide menu when backspace clears the /
  if (e.key === 'Backspace' && ta.value.length <= 1) {
    if (menu) menu.classList.remove('on');
  }
}

function insertSlashCmd(cmd, idx) {
  const ta = document.querySelector(`#wsp-${idx} .wsp-ta`);
  if (ta) { ta.value = cmd + ' '; ta.focus(); }
  const menu = document.getElementById('slash-menu-' + idx);
  if (menu) menu.classList.remove('on');
}

function toggleSlashMenu(show, menuEl) {
  if (show) menuEl.classList.add('on');
  else menuEl.classList.remove('on');
}

function gridTermKey(e, idx) {
  if (e.key !== 'Enter') return;
  const inp = e.target, cmd = inp.value.trim();
  if (!cmd) return;
  inp.value = '';
  const sc = document.getElementById('wspt-' + idx);
  const add = (cls, txt) => {
    const d = document.createElement('div'); d.className = cls; d.textContent = txt; sc.appendChild(d);
  };
  add('tl-cmd', '$ ' + cmd);
  if (cmd.startsWith('uv run pytest')) {
    add('tl-out', 'collecting…');
    setTimeout(() => { add('tl-ok', '✓ all passed'); sc.scrollTop = 9999; }, 700);
  } else if (cmd === 'clear') {
    sc.innerHTML = '';
  } else {
    add('tl-out', 'running…');
    setTimeout(() => { add('tl-dim', '[done]'); sc.scrollTop = 9999; }, 400);
  }
  sc.scrollTop = 9999;
}

// ── Chat (build pane 0) ────────────────────────────────────────────────────
const REPLIES = [
  "Sure! Here's the Discord adapter wired with a 30s timeout:\n```python\nasync def handle(self, msg):\n    try:\n        async with asyncio.timeout(30):\n            async for chunk in engine.stream_output(session_id):\n                await msg.channel.send(chunk.text)\n    except TimeoutError:\n        await msg.channel.send('⏱ Timed out.')\n```",
  "Done! `src/gits/adapters/discord/bot.py` updated. Run `uv run pytest` to verify.",
  "Want me to add a `/agent` command that hands off to the Agent fleet? Users can trigger agents directly from Build mode.",
  "All good. The async generator streams correctly — no more blocking on output.",
];
let ri = 0;

function ar(ta) { ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight, 100) + 'px'; }

function hk(e) {
  if (e.key === 'Escape') {
    const menu = document.getElementById('slash-menu-0');
    if (menu) menu.classList.remove('on');
    return;
  }
  if (e.key === '/') {
    const ta = e.target;
    if (ta.value === '') {
      setTimeout(() => {
        const menu = document.getElementById('slash-menu-0');
        if (menu) menu.classList.add('on');
      }, 0);
    }
    return;
  }
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    const menu = document.getElementById('slash-menu-0');
    if (menu) menu.classList.remove('on');
    send();
  }
}

function send() {
  const inp = document.getElementById('build-inp');
  const txt = inp.value.trim();
  if (!txt) return;
  addm('usr', txt);
  inp.value = ''; ar(inp);
  document.getElementById('typing').style.display = 'flex';
  scrl();
  setTimeout(() => {
    document.getElementById('typing').style.display = 'none';
    addm('ai', REPLIES[ri++ % REPLIES.length]);
  }, 900 + Math.random() * 500);
}

function addm(role, text) {
  const now = new Date().toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
  const d = document.createElement('div');
  d.className = 'msg ' + role;
  d.innerHTML = `<div class="mav">${role==='ai'?'🤖':'W'}</div><div><div class="bbl">${fmt(text)}</div><div class="mt">${now}</div></div>`;
  const msgs = document.getElementById('build-msgs');
  msgs.insertBefore(d, document.getElementById('typing'));
  scrl();
}

function fmt(t) {
  t = t.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, l, c) =>
    `<pre><code>${esc(c.trim())}</code><button class="ccbtn" onclick="cpc(this)">Copy</button></pre>`);
  t = t.replace(/`([^`]+)`/g, '<code>$1</code>');
  return t.split('\n').map(l => `<p>${l||'&nbsp;'}</p>`).join('');
}

function scrl() {
  const c = document.getElementById('build-msgs');
  if (c) setTimeout(() => c.scrollTop = c.scrollHeight, 10);
}

function cpc(btn) {
  navigator.clipboard.writeText(btn.previousElementSibling.textContent).catch(() => {});
  btn.textContent = 'Copied!';
  setTimeout(() => btn.textContent = 'Copy', 1500);
}

// ── Agents view ────────────────────────────────────────────────────────────
function _flatAgent(id) {
  for (const p of AGENTS.browser.profiles) {
    for (const a of p.agents) { if (a.id === id) return a; }
  }
  for (const a of AGENTS.loop) { if (a.id === id) return a; }
  for (const a of AGENTS.reactive) { if (a.id === id) return a; }
  return null;
}

function _fleetCardHTML(a) {
  const badgeMap = {
    running:  ['fleet-badge-running',  '▶ Running'],
    done:     ['fleet-badge-done',     '✓ Done'],
    idle:     ['fleet-badge-idle',     '⏸ Idle'],
    listening:['fleet-badge-listening','● Listening'],
    waiting:  ['fleet-badge-waiting',  '⚠ Waiting'],
  };
  const [badgeCls, badgeTxt] = badgeMap[a.status] || ['fleet-badge-idle', a.status];
  return `<div class="fleet-card ${a.status}${curAgentId===a.id?' on':''}" onclick="openFleetDrawer(this,'${a.id}')">
    <div class="fleet-card-top">
      <div class="fleet-card-name">${esc(a.name)}</div>
      <div class="fleet-card-badge ${badgeCls}">${badgeTxt}</div>
    </div>
    <div class="fleet-card-sub">${esc(a.sub)}</div>
    <div class="fleet-card-type">${esc(a.type)}</div>
  </div>`;
}

function renderFleet() {
  // Profile cards
  const profileRow = document.getElementById('profile-row');
  if (profileRow) {
    let html = '';
    AGENTS.browser.profiles.forEach(p => {
      const running = p.agents.filter(a => a.status === 'running').length;
      const count = p.agents.length;
      const isOn = p.id === curProfileId;
      const statusCls = running > 0 ? 'running' : 'idle';
      const statusTxt = running > 0 ? `▶ ${running} running` : count > 0 ? '⏸ Idle' : '● No agents';
      html += `<div class="profile-card${isOn?' on':''}" onclick="selProfile(this,'${p.id}')">
        <div class="profile-card-ico">🌐</div>
        <div class="profile-card-name">${esc(p.label)}</div>
        <div class="profile-card-count">${count} agent${count!==1?'s':''}</div>
        <div class="profile-card-status ${statusCls}">${statusTxt}</div>
      </div>`;
    });
    html += `<div class="profile-card add" onclick="showToast('Add Chrome profile — coming soon')">＋<br>Add Profile</div>`;
    profileRow.innerHTML = html;
  }
  renderProfileAgents(curProfileId);

  // Loop grid
  const loopGrid = document.getElementById('loop-grid');
  if (loopGrid) {
    let html = AGENTS.loop.map(_fleetCardHTML).join('');
    html += `<div class="fleet-card-add" onclick="showToast('Type /agent in Build to create a Loop Agent')">＋ New Loop Agent</div>`;
    loopGrid.innerHTML = html;
  }

  // Reactive grid
  const reactiveGrid = document.getElementById('reactive-grid');
  if (reactiveGrid) {
    let html = AGENTS.reactive.map(_fleetCardHTML).join('');
    html += `<div class="fleet-card-add" onclick="showToast('Type /agent in Build to create a Reactive Agent')">＋ New Reactive Agent</div>`;
    reactiveGrid.innerHTML = html;
  }
}

function renderProfileAgents(profileId) {
  const profile = AGENTS.browser.profiles.find(p => p.id === profileId);
  const row = document.getElementById('profile-agents-row');
  if (!row || !profile) return;
  if (profile.agents.length === 0) {
    row.innerHTML = `<div style="padding:6px 10px;font-size:12px;color:rgba(0,0,0,.35);font-style:italic">No agents in this profile. Ask the Build agent to create one.</div>`;
    return;
  }
  row.innerHTML = profile.agents.map(_fleetCardHTML).join('');
}

function selProfile(el, profileId) {
  curProfileId = profileId;
  document.querySelectorAll('.profile-card').forEach(c => c.classList.remove('on'));
  if (el) el.classList.add('on');
  renderProfileAgents(profileId);
}

function openFleetDrawer(el, agentId) {
  curAgentId = agentId;
  document.querySelectorAll('.fleet-card').forEach(c => c.classList.remove('on'));
  if (el) el.classList.add('on');
  document.getElementById('fleet-drawer').classList.add('on');
  _renderFleetDrawer(agentId);
}

function closeFleetDrawer() {
  document.getElementById('fleet-drawer').classList.remove('on');
  document.querySelectorAll('.fleet-card').forEach(c => c.classList.remove('on'));
  curAgentId = null;
}

function _renderFleetDrawer(agentId) {
  const a = _flatAgent(agentId);
  const titleEl = document.getElementById('fleet-drawer-title');
  const bodyEl = document.getElementById('fleet-drawer-body');
  if (!a || !bodyEl) return;
  if (titleEl) titleEl.textContent = a.name + ' · ' + a.type;

  const d = a.detail;
  const runProgress = d.running
    ? `▶ Running · step ${d.steps} of ~${d.totalSteps} · ${d.elapsed} elapsed`
    : a.status === 'done' ? `✓ Done · ${d.steps} steps · ${d.elapsed}`
    : a.status === 'listening' ? '● Listening for events…'
    : '⏸ Idle';

  const logRows = d.log.map(l => {
    const out = l.out ? `<div class="ag-log-out">${esc(l.out)}</div>` : '';
    const pending = l.pending ? `<div class="ag-log-pending">⏳ in progress…</div>` : '';
    return `<div class="ag-log-row">
      <div class="ag-log-ico">${l.ico}</div>
      <div class="ag-log-body">
        <div class="ag-log-action">${l.action}</div>
        <div class="ag-log-desc">${esc(l.desc)}</div>${out}${pending}
      </div>${l.ts ? `<div class="ag-log-ts">${l.ts}</div>` : ''}
    </div>`;
  }).join('');

  const chromeBanner = a.type === 'Browser Agent' ? `
    <div class="ag-chrome-banner">
      <div class="ag-chrome-banner-inner">🌐 Real Chrome · ${esc(a.profile)} — your sessions, your cookies, no re-logging in</div>
    </div>` : '';

  bodyEl.innerHTML = `
    <div class="drawer-col-left">
      ${chromeBanner}
      <div class="ag-run-bar"><div class="ag-run-status">${runProgress}</div></div>
      <div class="ag-actions">
        <button class="ag-btn">⏸ Pause</button>
        <button class="ag-btn">▶ Run Now</button>
        <button class="ag-btn link" onclick="setMode('data')">View in Data →</button>
      </div>
    </div>
    <div class="drawer-col-right">
      <div class="ag-log-hd">— Execution Log</div>
      <div class="ag-log-scroll">${logRows || '<div style="color:rgba(0,0,0,.30);font-size:12px;padding:8px 0">No log entries yet.</div>'}</div>
    </div>`;
}

// ── Data file tree ──────────────────────────────────────────────────────────
function _findDataNode(nodes, id) {
  for (const n of nodes) {
    if (n.id === id) return n;
    if (n.children) { const f = _findDataNode(n.children, id); if (f) return f; }
  }
  return null;
}

function _renderTreeNodes(nodes, depth) {
  const pad = d => `padding-left:${10 + d * 16}px`;
  let html = '';
  nodes.forEach(n => {
    if (n.type === 'folder') {
      const arrow = n.children.length
        ? `<span class="dtree-arrow${n.open?' open':''}" style="margin-left:auto">›</span>` : '';
      html += `<div class="dtree-node" style="${pad(depth)}" onclick="toggleDataNode('${n.id}')">
        <span class="dtree-ico">📁</span><span class="dtree-name">${esc(n.name)}</span>${arrow}</div>`;
      if (n.children.length) {
        html += `<div class="dtree-children${n.open?' open':''}" id="dtree-ch-${n.id}">`;
        html += _renderTreeNodes(n.children, depth + 1);
        html += '</div>';
      }
    } else if (n.type === 'sqlite') {
      const arrow = `<span class="dtree-arrow${n.open?' open':''}" style="margin-left:auto">›</span>`;
      html += `<div class="dtree-node" style="${pad(depth)}" onclick="toggleDataNode('${n.id}')">
        <span class="dtree-ico">🗄</span><span class="dtree-name">${esc(n.name)}</span>${arrow}</div>`;
      html += `<div class="dtree-children${n.open?' open':''}" id="dtree-ch-${n.id}">`;
      n.tables.forEach(t => {
        const sel = curTableId === t.id;
        html += `<div class="dtree-node table-item${sel?' sel':''}" style="${pad(depth+1)}"
          onclick="selDataTable(this,'${t.id}')">
          <span class="dtree-ico" style="font-size:10px;color:rgba(0,0,0,.30)">↳</span>
          <span class="dtree-name">${esc(t.name)}</span>
          <span class="dtree-count">${t.rows}</span>
        </div>`;
      });
      html += '</div>';
    } else if (n.type === 'csv') {
      const sel = curTableId === n.tableId;
      html += `<div class="dtree-node${sel?' sel':''}" style="${pad(depth)}"
        onclick="selDataTable(this,'${n.tableId}')">
        <span class="dtree-ico">📄</span>
        <span class="dtree-name">${esc(n.name)}</span>
        <span class="dtree-count">${n.rows}</span>
      </div>`;
    }
  });
  return html;
}

function renderDataTree() {
  const inner = document.getElementById('data-tree-inner');
  if (inner) inner.innerHTML = _renderTreeNodes(DATA_FILES, 0);
}

function toggleDataNode(nodeId) {
  const node = _findDataNode(DATA_FILES, nodeId);
  if (node) { node.open = !node.open; renderDataTree(); }
}

function selDataTable(el, tableId) {
  curTableId = tableId;
  sortCol = null; sortDir = 1; filterText = '';
  const s = document.getElementById('db-search');
  if (s) s.value = '';
  closeDrawer();
  renderDataTree();
  renderTable();
}

// ── Skill view ─────────────────────────────────────────────────────────────
function selSkill(el, id) {
  document.querySelectorAll('.ski').forEach(s => s.classList.remove('on'));
  if (el) el.classList.add('on');
  curSkill = id;
  renderSkillDetail(id);
}

function renderSkillDetail(id) {
  const sk = SKILLS[id];
  if (!sk) return;

  const runs = sk.runs.map(r => `
    <div class="sk-run-item">
      <div class="sk-run-status sk-run-${r.status}"></div>
      <div class="sk-run-info">
        <div class="sk-run-ts">${r.ts}</div>
        <div class="sk-run-params-txt">${esc(r.params)}</div>
        ${r.error ? `
        <div class="sk-debug">
          <div class="sk-debug-hd">🤖 AI Debug</div>
          <div class="sk-debug-msg">${esc(r.error.msg)}</div>
          <div class="sk-debug-ai">${esc(r.error.ai)}</div>
          <button class="sk-debug-fix" onclick="setMode('build')">Apply fix in Build →</button>
        </div>` : ''}
      </div>
    </div>`).join('');

  document.getElementById('sk-detail').innerHTML = `
    <div class="sk-detail-head">
      <div class="sk-name">${esc(sk.name)}</div>
      <div class="sk-dsc">${esc(sk.desc)}</div>
    </div>
    <div class="sk-body">
      <div>
        <div class="sk-section-lbl">Parameters</div>
        <div class="sk-params">
          ${sk.params.map(p => `
            <div class="sk-param">
              <div class="sk-param-lbl">${esc(p.label)}</div>
              <input class="sk-param-inp" placeholder="${esc(p.placeholder)}">
            </div>`).join('')}
        </div>
      </div>
      <button class="sk-run-btn" onclick="runSkill('${id}')">&#9654; Run</button>
      <div id="sk-stream-${id}" style="display:none">
        <div class="sk-section-lbl">Output</div>
        <div class="sk-stream-panel" id="sk-stream-panel-${id}"></div>
      </div>
      ${sk.runs.length ? `<div class="sk-runs-sep"></div><div class="sk-section-lbl">Recent runs</div>${runs}` : ''}
    </div>`;
}

function runSkill(id) {
  const sk = SKILLS[id];
  sk.runs.unshift({status:'run', ts:'Now', params:'...', error:null});
  renderSkillDetail(id);

  const streamWrap = document.getElementById('sk-stream-' + id);
  const streamPanel = document.getElementById('sk-stream-panel-' + id);
  if (streamWrap) streamWrap.style.display = 'block';

  const lines = [
    {text:'Initializing skill…', cls:'dim'},
    {text:'Fetching data…', cls:''},
    {text:'Processing rows…', cls:''},
    {text:'Saving to database…', cls:''},
    {text:'Done. 86 rows written.', cls:'ok'},
  ];

  let i = 0;
  const interval = setInterval(() => {
    if (!streamPanel || i >= lines.length) {
      clearInterval(interval);
      if (sk.runs[0]) { sk.runs[0].status = 'done'; sk.runs[0].ts = 'Just now'; }
      renderSkillDetail(id);
      return;
    }
    const d = document.createElement('div');
    d.className = 'sk-stream-line' + (lines[i].cls ? ' ' + lines[i].cls : '');
    d.textContent = '> ' + lines[i].text;
    if (streamPanel) { streamPanel.appendChild(d); streamPanel.scrollTop = streamPanel.scrollHeight; }
    i++;
  }, 350);
}

// ── Data view ──────────────────────────────────────────────────────────────
function selTable(el, id) {
  document.querySelectorAll('.db-tbl-item').forEach(x => x.classList.remove('on'));
  if (el) el.classList.add('on');
  curTableId = id;
  sortCol = null; sortDir = 1; filterText = '';
  const s = document.getElementById('db-search');
  if (s) s.value = '';
  closeDrawer();
  renderTable();
}

function renderTable() {
  const data = DB[curTableId];
  if (!data) return;
  const tname = document.getElementById('db-tname');
  if (tname) tname.textContent = curTableId;

  let rows = data.rows.filter(r =>
    !filterText || Object.values(r).some(v => v && String(v).toLowerCase().includes(filterText))
  );

  if (sortCol) {
    rows = [...rows].sort((a,b) => {
      const av = a[sortCol] ?? '', bv = b[sortCol] ?? '';
      return String(av).localeCompare(String(bv), undefined, {numeric:true}) * sortDir;
    });
  }

  const cnt = document.getElementById('db-count');
  if (cnt) cnt.textContent = `${rows.length} row${rows.length!==1?'s':''}`;

  const thead = document.getElementById('db-thead');
  if (thead) thead.innerHTML = '<tr>' + data.cols.map(c => {
    const ico = sortCol===c ? (sortDir>0?'↑':'↓') : '';
    return `<th onclick="sortBy('${c}')">${esc(c)} <span class="sort-ico">${ico}</span></th>`;
  }).join('') + '</tr>';

  const STATUS_COLOR = {done:'#16a34a',running:'#4f46e5',queued:'rgba(0,0,0,.45)',needs_review:'#b45309',failed:'#dc2626'};
  const mono = new Set(['id','task_id','ts','created_at','input','output','value','size_bytes','seq']);
  const tbody = document.getElementById('db-tbody');
  if (tbody) tbody.innerHTML = rows.map((r, ri) =>
    `<tr class="row-click" onclick="openDrawer(${ri}, ${JSON.stringify(JSON.stringify(r))})">` +
    data.cols.map(c => {
      const v = r[c];
      if (v===null||v===undefined) return `<td><span class="db-null">null</span></td>`;
      if (c==='status') return `<td class="status" style="color:${STATUS_COLOR[v]||'inherit'}">${esc(String(v))}</td>`;
      if (c==='size_kb' && v>0) return `<td class="mono">${Number(v).toFixed(1)} KB</td>`;
      if (c==='size_bytes' && v>0) return `<td class="mono">${(v/1024).toFixed(1)} KB</td>`;
      if (c==='sensitive') return `<td>${v?'🔒':''}</td>`;
      const s = String(v);
      const disp = s.length > 60 ? s.slice(0,58)+'…' : s;
      return `<td class="${mono.has(c)?'mono':''}">${esc(disp)}</td>`;
    }).join('') + '</tr>'
  ).join('');
}

function sortBy(col) {
  if (sortCol===col) sortDir *= -1; else { sortCol = col; sortDir = 1; }
  renderTable();
}

function filterTable() {
  filterText = document.getElementById('db-search').value.toLowerCase();
  renderTable();
}

function openDrawer(ri, rjson) {
  const r = JSON.parse(rjson);
  const title = document.getElementById('drawer-title');
  if (title) title.textContent = curTableId + ' · row';
  const body = document.getElementById('drawer-body');
  if (body) body.innerHTML = Object.entries(r).map(([k,v]) => {
    const isLong = v && String(v).length > 40;
    const disp = v===null||v===undefined
      ? '<span class="db-null">null</span>'
      : `<div class="drawer-val${isLong?' mono':''}">${esc(String(v))}</div>`;
    return `<div class="drawer-field"><div class="drawer-key">${esc(k)}</div>${disp}</div>`;
  }).join('<div class="drawer-sep"></div>');
  document.getElementById('db-drawer').classList.add('on');
}

function closeDrawer() {
  const d = document.getElementById('db-drawer');
  if (d) d.classList.remove('on');
}

function exportCSV() {
  const data = DB[curTableId];
  if (!data) return;
  const lines = [data.cols.join(','), ...data.rows.map(r => data.cols.map(c => JSON.stringify(r[c]??'')).join(','))];
  const blob = new Blob([lines.join('\n')], {type:'text/csv'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = curTableId + '.csv'; a.click();
}

function refreshTable() { renderTable(); }

function switchDbView(tab) {
  document.querySelectorAll('.db-view-tab').forEach(t => t.classList.remove('on'));
  const el = document.querySelector(`.db-view-tab[data-view="${tab}"]`);
  if (el) el.classList.add('on');
  // cards view stub
  if (tab === 'cards') showToast('Cards view coming soon');
}

// ── Toast ──────────────────────────────────────────────────────────────────
function showToast(msg, onView) {
  const toast = document.getElementById('toast');
  const msgEl = document.getElementById('toast-msg');
  if (!toast || !msgEl) return;
  msgEl.textContent = msg;
  const viewLink = document.getElementById('toast-view-link');
  if (viewLink) {
    if (onView) {
      viewLink.style.display = 'inline';
      viewLink.onclick = onView;
    } else {
      viewLink.style.display = 'none';
    }
  }
  toast.classList.add('on');
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => dismissToast(), 4000);
}

function dismissToast() {
  document.getElementById('toast').classList.remove('on');
}

// ── Slash menu (global dismiss) ────────────────────────────────────────────
document.addEventListener('click', e => {
  if (!e.target.closest('.slash-menu') && !e.target.closest('.wsp-ta') && !e.target.closest('.irow')) {
    document.querySelectorAll('.slash-menu').forEach(m => m.classList.remove('on'));
  }
  if (!e.target.closest('#agents-popover') && !e.target.closest('#tb-agents-btn')) {
    closeAgentsPopover();
  }
});

// ── Init ───────────────────────────────────────────────────────────────────
(function init() {
  // fleet grid
  renderFleet();

  // agents popover
  const pop = document.getElementById('agents-popover-list');
  if (pop) {
    let html = '';
    AGENTS.browser.profiles.forEach(p => p.agents.filter(a=>a.status==='running').forEach(a => {
      html += `<div class="ap-item"><span>▶</span><span class="ap-name">${esc(a.name)}</span><span class="ap-status">running</span></div>`;
    }));
    AGENTS.loop.filter(a=>a.status==='running').forEach(a => {
      html += `<div class="ap-item"><span>▶</span><span class="ap-name">${esc(a.name)}</span><span class="ap-status">running</span></div>`;
    });
    pop.innerHTML = html || '<div class="ap-item" style="color:rgba(0,0,0,.38)">No active agents</div>';
  }

  // data file tree
  renderDataTree();

  // initial renders
  renderSkillDetail('market');
  renderTable();
  updateAgentBadge();

  // toast after 3s — simulate agent completing
  setTimeout(() => {
    showToast('Nash-AI Reporter · ✓ Done → View in Agents', () => setMode('agents'));
  }, 3000);
})();
