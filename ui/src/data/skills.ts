import type { Skill } from '../types';

export const SKILLS: Record<string, Skill> = {
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
  github_pr: {
    name: 'GitHub PR Reviewer',
    desc: 'When a PR is opened, runs Claude to review diffs and posts a summary comment.',
    params: [
      {key:'repo', label:'Repo', placeholder:'owner/repo'},
      {key:'min_lines', label:'Min lines changed', placeholder:'10'},
    ],
    runs: [
      {status:'done', ts:'Today 09:12', params:'repo=ai4life/ghost-in-the-shell min_lines=10', error:null},
      {status:'done', ts:'Yesterday 16:44', params:'repo=ai4life/ghost-in-the-shell min_lines=10', error:null},
    ]
  },
  digest: {
    name: 'Discord Digest',
    desc: 'Reads recent channel messages, summarizes with Claude, and saves to DB.',
    params: [
      {key:'channel', label:'Channel ID', placeholder:'1234567890'},
      {key:'lookback_hours', label:'Lookback (hours)', placeholder:'6'},
    ],
    runs: [
      {status:'done', ts:'Today 22:00', params:'lookback_hours=6', error:null},
      {status:'done', ts:'Today 16:00', params:'lookback_hours=6', error:null},
    ]
  },
};
