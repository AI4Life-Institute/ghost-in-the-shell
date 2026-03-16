import { AGENTS } from './data/agents';
import { state } from './state';
import { esc } from './utils';

// Views
import { setMode, updateAgentBadge, updateAgentsWarnBadge } from './views/mode';
import {
  renderGrid, renderSessions, renderSessionTabs,
  focusPane, openSessionPicker, closeSessPickerModal,
  pickSession, newSessionFromPicker, assignSessionToPane,
  toggleDevMode, selectBuildTab, addWindow, addPane,
  ar, hk, send, checkBuildEmpty, addm, scrl, cpc,
  _sessLabel, _shortDir, _projectName, _groupDir, buildSessionTree, recentSessions,
  _panelHeadHTML, _updatePanelHeader, _ensurePaneTerm, _initPaneTerm, _paneStatus, _makePanelEl,
  renderSessPickerTree, _spRowHTML, newSession, activateSession,
} from './views/build';
import {
  renderFleet, renderProfileAgents, selProfile,
  openFleetDrawer, closeFleetDrawer, _flatAgent, _fleetCardHTML,
  renderTriggerGrid,
} from './views/agents';
import { renderRunnerGrid, renderRunnerCard, openRunnerDrawer, runnerRunNow, runnerTogglePause, runnerTogglePauseById } from './views/runner';
import { renderDataTree, toggleDataNode, _findDataNode, _renderTreeNodes } from './views/data-tree';
import { selDataTable, selTable, renderTable, sortBy, filterTable, openDrawer, closeDrawer, exportCSV, refreshTable, switchDbView } from './views/data';
import { selSkill, renderSkillDetail, toggleRunExpand, replayRun, runSkill, renderSkillPanel, openNewSkillModal, closeNewSkillModal, generateNewSkill, saveNewSkill, renderSkillsList, selSkillByName, renderSkillDetailReal } from './views/skills';
import { showToast, dismissToast } from './ui/toast';
import { _autoScreenshot, takeScreenshot } from './ui/screenshot';
import { installTauriShim, ghostSetup, sendPane, _addPaneMsg, _initTerminalWhenVisible, initPtyTerminal } from './ipc';

// ── Titlebar helpers ───────────────────────────────────────────────────────

function folderPickerClick(): void {
  showToast('📁 Folder picker — ~/myproject selected');
}

function toggleAgentsPopover(e: Event): void {
  e.stopPropagation();
  document.getElementById('agents-popover')!.classList.toggle('on');
}

function closeAgentsPopover(): void {
  document.getElementById('agents-popover')!.classList.remove('on');
}

// ── Init ───────────────────────────────────────────────────────────────────

(function init() {
  renderGrid();
  renderFleet();

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

  renderDataTree();
  renderSkillDetail('market');
  renderTable();
  updateAgentBadge();
  updateAgentsWarnBadge();

  function _trySetup(attempts: number): void {
    installTauriShim();
    if (window.ghost) {
      ghostSetup();
    } else if (attempts > 0) {
      setTimeout(() => _trySetup(attempts - 1), 100);
    } else {
      showToast('No bridge — running in browser mode');
      renderGrid();
    }
  }
  _trySetup(20);
})();

// ── Global event listeners ─────────────────────────────────────────────────

document.addEventListener('keydown', (e: KeyboardEvent) => {
  if (e.metaKey && e.shiftKey && e.key === 's') {
    e.preventDefault();
    takeScreenshot();
  }
});

document.addEventListener('click', (e: MouseEvent) => {
  const target = e.target as Element;
  if (!target.closest('.slash-menu') && !target.closest('.wsp-ta') && !target.closest('.irow')) {
    document.querySelectorAll('.slash-menu').forEach(m => m.classList.remove('on'));
  }
  if (!target.closest('#agents-popover') && !target.closest('#tb-agents-btn')) {
    closeAgentsPopover();
  }
});

// ── Expose globals for HTML onclick attributes ─────────────────────────────

(window as any).setMode = setMode;
(window as any).folderPickerClick = folderPickerClick;
(window as any).toggleAgentsPopover = toggleAgentsPopover;
(window as any).closeAgentsPopover = closeAgentsPopover;
(window as any).toggleDevMode = toggleDevMode;
(window as any).focusPane = focusPane;
(window as any).openSessionPicker = openSessionPicker;
(window as any).closeSessPickerModal = closeSessPickerModal;
(window as any).newSessionFromPicker = newSessionFromPicker;
(window as any).pickSession = pickSession;
(window as any).openFleetDrawer = openFleetDrawer;
(window as any).closeFleetDrawer = closeFleetDrawer;
(window as any).selProfile = selProfile;
(window as any).openRunnerDrawer = openRunnerDrawer;
(window as any).runnerRunNow = runnerRunNow;
(window as any).runnerTogglePause = runnerTogglePause;
(window as any).runnerTogglePauseById = runnerTogglePauseById;
(window as any).toggleDataNode = toggleDataNode;
(window as any).selDataTable = selDataTable;
(window as any).selTable = selTable;
(window as any).openDrawer = openDrawer;
(window as any).closeDrawer = closeDrawer;
(window as any).exportCSV = exportCSV;
(window as any).refreshTable = refreshTable;
(window as any).switchDbView = switchDbView;
(window as any).filterTable = filterTable;
(window as any).sortBy = sortBy;
(window as any).selSkill = selSkill;
(window as any).runSkill = runSkill;
(window as any).toggleRunExpand = toggleRunExpand;
(window as any).replayRun = replayRun;
(window as any).openNewSkillModal = openNewSkillModal;
(window as any).closeNewSkillModal = closeNewSkillModal;
(window as any).generateNewSkill = generateNewSkill;
(window as any).saveNewSkill = saveNewSkill;
(window as any).showToast = showToast;
(window as any).dismissToast = dismissToast;
(window as any).takeScreenshot = takeScreenshot;
(window as any).sendPane = sendPane;
(window as any).initPtyTerminal = initPtyTerminal;
(window as any).cpc = cpc;
(window as any).ar = ar;
(window as any).hk = hk;
(window as any).send = send;
(window as any).addm = addm;
(window as any).selSkillByName = selSkillByName;
(window as any).renderSkillDetailReal = renderSkillDetailReal;
