import type { AgentDashboard } from '../types';

// ── Demo dashboards for 5 real agent scenarios (task 12.4) ─────────────────
// These are preloaded into state.agentDashboards on startup so the Agents
// view shows real-looking content without a backend connection.

export const DEMO_DASHBOARDS: AgentDashboard[] = [

  // 1. BTC Monitor (Loop Agent — data monitoring)
  {
    agentId: 'btc-monitor',
    widgets: [
      {
        id: 'btc-monitor-w1', type: 'conversation', size: '2x1',
        agentId: 'btc-monitor', state: 'running', title: 'Conversation',
        config: { mode: 'tail' },
      },
      {
        id: 'btc-monitor-w2', type: 'chart', size: '2x1',
        agentId: 'btc-monitor', state: 'running', title: 'BTC Prices',
        config: { table: 'btc_prices', xField: 'ts', yField: 'price', chartType: 'line', view: 'chart', range: '24h' },
      },
    ],
  },

  // 2. Nash-AI Reporter (Browser Agent — PDF list)
  {
    agentId: 'nash-reporter',
    widgets: [
      {
        id: 'nash-reporter-w1', type: 'conversation', size: '2x1',
        agentId: 'nash-reporter', state: 'running', title: 'Conversation',
        config: { mode: 'chat' },
      },
      {
        id: 'nash-reporter-w2', type: 'files', size: '2x2',
        agentId: 'nash-reporter', state: 'done', title: 'Reports',
        config: {
          dir: '~/.gits/outputs/nash-reporter',
          viewMode: 'list',
          actions: [
            { label: 'Preview', event: 'file_preview' },
            { label: 'Send',    event: 'file_send'    },
          ],
          files: [
            { id: 'r1', name: 'gs_q2_2024.pdf',       path: '/tmp/gs_q2_2024.pdf',       mimeType: 'application/pdf', size: 2457600,  createdAt: '2026-03-15 14:43', isNew: true },
            { id: 'r2', name: 'morgan_q1_2024.pdf',   path: '/tmp/morgan_q1_2024.pdf',   mimeType: 'application/pdf', size: 1843200,  createdAt: '2026-03-14 09:21' },
            { id: 'r3', name: 'ubs_tech_report.pdf',  path: '/tmp/ubs_tech_report.pdf',  mimeType: 'application/pdf', size: 3145728,  createdAt: '2026-03-13 16:08' },
          ],
        },
      },
    ],
  },

  // 3. Discord Digest (Loop Agent — Claude compute with review)
  {
    agentId: 'discord-digest',
    widgets: [
      {
        id: 'discord-digest-w1', type: 'conversation', size: '2x1',
        agentId: 'discord-digest', state: 'idle', title: 'Conversation',
        config: { mode: 'tail' },
      },
      {
        id: 'discord-digest-w2', type: 'compute', size: '2x2',
        agentId: 'discord-digest', state: 'review', title: "Today's Digest",
        config: {
          content: `## Discord #dev Digest · Mar 15

**3 action items found**

### 🔥 Hot topics
- **Deploy pipeline** broken since 14:30 — @alice patching now
- New **Tauri 2.1** released — upgrade discussion started by @bob
- \`ghost-ui\` v43 merged with agent dashboard feature

### 📦 PRs merged
- Fix: session picker crash on empty workspace
- Feat: add agent badge counter to sidebar

### 💬 Notable discussions
- Performance of IPC bridge when streaming large outputs
- Request: keyboard shortcuts for mode switching`,
          streaming: false,
          reviewActions: [
            { label: '✓ Approve & Post', event: 'digest_approve' },
            { label: '✕ Discard',        event: 'digest_discard' },
          ],
        },
      },
    ],
  },

  // 4. HN Digest (Loop Agent — image files via browser)
  {
    agentId: 'hn-digest',
    widgets: [
      {
        id: 'hn-digest-w1', type: 'conversation', size: '2x1',
        agentId: 'hn-digest', state: 'idle', title: 'Conversation',
        config: { mode: 'chat' },
      },
      {
        id: 'hn-digest-w2', type: 'chart', size: '2x1',
        agentId: 'hn-digest', state: 'done', title: 'HN Links',
        config: { table: 'hn_links', view: 'table', range: '24h' },
      },
    ],
  },

  // 5. FanVue Cloner (Browser Agent — gallery + pipeline)
  {
    agentId: 'fanvue-cloner',
    widgets: [
      {
        id: 'fanvue-cloner-w1', type: 'conversation', size: '2x1',
        agentId: 'fanvue-cloner', state: 'running', title: 'Conversation',
        config: { mode: 'tail' },
      },
      {
        id: 'fanvue-cloner-w2', type: 'files', size: '2x2',
        agentId: 'fanvue-cloner', state: 'running', title: 'Captured Images',
        config: {
          dir: '~/.gits/outputs/fanvue-cloner',
          viewMode: 'gallery',
          selectable: true,
          selected: [],
          actions: [
            { label: 'Use',  event: 'image_select' },
            { label: 'Skip', event: 'image_skip'   },
          ],
          batchActions: [
            { label: 'Process Selected →', event: 'batch_process' },
          ],
          files: [
            { id: 'img1', name: 'profile_001.jpg', path: '/tmp/p1.jpg', mimeType: 'image/jpeg', size: 204800,  createdAt: '2026-03-15 23:01', isNew: true },
            { id: 'img2', name: 'profile_002.jpg', path: '/tmp/p2.jpg', mimeType: 'image/jpeg', size: 184320,  createdAt: '2026-03-15 23:01', isNew: true },
            { id: 'img3', name: 'profile_003.jpg', path: '/tmp/p3.jpg', mimeType: 'image/jpeg', size: 225280,  createdAt: '2026-03-15 23:00' },
            { id: 'img4', name: 'profile_004.jpg', path: '/tmp/p4.jpg', mimeType: 'image/jpeg', size: 196608,  createdAt: '2026-03-15 23:00' },
          ],
        },
      },
      {
        id: 'fanvue-cloner-w3', type: 'compute', size: '2x1',
        agentId: 'fanvue-cloner', state: 'idle', title: 'Analysis',
        config: {
          content: '## Waiting for image selection…\n\nSelect images above, then run analysis.',
          streaming: false,
        },
      },
    ],
    pipeline: [
      { id: 'stage-crawl',    label: 'Crawl',    widgetId: 'fanvue-cloner-w1', state: 'running' },
      { id: 'stage-select',   label: 'Select',   widgetId: 'fanvue-cloner-w2', state: 'idle'    },
      { id: 'stage-analyze',  label: 'Analyze',  widgetId: 'fanvue-cloner-w3', state: 'idle'    },
    ],
  },
];
