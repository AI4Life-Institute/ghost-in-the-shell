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
    },
    {
      id: 'weather-reporter',
      name: 'Weather Reporter',
      status: 'running',
      autoRepaired: true,
      sub: 'Every 4h · next 2h',
      type: 'Loop Agent',
      detail: {
        running: true,
        steps: 2, totalSteps: 4, elapsed: '1s',
        log: [
          {ico:'⚡', action:'Evaluate', desc:'GET /api/weather/today', out:'→ 18°C, partly cloudy', ts:'16:00:00', done:true},
          {ico:'💾', action:'Save', desc:'INSERT weather_log', out:'→ ok', ts:'16:00:01', done:true},
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
      sub: 'Trigger: Discord · #dev · ● Connected',
      type: 'Reactive Agent',
      detail: {
        running: false,
        steps: 0, totalSteps: 0, elapsed: '—',
        log: [],
        hitl: null,
      }
    },
    {
      id: 'notion-trigger',
      name: 'Notion Page Watcher',
      status: 'waiting',
      sub: 'Trigger: Notion webhook · ⚠ Needs approval',
      type: 'Reactive Agent',
      detail: {
        running: false,
        steps: 1, totalSteps: 3, elapsed: '4s',
        log: [
          {ico:'🔔', action:'Triggered', desc:'Page "Week 12" updated in Notion', out:'', ts:'14:45:10', done:true},
        ],
        hitl: {msg: 'Export "Week 12" page as PDF and save to Data?', pending:true},
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
    {id:'btc_prices',    name:'btc_prices',    rows:128, updated:'2m ago', icon:'📊', table:'btc_prices',   sourceAgent:'btc-monitor'},
    {id:'hn_links',      name:'hn_links',       rows:340, updated:'2h ago', icon:'🔗', table:'hn_links',     sourceAgent:'hn-digest-loop'},
    {id:'nash_reports',  name:'nash_reports',   rows:47,  updated:'10m ago',icon:'📄', table:'nash_reports', sourceAgent:'nash-reporter'},
  ],
  fromSkills: [
    {id:'market_scans',  name:'market_scans',   rows:86,  updated:'14m ago',icon:'📈', table:'market_scans', sourceSkill:'market'},
    {id:'screenshots',   name:'screenshots',    rows:12,  updated:'1h ago', icon:'🖼', table:'screenshots',  sourceSkill:'screenshot'},
  ],
  manual: [
    {id:'notes',         name:'notes',          rows:8,   updated:'3d ago', icon:'📝', table:'notes'},
  ]
};

// Map table IDs to source info for the data view header
const TABLE_SOURCE_MAP = {};
DB_COLLECTIONS.fromAgents.forEach(c => {
  TABLE_SOURCE_MAP[c.table] = {type:'agent', id:c.sourceAgent};
});
DB_COLLECTIONS.fromSkills.forEach(c => {
  TABLE_SOURCE_MAP[c.table] = {type:'skill', id:c.sourceSkill};
});

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
let activeSessId = null;      // channel_id of the open PTY session
let sessTerminal = null;      // single xterm.js Terminal instance
let allSessions = [];         // all sessions from backend
let tmuxSession = 'ghost';    // tmux session name from backend
let curMode = 'build';
let curAgentId = 'nash-reporter';
let curProfileId = 'personal-chrome';
let curSkill = 'market';
let curTableId = 'btc_prices';
let sortCol = null, sortDir = 1;
let filterText = '';
let slashMenuOpen = false;
let toastTimer = null;
let skillRunExpanded = {}; // tracks which run history entries are expanded

// Runner Agent state (from backend agents_list / skills_list events)
let runnerAgents = {}; // skill_name → most recent run object
let skillDefs = {};    // skill_name → skill definition object (trigger, steps, on_failure, guard)

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
  AGENTS.reactive.forEach(a => { if (a.status === 'listening' || a.status === 'running') n++; });
  return n;
}

function countWaitingAgents() {
  let n = 0;
  AGENTS.browser.profiles.forEach(p => p.agents.forEach(a => { if (a.status === 'waiting') n++; }));
  AGENTS.loop.forEach(a => { if (a.status === 'waiting') n++; });
  AGENTS.reactive.forEach(a => { if (a.status === 'waiting') n++; });
  return n;
}

function updateAgentsWarnBadge() {
  const warnEl = document.querySelector('.mode[data-mode="agents"] .mode-badge.warn');
  if (warnEl) warnEl.style.display = countWaitingAgents() > 0 ? '' : 'none';
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

// ── Build view: session sidebar + terminal ─────────────────────────────────
function renderSessions(sessions) {
  const list = document.getElementById('sess-list');
  if (!list) return;
  list.innerHTML = '';
  if (!sessions.length) {
    list.innerHTML = '<div style="padding:20px 12px;color:rgba(255,255,255,0.2);font-size:12px">No sessions found</div>';
    return;
  }
  sessions.forEach(sess => {
    const dir = sess.work_dir ? sess.work_dir.split('/').pop() : '~';
    const isActive = sess.channel_id === activeSessId;
    const cli = (sess.coding_cli || 'claude').toLowerCase();
    const clsExtra = cli === 'codex' ? ' codex' : cli === 'opencode' ? ' opencode' : cli === 'copilot' ? ' copilot' : '';
    const item = document.createElement('div');
    item.className = 'sess-item' + (isActive ? ' active' : '');
    item.dataset.channelId = sess.channel_id;
    item.innerHTML =
      `<div class="sess-row1">` +
        `<span class="sess-dot-sm${isActive ? ' active' : ''}"></span>` +
        `<span class="sess-nm">${esc(sess.window_name || cli)}</span>` +
        `<span class="sess-cli-b${clsExtra}">${esc(cli)}</span>` +
      `</div>` +
      `<div class="sess-wd">${esc(dir)}</div>`;
    item.onclick = () => activateSession(sess);
    list.appendChild(item);
  });
}

function activateSession(sess) {
  if (activeSessId === sess.channel_id && sessTerminal) return; // already open
  activeSessId = sess.channel_id;

  // Highlight sidebar
  document.querySelectorAll('.sess-item').forEach(el => {
    const on = el.dataset.channelId === sess.channel_id;
    el.classList.toggle('active', on);
    const dot = el.querySelector('.sess-dot-sm');
    if (dot) { dot.classList.toggle('active', on); dot.classList.remove('busy'); }
  });

  // Update topbar
  const dir = sess.work_dir ? sess.work_dir.split('/').pop() : '~';
  const nameEl = document.getElementById('term-sess-name');
  const cliEl  = document.getElementById('term-sess-cli');
  const dirEl  = document.getElementById('term-sess-dir');
  if (nameEl) { nameEl.textContent = sess.window_name || sess.coding_cli; nameEl.style.color = ''; }
  if (cliEl)  cliEl.textContent = sess.coding_cli || 'claude';
  if (dirEl)  dirEl.textContent = dir;

  // Show terminal, hide empty
  const termEl  = document.getElementById('main-term');
  const emptyEl = document.getElementById('term-empty');
  if (termEl)  termEl.style.display = '';
  if (emptyEl) emptyEl.style.display = 'none';

  // Open PTY terminal
  initPtyTerminal(sess.channel_id);
}

function newSession() {
  const name = prompt('Session name:', 'ghost');
  if (!name) return;
  // Default work_dir to the active session's dir, or the first session's dir
  const refSess = allSessions.find(s => s.channel_id === activeSessId) || allSessions[0];
  const work_dir = refSess?.work_dir || '~';
  if (window.ghost) {
    window.ghost.send('new_session', { name, work_dir, cli: 'claude' });
  }
}

function selectBuildTab() {} // compat stub
function toggleDevMode() {}  // compat stub
function addWindow() {}      // compat stub
function addPane() {}        // compat stub

// ── Chat helpers (kept for Agents view compatibility) ──────────────────────
function ar(ta) { ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight, 100) + 'px'; }
function hk(e) {}  // compat stub — input now handled by PTY directly
function send() {} // compat stub
function checkBuildEmpty() {}

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
  const repairBadge = a.autoRepaired
    ? `<div class="fleet-repaired-badge">🤖 Auto-repaired by Build Agent</div>`
    : '';
  return `<div class="fleet-card ${a.status}${curAgentId===a.id?' on':''}" onclick="openFleetDrawer(this,'${a.id}')">
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
  // empty state check
  const totalAgents = AGENTS.browser.profiles.reduce((n,p)=>n+p.agents.length,0)
    + AGENTS.loop.length + AGENTS.reactive.length;
  const emptyEl = document.getElementById('agents-empty');
  const scrollEl = document.getElementById('fleet-scroll');
  if (emptyEl) emptyEl.style.display = totalAgents === 0 ? 'flex' : 'none';
  if (scrollEl) scrollEl.style.display = totalAgents === 0 ? 'none' : '';

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

  // live browser screenshot view (Browser Agents only)
  const liveView = a.type === 'Browser Agent' ? _mockBrowserScreen(a) : '';

  // Chrome profile banner for Browser Agents (tasks 1.15 + 1.16)
  const chromeBanner = a.type === 'Browser Agent' ? `
    <div class="ag-chrome-banner">
      <div class="ag-chrome-banner-inner">
        <span>🌐</span>
        <strong>Real Chrome · ${esc(a.profile || 'Personal Chrome')}</strong>
      </div>
      <div style="font-size:11px;color:rgba(0,0,0,.45);margin-top:3px">Your sessions. Your cookies. No re-logging in.</div>
    </div>` : '';

  // two-column bottom section
  const colLeft = `
    <div class="drawer-col-left">
      ${chromeBanner}
      <div class="ag-run-bar"><div class="ag-run-status">${runProgress}</div></div>
      <div class="ag-actions">
        <button class="ag-btn">⏸ Pause</button>
        <button class="ag-btn">▶ Run Now</button>
        <button class="ag-btn" style="color:#dc2626;border-color:rgba(220,38,38,.25)" onclick="showToast('Agent deleted (simulated)')">🗑 Delete</button>
        <button class="ag-btn link" onclick="setMode('data')">View in Data →</button>
      </div>
    </div>`;
  const colRight = `
    <div class="drawer-col-right">
      <div class="ag-log-hd">— Execution Log</div>
      <div class="ag-log-scroll">${logRows || '<div style="color:rgba(0,0,0,.30);font-size:12px;padding:8px 0">No log entries yet.</div>'}</div>
    </div>`;

  if (liveView) {
    // browser agents: top = live view (full width), bottom = two cols
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
  // pick mock content based on agent
  const isRunning = a.detail.running;
  const currentUrl = isRunning ? 'nash-ai.cn/reports/list' : 'nash-ai.cn/reports';
  const liveLbl = isRunning
    ? `<span class="live-dot"></span> Live · 2s ago`
    : `<span style="color:rgba(0,0,0,.35)">Last frame · 18s ago</span>`;

  // fake page content
  const pageContent = isRunning ? `
    <div class="mock-site-header">
      <div class="mock-site-logo">Nash<span>AI</span></div>
      <div class="mock-site-nav">Reports &nbsp;·&nbsp; Portfolio &nbsp;·&nbsp; Settings</div>
    </div>
    <div class="mock-site-body">
      <div class="mock-site-title">Research Reports</div>
      <div class="mock-site-row sel">
        <div class="mock-site-row-ico">📄</div>
        <div class="mock-site-row-name">Goldman Sachs Q2 2024 Analysis</div>
        <div class="mock-site-row-meta">2.4 MB · PDF</div>
        <div class="mock-download-bar"><div class="mock-download-fill"></div></div>
      </div>
      <div class="mock-site-row dim"><div class="mock-site-row-ico">📄</div><div class="mock-site-row-name">Morgan Stanley Q2 2024</div><div class="mock-site-row-meta">1.8 MB</div></div>
      <div class="mock-site-row dim"><div class="mock-site-row-ico">📄</div><div class="mock-site-row-name">JP Morgan Macro Outlook</div><div class="mock-site-row-meta">3.1 MB</div></div>
    </div>` : `
    <div class="mock-site-header">
      <div class="mock-site-logo">Nash<span>AI</span></div>
    </div>
    <div class="mock-site-body" style="opacity:.7">
      <div class="mock-site-title">Research Reports · 47 items</div>
      <div class="mock-site-row dim"><div class="mock-site-row-ico">📄</div><div class="mock-site-row-name">Goldman Sachs Q2 2024 Analysis</div><div class="mock-site-row-meta">✓ Downloaded</div></div>
    </div>`;

  return `<div class="live-browser">
    <div class="live-browser-bar">
      <div class="live-browser-dots"><span></span><span></span><span></span></div>
      <div class="live-browser-url">🔒 ${currentUrl}</div>
      <div class="live-browser-badge">${liveLbl}</div>
    </div>
    <div class="live-browser-screen">${pageContent}</div>
  </div>`;
}

// ── Runner Agent cards (task 6.1–6.6) ──────────────────────────────────────

/**
 * Build HTML for a single Runner Agent card from a run object.
 * run: { skill_name, status, started_at, finished_at, run_id, ... }
 */
function renderRunnerCard(run) {
  const name = run.skill_name || 'Unknown Skill';

  // Status dot color: green=success, red=failed, yellow=running, orange=guarded
  const dotColorMap = {
    success: '#22c55e',
    done:    '#22c55e',
    failed:  '#ef4444',
    fail:    '#ef4444',
    running: '#eab308',
    guarded: '#f97316',
  };
  const dotColor = dotColorMap[run.status] || 'rgba(0,0,0,.25)';

  // Badge label
  const badgeMap = {
    success: ['fleet-badge-done',    '✓ Success'],
    done:    ['fleet-badge-done',    '✓ Done'],
    failed:  ['fleet-badge-waiting', '✗ Failed'],
    fail:    ['fleet-badge-waiting', '✗ Failed'],
    running: ['fleet-badge-running', '▶ Running'],
    guarded: ['fleet-badge-waiting', '⚠ Guarded'],
  };
  const [badgeCls, badgeTxt] = badgeMap[run.status] || ['fleet-badge-idle', run.status || '—'];

  // Last run time
  let lastRunTxt = '—';
  if (run.started_at) {
    try {
      const d = new Date(run.started_at);
      lastRunTxt = d.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
    } catch(e) {
      lastRunTxt = run.started_at;
    }
  }

  // Duration
  let durationTxt = '';
  if (run.started_at && run.finished_at) {
    try {
      const dur = Math.round((new Date(run.finished_at) - new Date(run.started_at)) / 1000);
      durationTxt = ` · ${dur}s`;
    } catch(e) {}
  }

  // Trigger badge from skillDefs
  const sk = skillDefs[name];
  let triggerBadge = '';
  if (sk && sk.trigger) {
    const ttype = (sk.trigger.type || sk.trigger).toLowerCase();
    const trigLabel = ttype === 'loop' ? 'Loop' : ttype === 'reactive' ? 'Reactive' : sk.trigger.type || sk.trigger;
    triggerBadge = `<span class="runner-trigger-badge runner-trigger-${ttype}">${trigLabel}</span>`;
  }

  const isPaused = run._paused === true;

  return `<div class="fleet-card runner-card ${run.status || 'idle'}" onclick="openRunnerDrawer(this,'${esc(name)}')">
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
      <button class="runner-btn" onclick="runnerRunNow('${esc(name)}')">▶ Run Now</button>
      <button class="runner-btn runner-btn-sec" onclick="runnerTogglePause('${esc(name)}',this)">
        ${isPaused ? '▶ Resume' : '⏸ Pause'}
      </button>
    </div>
  </div>`;
}

/** Render all runner agent cards into the runner-grid section */
function renderRunnerGrid() {
  const grid = document.getElementById('runner-grid');
  if (!grid) return;
  const runs = Object.values(runnerAgents);
  if (runs.length === 0) {
    grid.innerHTML = '<div style="font-size:12px;color:rgba(0,0,0,.35);padding:6px 0;font-style:italic">No runner agents yet.</div>';
    return;
  }
  grid.innerHTML = runs.map(renderRunnerCard).join('');
}

/** Open the fleet drawer for a runner agent — shows log panel */
function openRunnerDrawer(el, skillName) {
  document.querySelectorAll('.fleet-card').forEach(c => c.classList.remove('on'));
  if (el) el.classList.add('on');

  const run = runnerAgents[skillName];
  const drawer = document.getElementById('fleet-drawer');
  const titleEl = document.getElementById('fleet-drawer-title');
  const bodyEl = document.getElementById('fleet-drawer-body');
  if (!drawer || !bodyEl) return;

  drawer.classList.add('on');
  if (titleEl) titleEl.textContent = skillName + ' · Runner Agent';

  const sk = skillDefs[skillName];
  let metaRows = '';
  if (sk) {
    const ttype = sk.trigger ? (sk.trigger.type || sk.trigger) : '—';
    const onFail = sk.on_failure || '—';
    const guard = sk.guard ? (sk.guard.enabled ? '✓ Enabled' : '✗ Disabled') : '—';
    const steps = Array.isArray(sk.steps) ? sk.steps.length : '—';
    metaRows = `
      <div class="runner-meta-row">
        <span class="runner-meta-key">Trigger</span><span class="runner-meta-val">${esc(String(ttype))}</span>
        <span class="runner-meta-key">On failure</span><span class="runner-meta-val">${esc(String(onFail))}</span>
        <span class="runner-meta-key">Guard</span><span class="runner-meta-val">${esc(String(guard))}</span>
        <span class="runner-meta-key">Steps</span><span class="runner-meta-val">${esc(String(steps))}</span>
      </div>`;
  }

  const runId = run ? run.run_id : null;
  const logPanelId = 'runner-log-panel-' + skillName.replace(/[^a-z0-9]/gi, '_');

  bodyEl.innerHTML = `
    <div class="drawer-col-left">
      <div class="ag-run-bar">
        <div class="ag-run-status">${run ? esc(run.status || '—') : 'No runs yet'}</div>
      </div>
      ${metaRows}
      <div class="ag-actions">
        <button class="ag-btn" onclick="runnerRunNow('${esc(skillName)}')">▶ Run Now</button>
        <button class="ag-btn" onclick="runnerTogglePauseById('${esc(skillName)}')">⏸ Pause / ▶ Resume</button>
      </div>
    </div>
    <div class="drawer-col-right">
      <div class="ag-log-hd">— Live Log</div>
      <div class="ag-log-scroll runner-log-scroll" id="${logPanelId}">
        <div style="color:rgba(0,0,0,.30);font-size:12px;padding:8px 0">Fetching log…</div>
      </div>
    </div>`;

  // Request log from backend (task 6.4)
  if (window.ghost && skillName && runId) {
    window.ghost.send('agent_log', { skill_name: skillName, run_id: runId });
  } else {
    // No backend — show placeholder
    const lp = document.getElementById(logPanelId);
    if (lp) lp.innerHTML = '<div style="color:rgba(0,0,0,.30);font-size:12px;padding:8px 0">No log available (not connected to backend).</div>';
  }
}

/** Send skill_run IPC command */
function runnerRunNow(skillName) {
  if (window.ghost) {
    window.ghost.send('skill_run', { skill_name: skillName });
    showToast(`▶ Running "${skillName}"…`);
  } else {
    showToast(`▶ Run Now: ${skillName} (no backend connected)`);
  }
}

/** Toggle pause/resume for a runner agent */
function runnerTogglePause(skillName, btn) {
  const run = runnerAgents[skillName];
  if (!run) return;
  const wasPaused = run._paused === true;
  run._paused = !wasPaused;
  if (btn) btn.textContent = run._paused ? '▶ Resume' : '⏸ Pause';
  if (window.ghost) {
    window.ghost.send(run._paused ? 'skill_pause' : 'skill_resume', { skill_name: skillName });
    showToast(run._paused ? `⏸ Paused "${skillName}"` : `▶ Resumed "${skillName}"`);
  } else {
    showToast((run._paused ? '⏸ Paused ' : '▶ Resumed ') + skillName + ' (no backend)');
  }
}

function runnerTogglePauseById(skillName) {
  const run = runnerAgents[skillName];
  if (!run) return;
  runnerTogglePause(skillName, null);
  renderRunnerGrid();
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

  const runs = sk.runs.map((r, ri) => {
    const runKey = id + '_' + ri;
    const isExpanded = !!skillRunExpanded[runKey];
    const statusLabel = r.status === 'done' ? '✓ Done' : r.status === 'fail' ? '✗ Failed' : '⏳ Running';
    const debugBlock = r.error ? `
      <div class="sk-debug">
        <div class="sk-debug-hd">🤖 AI Debug</div>
        <div class="sk-debug-msg">${esc(r.error.msg)}</div>
        <div class="sk-debug-ai">${esc(r.error.ai)}</div>
        <button class="sk-debug-fix" onclick="setMode('build')">Apply fix in Build →</button>
      </div>` : '';
    return `
    <div class="sk-run-item" onclick="toggleRunExpand('${runKey}','${id}')">
      <div class="sk-run-status sk-run-${r.status}"></div>
      <div class="sk-run-info">
        <div style="display:flex;align-items:center;gap:6px">
          <span style="font-size:11.5px;font-weight:600;color:${r.status==='done'?'#16a34a':r.status==='fail'?'#dc2626':'#4f46e5'}">${statusLabel}</span>
          <span class="sk-run-ts">${r.ts}</span>
        </div>
        <div class="sk-run-params-txt">${esc(r.params)}</div>
        <div class="sk-run-detail${isExpanded?' open':''}">
          ${r.elapsed ? `<div style="font-size:11px;color:rgba(0,0,0,.38);margin-bottom:4px">Elapsed: ${r.elapsed || '—'}</div>` : ''}
          ${debugBlock}
          <button class="sk-run-replay" onclick="replayRun(event,'${id}',${ri})">↺ Replay</button>
        </div>
      </div>
      <div class="sk-run-expand">${isExpanded ? '▲' : '▼'}</div>
    </div>`;
  }).join('');

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

function toggleRunExpand(runKey, skillId) {
  skillRunExpanded[runKey] = !skillRunExpanded[runKey];
  renderSkillDetail(skillId);
}

function replayRun(e, skillId, runIdx) {
  e.stopPropagation();
  const sk = SKILLS[skillId];
  const run = sk.runs[runIdx];
  if (!run) return;
  // pre-fill param inputs from run params string
  const inputs = document.querySelectorAll('#sk-detail .sk-param-inp');
  const pairs = (run.params || '').split(' ');
  inputs.forEach((inp, i) => {
    const pair = pairs[i];
    if (pair) inp.value = pair.split('=').slice(1).join('=');
  });
  showToast('Parameters pre-filled from past run. Click Run to execute.');
}

function runSkill(id) {
  const sk = SKILLS[id];
  const startTime = Date.now();
  sk.runs.unshift({status:'run', ts:'Now', params:'...', error:null, elapsed:null});
  renderSkillDetail(id);

  const streamWrap = document.getElementById('sk-stream-' + id);
  const streamPanel = document.getElementById('sk-stream-panel-' + id);
  if (streamWrap) streamWrap.style.display = 'block';

  // simulate either success or failure based on skill
  const willFail = id === 'csvproc'; // csvproc simulates a failure for demo
  const lines = willFail ? [
    {text:'Initializing skill…', cls:'dim'},
    {text:'Loading ~/Downloads/data.csv…', cls:''},
    {text:'ERROR: FileNotFoundError: ~/Downloads/data.csv not found', cls:'err'},
  ] : [
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
      const elapsed = ((Date.now() - startTime) / 1000).toFixed(1) + 's';
      if (willFail) {
        if (sk.runs[0]) {
          sk.runs[0].status = 'fail';
          sk.runs[0].ts = 'Just now';
          sk.runs[0].elapsed = elapsed;
          sk.runs[0].error = {
            msg: 'FileNotFoundError: ~/Downloads/data.csv not found\n  at csv_processor.py:14',
            ai: 'The file path does not exist. Make sure the CSV file is in your Downloads folder, or update the input path to point to the correct file location.'
          };
        }
        // show auto-expanded debug inline in stream
        if (streamPanel) {
          const sep = document.createElement('div');
          sep.className = 'sk-stream-line err';
          sep.textContent = '─────────────────────────────';
          streamPanel.appendChild(sep);
          const dbg = document.createElement('div');
          dbg.style.cssText = 'padding:8px 10px;margin-top:6px;background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.20);border-radius:7px';
          dbg.innerHTML = `<div style="font-size:11px;font-weight:600;color:#dc2626;margin-bottom:4px">🤖 AI Debug</div>
<div style="font-size:11.5px;color:rgba(0,0,0,.65);line-height:1.5">The file path does not exist. Make sure the CSV file is in your Downloads folder, or update the <code style="background:rgba(0,0,0,.07);padding:0 3px;border-radius:3px">input</code> path parameter to point to the correct file location.</div>
<button class="sk-debug-fix" style="margin-top:8px" onclick="setMode('build')">Apply fix in Build →</button>`;
          streamPanel.appendChild(dbg);
          streamPanel.scrollTop = streamPanel.scrollHeight;
        }
      } else {
        if (sk.runs[0]) {
          sk.runs[0].status = 'done';
          sk.runs[0].ts = 'Just now';
          sk.runs[0].elapsed = elapsed;
        }
        // show success action row below stream
        if (streamPanel) {
          const output = streamPanel.textContent;
          const actRow = document.createElement('div');
          actRow.className = 'sk-stream-actions';
          actRow.innerHTML = `<span class="sk-done-label">✓ Done · ${elapsed}</span>
            <button class="sk-copy-btn" onclick="navigator.clipboard.writeText(${JSON.stringify(output)}).then(()=>showToast('Output copied!'))">Copy output</button>
            <button class="sk-data-link" onclick="setMode('data')">View in Data →</button>`;
          streamPanel.after(actRow);
        }
      }
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

// ── Skills Panel — runner skill cards (tasks 7.1–7.3) ──────────────────────

/**
 * Render Skill cards from the backend skills_list into #runner-skills-list.
 * Each card shows: trigger badge, on_failure policy, guard status, step count.
 */
function renderSkillPanel(skills) {
  const container = document.getElementById('runner-skills-list');
  if (!container) return;
  if (!skills || skills.length === 0) {
    container.innerHTML = '<div style="font-size:12px;color:rgba(0,0,0,.35);padding:8px 0;font-style:italic">No skills loaded from ~/.gits/skills/.</div>';
    return;
  }
  container.innerHTML = skills.map(sk => {
    const name = sk.name || '—';
    const ttype = sk.trigger ? ((sk.trigger.type || sk.trigger) + '').toLowerCase() : 'unknown';
    const trigLabel = ttype === 'loop' ? 'Loop' : ttype === 'reactive' ? 'Reactive' : ttype;
    const onFail = sk.on_failure || '—';
    const guardEnabled = sk.guard ? !!sk.guard.enabled : false;
    const guardTxt = guardEnabled ? '✓ Guard on' : '✗ Guard off';
    const stepCount = Array.isArray(sk.steps) ? sk.steps.length : 0;
    const stepsHtml = Array.isArray(sk.steps) && sk.steps.length > 0
      ? sk.steps.map((s, i) => `<div class="runner-step-item">${i+1}. ${esc(s.tool || s.name || s.cmd || String(s))}</div>`).join('')
      : '<div class="runner-step-item" style="color:rgba(0,0,0,.32)">No steps defined</div>';

    return `<div class="runner-skill-card">
      <div class="runner-skill-card-head">
        <div class="runner-skill-name">${esc(name)}</div>
        <span class="runner-trigger-badge runner-trigger-${ttype}">${trigLabel}</span>
      </div>
      <div class="runner-skill-meta">
        <span class="runner-meta-chip">On fail: ${esc(String(onFail))}</span>
        <span class="runner-meta-chip${guardEnabled ? ' runner-meta-chip-on' : ''}">${guardTxt}</span>
        <span class="runner-meta-chip">${stepCount} step${stepCount !== 1 ? 's' : ''}</span>
      </div>
      <div class="runner-steps-list">${stepsHtml}</div>
    </div>`;
  }).join('');
}

// ── New Skill Modal (task 1.27) ─────────────────────────────────────────────
function openNewSkillModal() {
  document.getElementById('new-skill-modal').classList.add('on');
  const ta = document.getElementById('new-skill-desc');
  if (ta) { ta.value = ''; setTimeout(() => ta.focus(), 80); }
  const preview = document.getElementById('new-skill-preview');
  if (preview) preview.style.display = 'none';
}

function closeNewSkillModal(e) {
  if (e && e.target !== document.getElementById('new-skill-modal')) return;
  document.getElementById('new-skill-modal').classList.remove('on');
}

function generateNewSkill() {
  const desc = document.getElementById('new-skill-desc').value.trim();
  if (!desc) return;
  const btn = document.querySelector('.modal-gen-btn');
  if (btn) btn.textContent = '⏳ Generating…';
  setTimeout(() => {
    if (btn) btn.textContent = '✨ Generate Skill';
    // generate a mock skill preview
    const words = desc.split(' ');
    const name = words.slice(0,3).map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
    const preview = document.getElementById('new-skill-preview');
    const content = document.getElementById('new-skill-preview-content');
    if (content) content.innerHTML = `<strong>Name:</strong> ${esc(name)}<br>
<strong>Description:</strong> ${esc(desc)}<br><br>
<strong>Parameters:</strong><br>
&nbsp; • <code>symbol</code> — ticker symbol (e.g. AAPL)<br>
&nbsp; • <code>interval_min</code> — fetch interval in minutes<br><br>
<strong>Actions:</strong><br>
&nbsp; 1. Fetch data from API<br>
&nbsp; 2. Transform and clean rows<br>
&nbsp; 3. Save to Data`;
    if (preview) preview.style.display = 'block';
  }, 900);
}

function saveNewSkill() {
  const desc = document.getElementById('new-skill-desc').value.trim();
  const words = desc.split(' ');
  const name = words.slice(0,3).map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
  const newId = 'custom_' + Date.now();
  SKILLS[newId] = {
    name,
    desc,
    params: [{key:'symbol', label:'Symbol', placeholder:'AAPL'}],
    runs: []
  };
  // add to skill list
  const scroll = document.getElementById('sk-scroll');
  if (scroll) {
    const el = document.createElement('div');
    el.className = 'ski';
    el.innerHTML = `<div class="ski-name">${esc(name)}</div><div class="ski-desc">${esc(desc)}</div><span class="ski-tag">custom</span>`;
    el.onclick = () => { selSkill(el, newId); };
    scroll.appendChild(el);
  }
  closeNewSkillModal();
  showToast(`✓ Skill "${name}" saved`);
  setTimeout(() => { setMode('skill'); selSkill(null, newId); }, 400);
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

  // Source badge (task 1.29)
  const sourceMeta = TABLE_SOURCE_MAP[curTableId];
  let sourceBadgeHtml = '';
  if (sourceMeta) {
    if (sourceMeta.type === 'agent') {
      const a = _flatAgent(sourceMeta.id);
      const label = a ? esc(a.name) : esc(sourceMeta.id);
      sourceBadgeHtml = `<span class="db-source-badge">Source: Agent — <a onclick="setMode('agents')">${label}</a></span>`;
    } else if (sourceMeta.type === 'skill') {
      const sk = SKILLS[sourceMeta.id];
      const label = sk ? esc(sk.name) : esc(sourceMeta.id);
      sourceBadgeHtml = `<span class="db-source-badge">Source: Skill — <a onclick="setMode('skill')">${label}</a></span>`;
    }
  }
  if (tname) tname.innerHTML = curTableId + sourceBadgeHtml;

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

  // empty state (task 1.32)
  if (rows.length === 0 && !filterText) {
    const thead = document.getElementById('db-thead');
    if (thead) thead.innerHTML = '';
    const tbody = document.getElementById('db-tbody');
    if (tbody) tbody.innerHTML = `<tr class="db-empty-row"><td colspan="99">No data yet. Run an Agent or Skill to start collecting.</td></tr>`;
    return;
  }

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
// Screenshot function (button + Cmd+Shift+S)
// Uses html2canvas to capture DOM → base64 PNG → saved by Rust (no screen recording TCC needed)
async function takeScreenshot() {
  if (!window.__TAURI__) { showToast('Screenshot only available in the desktop app'); return; }
  const invoke = window.__TAURI__.core?.invoke ?? window.__TAURI__.invoke;
  showToast('📸 Capturing…');

  // Load html2canvas from CDN if not already loaded
  if (!window.html2canvas) {
    await new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = 'https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js';
      s.onload = resolve; s.onerror = reject;
      document.head.appendChild(s);
    });
  }

  try {
    const canvas = await window.html2canvas(document.body, {
      backgroundColor: null,
      scale: window.devicePixelRatio || 1,
      logging: false,
      useCORS: true,
    });
    const dataUrl = canvas.toDataURL('image/png');
    const path = await invoke('take_screenshot', { data: dataUrl });
    showToast(`📸 Saved: ${path}`);
    console.log('Screenshot saved to', path);
  } catch (err) {
    showToast(`Screenshot failed: ${err}`);
    console.error('Screenshot error:', err);
  }
}

// Cmd+Shift+S → screenshot
document.addEventListener('keydown', e => {
  if (e.metaKey && e.shiftKey && e.key === 's') {
    e.preventDefault();
    takeScreenshot();
  }
});

document.addEventListener('click', e => {
  if (!e.target.closest('.slash-menu') && !e.target.closest('.wsp-ta') && !e.target.closest('.irow')) {
    document.querySelectorAll('.slash-menu').forEach(m => m.classList.remove('on'));
  }
  if (!e.target.closest('#agents-popover') && !e.target.closest('#tb-agents-btn')) {
    closeAgentsPopover();
  }
});

// ── Tauri v2 IPC shim ─────────────────────────────────────────────────────
// Exposes the same window.ghost API as Electron's preload.js, so ghostSetup()
// works unchanged in both runtimes. Runs only when window.__TAURI__ is present.
function installTauriShim() {
  if (window.ghost || !window.__TAURI__) return;
  // Tauri v2: invoke lives at window.__TAURI__.core.invoke
  const invoke = window.__TAURI__.core?.invoke ?? window.__TAURI__.invoke;
  const tauriEvent = window.__TAURI__.event;

  // Internal event bus so multiple .on() calls for same event all fire
  const _listeners = [];  // { event, cb }

  function _dispatch(event, data) {
    _listeners.forEach(l => { if (l.event === '*' || l.event === event) l.cb(data); });
  }

  // Route Python IPC events (emitted from Python → Rust → Tauri 'python-event')
  tauriEvent.listen('python-event', (e) => {
    const data = e.payload;
    if (data?.event) _dispatch(data.event, data);
  });

  // Route PTY output events (emitted directly from Rust as 'pty-output')
  tauriEvent.listen('pty-output', (e) => {
    _dispatch('pty-output', e.payload);
  });

  window.ghost = {
    send(cmd, payload = {}) {
      return invoke('python_cmd', { cmd, payload });
    },
    on(event, cb) {
      const entry = { event, cb };
      _listeners.push(entry);
      return entry;
    },
    off(handle) {
      const idx = _listeners.indexOf(handle);
      if (idx !== -1) _listeners.splice(idx, 1);
    },
    onAny(cb) { return window.ghost.on('*', cb); },
  };
}

// ── Ghost Bridge (Electron IPC) ────────────────────────────────────────────

function sendPane(idx) {} // compat stub — input now handled by PTY directly

function _addPaneMsg(idx, role, text) {
  const msgs = idx === 0
    ? document.getElementById('build-msgs')
    : document.getElementById('wspc-' + idx);
  if (!msgs) return;
  const now = new Date().toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
  const d = document.createElement('div');
  d.className = 'msg ' + role;
  d.innerHTML = `<div class="mav">${role==='ai'?'🤖':'W'}</div><div><div class="bbl">${fmt(text)}</div><div class="mt">${now}</div></div>`;
  if (idx === 0) {
    msgs.insertBefore(d, document.getElementById('typing'));
  } else {
    msgs.appendChild(d);
  }
  setTimeout(() => msgs.scrollTop = msgs.scrollHeight, 10);
}

function _initTerminalWhenVisible(idx, channelId) {}  // compat stub

function initPtyTerminal(channelId) {
  if (sessTerminal) {
    sessTerminal.dispose();
    sessTerminal = null;
  }

  const termEl = document.getElementById('main-term');
  if (!termEl) return;
  termEl.innerHTML = '';

  const term = new Terminal({
    fontFamily: '"SF Mono", "JetBrains Mono", "Menlo", monospace',
    fontSize: 13,
    lineHeight: 1.45,
    theme: {
      background: 'transparent',
      foreground: 'rgba(255,255,255,0.85)',
      cursor: 'rgba(255,255,255,0.75)',
      selectionBackground: 'rgba(99,102,241,0.35)',
      black: '#1a1a1a', red: '#ff6b6b', green: '#51cf66',
      yellow: '#ffd43b', blue: '#74c0fc', magenta: '#cc5de8',
      cyan: '#3bc9db', white: '#e9ecef',
      brightBlack: '#868e96', brightRed: '#ff8787',
      brightGreen: '#8ce99a', brightYellow: '#ffe066',
      brightBlue: '#91d0ff', brightMagenta: '#da77f2',
      brightCyan: '#66d9e8', brightWhite: '#f8f9fa',
    },
    allowTransparency: true,
    cursorBlink: true,
    scrollback: 5000,
  });

  const fitAddon = new FitAddon.FitAddon();
  term.loadAddon(fitAddon);
  term.open(termEl);
  fitAddon.fit();
  sessTerminal = term;

  // Keyboard input → PTY
  term.onData((data) => {
    if (window.__TAURI__) {
      const encoded = btoa(unescape(encodeURIComponent(data)));
      (window.__TAURI__.core?.invoke ?? window.__TAURI__.invoke)('pty_input', { channel_id: channelId, data: encoded });
    }
  });

  // Open PTY on backend
  const binding = allSessions.find(s => s.channel_id === channelId);
  if (binding && window.__TAURI__) {
    (window.__TAURI__.core?.invoke ?? window.__TAURI__.invoke)('open_pty', {
      channel_id: channelId,
      tmux_session: tmuxSession,
      window_id: binding.window_id,
      rows: term.rows,
      cols: term.cols,
    }).catch(err => console.error('open_pty failed:', err));
  }

  // Auto-resize
  const ro = new ResizeObserver(() => {
    fitAddon.fit();
    if (window.__TAURI__ && term.rows && term.cols) {
      (window.__TAURI__.core?.invoke ?? window.__TAURI__.invoke)('resize_pty', {
        channel_id: channelId, rows: term.rows, cols: term.cols,
      }).catch(() => {});
    }
  });
  ro.observe(termEl);
  term.focus();
}

function ghostSetup() {
  if (typeof window === 'undefined' || !window.ghost) return;

  // Register all listeners FIRST, then send requests
  window.ghost.on('ready', () => {
    window.ghost.send('sessions');
    window.ghost.send('agents', {});
    window.ghost.send('skills', {});
  });

  window.ghost.on('sessions', (data) => {
    allSessions = data.sessions || [];
    if (data.tmux_session) tmuxSession = data.tmux_session;
    renderSessions(allSessions);
    // Auto-select first session on first load
    if (!activeSessId && allSessions.length > 0) {
      activateSession(allSessions[0]);
    }
  });

  window.ghost.on('pane_update', (data) => {
    // Update status dot in sidebar
    const item = document.querySelector(`.sess-item[data-channel-id="${data.channel_id}"]`);
    if (!item) return;
    const dot = item.querySelector('.sess-dot-sm');
    if (!dot) return;
    if (data.channel_id === activeSessId) {
      dot.className = 'sess-dot-sm active';
    } else if (data.status === 'busy') {
      dot.className = 'sess-dot-sm busy';
    } else {
      dot.className = 'sess-dot-sm';
    }
  });

  // PTY output → xterm.js
  window.ghost.on('pty-output', (data) => {
    if (data.channel_id !== activeSessId || !sessTerminal) return;
    if (data.closed) { sessTerminal.write('\r\n[terminal closed]\r\n'); return; }
    if (data.data) {
      try {
        const bytes = Uint8Array.from(atob(data.data), c => c.charCodeAt(0));
        sessTerminal.write(bytes);
      } catch (e) {
        sessTerminal.write(data.data);
      }
    }
  });

  // agents_list
  window.ghost.on('agents_list', (data) => {
    const runs = data.runs || [];
    runnerAgents = {};
    runs.forEach(run => {
      const key = run.skill_name;
      if (!key) return;
      const existing = runnerAgents[key];
      if (!existing) { runnerAgents[key] = run; return; }
      const existingTs = existing.started_at ? new Date(existing.started_at).getTime() : 0;
      const newTs = run.started_at ? new Date(run.started_at).getTime() : 0;
      if (newTs > existingTs) runnerAgents[key] = run;
    });
    renderRunnerGrid();
    updateAgentBadge();
  });

  // skills_list
  window.ghost.on('skills_list', (data) => {
    const skills = data.skills || [];
    skillDefs = {};
    skills.forEach(sk => { if (sk.name) skillDefs[sk.name] = sk; });
    renderRunnerGrid();
    renderSkillPanel(skills);
  });

  // agent_log
  window.ghost.on('agent_log', (data) => {
    const skillName = data.skill_name;
    if (!skillName) return;
    const logPanelId = 'runner-log-panel-' + skillName.replace(/[^a-z0-9]/gi, '_');
    const lp = document.getElementById(logPanelId);
    if (!lp) return;
    const line = data.line || data.text || '';
    if (!line) return;
    if (lp.querySelector('div[style]')) lp.innerHTML = '';
    const d = document.createElement('div');
    d.className = 'ag-log-row';
    d.style.cssText = 'font-size:11.5px;font-family:monospace;color:rgba(0,0,0,.7);padding:2px 0;border-bottom:1px solid rgba(0,0,0,.04)';
    d.textContent = line;
    lp.appendChild(d);
    lp.scrollTop = lp.scrollHeight;
  });

  // All listeners registered — now request data
  // Catch errors so we can see what's failing
  function _requestSessions() {
    window.ghost.send('sessions')
      .then(() => {})
      .catch(err => {
        const list = document.getElementById('sess-list');
        if (list) list.innerHTML = `<div style="padding:8px 12px;color:#f87171;font-size:11px">IPC error: ${err}</div>`;
      });
  }
  _requestSessions();
  window.ghost.send('agents', {}).catch(() => {});
  window.ghost.send('skills', {}).catch(() => {});

  // Retry sessions every 3s until they arrive
  const _sessRetry = setInterval(() => {
    if (allSessions.length > 0) { clearInterval(_sessRetry); return; }
    _requestSessions();
  }, 3000);
}

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
  updateAgentsWarnBadge();

  // Wire IPC bridge — retry until window.__TAURI__ is ready
  function _trySetup(attempts) {
    installTauriShim();
    if (window.ghost) {
      ghostSetup();
      const dbg = document.getElementById('sess-list');
      if (dbg && dbg.textContent.includes('Loading')) {
        dbg.innerHTML = '<div style="padding:8px 12px;color:rgba(255,255,255,0.4);font-size:11px">Connected, waiting for sessions…</div>';
      }
    } else if (attempts > 0) {
      const dbg = document.getElementById('sess-list');
      if (dbg) dbg.innerHTML = `<div style="padding:8px 12px;color:rgba(255,255,255,0.3);font-size:11px">Connecting… (${21-attempts}) __TAURI__=${!!window.__TAURI__}</div>`;
      setTimeout(() => _trySetup(attempts - 1), 100);
    } else {
      const dbg = document.getElementById('sess-list');
      if (dbg) dbg.innerHTML = '<div style="padding:8px 12px;color:#f87171;font-size:11px">No Tauri bridge — running in browser?</div>';
    }
  }
  _trySetup(20);
})();
