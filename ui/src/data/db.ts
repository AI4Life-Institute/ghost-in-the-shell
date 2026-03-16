import type { DbCollections, DataNode, TableSourceInfo } from '../types';

export const DB_COLLECTIONS: DbCollections = {
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
export const TABLE_SOURCE_MAP: Record<string, TableSourceInfo> = {};
DB_COLLECTIONS.fromAgents.forEach(c => {
  TABLE_SOURCE_MAP[c.table] = {type:'agent', id:c.sourceAgent!};
});
DB_COLLECTIONS.fromSkills.forEach(c => {
  TABLE_SOURCE_MAP[c.table] = {type:'skill', id:c.sourceSkill!};
});

export const DB: Record<string, { cols: string[]; rows: Record<string, any>[] }> = {
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
export const DATA_FILES: DataNode[] = [
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
