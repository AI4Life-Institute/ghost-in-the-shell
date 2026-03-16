import type { AgentsData } from '../types';

export const AGENTS: AgentsData = {
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
        agents: [
          {
            id: 'fanvue-cloner',
            name: 'FanVue Cloner',
            status: 'running',
            sub: 'step 3 of ~8 · 12s',
            type: 'Browser Agent',
            profile: 'Work Chrome',
            detail: {
              running: true,
              steps: 3, totalSteps: 8, elapsed: '12s',
              log: [
                {ico:'🧭', action:'Navigate', desc:'fanvue.com/login', out:'✓ Logged in', ts:'23:01:00', done:true},
                {ico:'📸', action:'Snapshot', desc:'found 24 creator profiles', out:'', ts:'23:01:02', done:true},
                {ico:'⬇', action:'Scrape', desc:'extracting profile metadata…', out:'', ts:'23:01:10', done:false, pending:true},
              ],
              hitl: null,
            }
          },
        ]
      },
      {
        id: 'research-chrome',
        label: 'Research',
        agents: [
          {
            id: 'market-crawler',
            name: 'Market Crawler',
            status: 'done',
            sub: 'ran 30m ago · 6 sources',
            type: 'Browser Agent',
            profile: 'Research',
            detail: {
              running: false,
              steps: 9, totalSteps: 9, elapsed: '34s',
              log: [
                {ico:'🧭', action:'Navigate', desc:'bloomberg.com/markets', out:'', ts:'22:30:00', done:true},
                {ico:'⚡', action:'Extract', desc:'pulled 14 market headlines', out:'→ saved', ts:'22:30:08', done:true},
                {ico:'💾', action:'Save to DB', desc:'INSERT 14 rows → market_news', out:'→ ok', ts:'22:30:10', done:true},
              ],
              hitl: null,
            }
          },
        ]
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
    },
    {
      id: 'discord-digest',
      name: 'Discord Digest',
      status: 'done',
      sub: 'Every 6h · ran 1h ago',
      type: 'Loop Agent',
      detail: {
        running: false,
        steps: 4, totalSteps: 4, elapsed: '8s',
        log: [
          {ico:'📡', action:'Fetch', desc:'read #dev last 200 messages', out:'→ 47 msgs', ts:'22:00:00', done:true},
          {ico:'🤖', action:'Summarize', desc:'Claude summarized channel activity', out:'→ 3 action items', ts:'22:00:05', done:true},
          {ico:'💾', action:'Save', desc:'INSERT discord_digest', out:'→ ok', ts:'22:00:08', done:true},
        ],
        hitl: null,
      }
    },
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
    },
    {
      id: 'github-pr-watcher',
      name: 'GitHub PR Watcher',
      status: 'listening',
      sub: 'Trigger: GitHub webhook · ghost-in-the-shell · ● Active',
      type: 'Reactive Agent',
      detail: {
        running: false,
        steps: 0, totalSteps: 0, elapsed: '—',
        log: [],
        hitl: null,
      }
    },
  ]
};
