import { state } from '../state';
import { esc } from '../utils';
import { SKILLS } from '../data/skills';
import { showToast } from '../ui/toast';
import { linkToLibrary } from './library';

export function selSkill(el: HTMLElement | null, id: string): void {
  document.querySelectorAll('.ski').forEach(s => s.classList.remove('on'));
  if (el) el.classList.add('on');
  state.curSkill = id;
  renderSkillDetail(id);
}

export function renderSkillDetail(id: string): void {
  const sk = SKILLS[id];
  if (!sk) return;

  const runs = sk.runs.map((r, ri) => {
    const runKey = id + '_' + ri;
    const isExpanded = !!state.skillRunExpanded[runKey];
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

  const detailEl = document.getElementById('sk-detail');
  if (!detailEl) return;
  detailEl.innerHTML = `
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

export function toggleRunExpand(runKey: string, skillId: string): void {
  state.skillRunExpanded[runKey] = !state.skillRunExpanded[runKey];
  renderSkillDetail(skillId);
}

export function replayRun(e: Event, skillId: string, runIdx: number): void {
  e.stopPropagation();
  const sk = SKILLS[skillId];
  const run = sk.runs[runIdx];
  if (!run) return;
  const inputs = document.querySelectorAll('#sk-detail .sk-param-inp') as NodeListOf<HTMLInputElement>;
  const pairs = (run.params || '').split(' ');
  inputs.forEach((inp, i) => {
    const pair = pairs[i];
    if (pair) inp.value = pair.split('=').slice(1).join('=');
  });
  showToast('Parameters pre-filled from past run. Click Run to execute.');
}

export function runSkill(id: string): void {
  const sk = SKILLS[id];
  const startTime = Date.now();
  sk.runs.unshift({status:'run', ts:'Now', params:'...', error:null, elapsed:null});
  renderSkillDetail(id);

  const streamWrap = document.getElementById('sk-stream-' + id);
  const streamPanel = document.getElementById('sk-stream-panel-' + id);
  if (streamWrap) streamWrap.style.display = 'block';

  const willFail = id === 'csvproc';
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
        if (streamPanel) {
          const output = streamPanel.textContent || '';
          const actRow = document.createElement('div');
          actRow.className = 'sk-stream-actions';
          actRow.innerHTML = `<span class="sk-done-label">✓ Done · ${elapsed}</span>
            <button class="sk-copy-btn" onclick="navigator.clipboard.writeText(${JSON.stringify(output)}).then(()=>showToast('Output copied!'))">Copy output</button>
            <button class="sk-data-link" onclick="linkToLibrary('data')">View in Data →</button>`;
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

export function renderSkillPanel(skills: any[]): void {
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
      ? sk.steps.map((s: any, i: number) => `<div class="runner-step-item">${i+1}. ${esc(s.tool || s.name || s.cmd || String(s))}</div>`).join('')
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

export function openNewSkillModal(): void {
  document.getElementById('new-skill-modal')!.classList.add('on');
  const ta = document.getElementById('new-skill-desc') as HTMLTextAreaElement;
  if (ta) { ta.value = ''; setTimeout(() => ta.focus(), 80); }
  const preview = document.getElementById('new-skill-preview');
  if (preview) preview.style.display = 'none';
}

export function closeNewSkillModal(e?: MouseEvent): void {
  if (e && e.target !== document.getElementById('new-skill-modal')) return;
  document.getElementById('new-skill-modal')!.classList.remove('on');
}

export function generateNewSkill(): void {
  const desc = (document.getElementById('new-skill-desc') as HTMLTextAreaElement).value.trim();
  if (!desc) return;
  const btn = document.querySelector('.modal-gen-btn') as HTMLButtonElement;
  if (btn) btn.textContent = '⏳ Generating…';
  setTimeout(() => {
    if (btn) btn.textContent = '✨ Generate Skill';
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

export function saveNewSkill(): void {
  const desc = (document.getElementById('new-skill-desc') as HTMLTextAreaElement).value.trim();
  const words = desc.split(' ');
  const name = words.slice(0,3).map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
  const newId = 'custom_' + Date.now();
  SKILLS[newId] = {
    name,
    desc,
    params: [{key:'symbol', label:'Symbol', placeholder:'AAPL'}],
    runs: []
  };
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
  setTimeout(() => { linkToLibrary('skills'); selSkill(null, newId); }, 400);
}

// ── Real-data skill functions (from backend skillDefs) ────────────────────

export function renderSkillsList(): void {
  const scroll = document.getElementById('sk-scroll');
  if (!scroll || Object.keys(state.skillDefs).length === 0) return;
  const skills = Object.values(state.skillDefs);
  scroll.innerHTML = skills.map((sk, i) => {
    const ttype = ((sk.trigger as any)?.type || sk.trigger || '').toString().toLowerCase();
    const isOn = state.curSkill === sk.name;
    return `<div class="ski${isOn ? ' on' : ''}" onclick="selSkillByName(this,'${esc(sk.name)}')">
      <div class="ski-name">${esc(sk.name)}</div>
      <div class="ski-desc">${esc((sk as any).description || (sk as any).desc || sk.name)}</div>
      <span class="ski-tag">${esc(ttype)}</span>
    </div>`;
  }).join('');
  if (!state.skillDefs[state.curSkill] && skills[0]) state.curSkill = skills[0].name;
  if (state.skillDefs[state.curSkill]) renderSkillDetailReal(state.curSkill);
}

export function selSkillByName(el: HTMLElement | null, name: string): void {
  document.querySelectorAll('.ski').forEach(s => s.classList.remove('on'));
  if (el) el.classList.add('on');
  state.curSkill = name;
  renderSkillDetailReal(name);
}

export function renderSkillDetailReal(name: string): void {
  const sk = state.skillDefs[name];
  if (!sk) return;
  const run = state.runnerAgents[name];
  const ttype = ((sk.trigger as any)?.type || sk.trigger || '').toString().toLowerCase();
  const trigLabel = ttype === 'loop' ? 'Loop' : ttype === 'reactive' ? 'Reactive' : ttype || '—';

  const stepsHtml = Array.isArray(sk.steps) && sk.steps.length
    ? sk.steps.map((s: any, i: number) => `<div style="font-size:12px;padding:3px 0;border-bottom:1px solid rgba(0,0,0,.05)">
        <span style="color:rgba(0,0,0,.3);margin-right:6px">${i+1}.</span>${esc(s.tool || s.name || s.cmd || JSON.stringify(s))}
      </div>`).join('')
    : '<div style="font-size:12px;color:rgba(0,0,0,.3);font-style:italic">No steps defined</div>';

  let lastRunHtml = '<div style="font-size:12px;color:rgba(0,0,0,.3);font-style:italic;padding:6px 0">No runs yet</div>';
  if (run) {
    const ts = run.started_at ? new Date(run.started_at).toLocaleString() : '—';
    const dur = (run.started_at && run.finished_at)
      ? Math.round((new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()) / 1000) + 's' : '—';
    const sc: Record<string, string> = {success:'done', done:'done', failed:'fail', fail:'fail', running:'run'};
    const scCls = sc[run.status] || 'run';
    lastRunHtml = `<div class="sk-run-item">
      <div class="sk-run-status sk-run-${scCls}"></div>
      <div class="sk-run-info">
        <div style="font-size:11.5px;font-weight:600">${esc(run.status || '—')}</div>
        <div class="sk-run-ts">${esc(ts)} · ${esc(dur)}</div>
      </div>
    </div>`;
  }

  const metaChips = [
    sk.on_failure ? `On fail: ${esc(String(sk.on_failure))}` : null,
    sk.guard?.enabled ? '🛡 Guard on' : null,
    (sk as any).paused ? '⏸ Paused' : null,
  ].filter(Boolean).map(t => `<span class="runner-meta-chip">${t}</span>`).join('');

  const detailEl = document.getElementById('sk-detail');
  if (!detailEl) return;
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
        <button class="sk-run-btn" onclick="runnerRunNow('${esc(name)}')">▶ Run Now</button>
        <button class="sk-run-btn" style="background:rgba(0,0,0,.08);color:rgba(0,0,0,.6)"
          onclick="runnerTogglePauseById('${esc(name)}')">${run?._paused ? '▶ Resume' : '⏸ Pause'}</button>
      </div>
    </div>`;
}
