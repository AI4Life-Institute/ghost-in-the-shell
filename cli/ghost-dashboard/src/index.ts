#!/usr/bin/env node
import fs from 'fs';
import path from 'path';
import os from 'os';
import { generate } from './generate.js';
import { validateDashboard, printValidation } from './validate.js';

const DASHBOARDS_DIR = path.join(os.homedir(), '.gits/dashboards');

function usage(): void {
  console.log(`
ghost-dashboard — AI-powered dashboard config tool

Commands:
  generate <agent-id> [--dry-run] [--agent-file <path>]
      Generate a dashboard config for the given agent using Claude.
      --dry-run       Print JSON to stdout, do not write file.
      --agent-file    Path to agent definition file for richer context.

  validate <agent-id>
      Validate the dashboard config at ~/.gits/dashboards/<agent-id>.json.
      Exit 0 on success, 1 on failure.

Examples:
  ghost-dashboard generate btc-monitor
  ghost-dashboard generate nash-reporter --dry-run
  ghost-dashboard validate btc-monitor
`);
}

async function main(): Promise<void> {
  const args = process.argv.slice(2);
  const cmd = args[0];

  if (!cmd || cmd === '--help' || cmd === '-h') {
    usage();
    process.exit(0);
  }

  if (cmd === 'generate') {
    const agentId = args[1];
    if (!agentId) { console.error('Error: agent-id is required'); usage(); process.exit(1); }

    const dryRun = args.includes('--dry-run');
    const agentFileIdx = args.indexOf('--agent-file');
    const agentFile = agentFileIdx >= 0 ? args[agentFileIdx + 1] : undefined;

    await generate(agentId, { dryRun, agentFile });
    return;
  }

  if (cmd === 'validate') {
    const agentId = args[1];
    if (!agentId) { console.error('Error: agent-id is required'); usage(); process.exit(1); }

    const filePath = path.join(DASHBOARDS_DIR, `${agentId}.json`);
    if (!fs.existsSync(filePath)) {
      console.error(`✗ Not found: ${filePath}`);
      process.exit(1);
    }

    let dashboard: unknown;
    try {
      dashboard = JSON.parse(fs.readFileSync(filePath, 'utf8'));
    } catch (e) {
      console.error(`✗ Failed to parse JSON: ${(e as Error).message}`);
      process.exit(1);
    }

    const result = validateDashboard(dashboard);
    printValidation(result, agentId);
    process.exit(result.ok ? 0 : 1);
  }

  console.error(`Unknown command: ${cmd}`);
  usage();
  process.exit(1);
}

main().catch(e => { console.error(e); process.exit(1); });
