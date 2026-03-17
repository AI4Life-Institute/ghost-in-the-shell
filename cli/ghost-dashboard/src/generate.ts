import fs from 'fs';
import path from 'path';
import os from 'os';
import type { AgentDashboard } from './types.js';
import { validateDashboard, printValidation } from './validate.js';

const CATALOG_DIR = path.join(os.homedir(), '.config/ghost/widgets');
const SAMPLES_DIR = path.join(os.homedir(), '.config/ghost/dashboard-samples');
const OUTPUT_DIR  = path.join(os.homedir(), '.gits/dashboards');

function loadCatalogs(): string {
  if (!fs.existsSync(CATALOG_DIR)) return '';
  return fs.readdirSync(CATALOG_DIR)
    .filter(f => f.endsWith('.md'))
    .map(f => fs.readFileSync(path.join(CATALOG_DIR, f), 'utf8'))
    .join('\n\n---\n\n');
}

function loadSamples(): string {
  if (!fs.existsSync(SAMPLES_DIR)) return '';
  return fs.readdirSync(SAMPLES_DIR)
    .filter(f => f.endsWith('.json'))
    .map(f => {
      const raw = fs.readFileSync(path.join(SAMPLES_DIR, f), 'utf8');
      return `### Sample: ${f}\n\`\`\`json\n${raw}\n\`\`\``;
    })
    .join('\n\n');
}

function buildPrompt(agentId: string, agentContext: string): string {
  const catalogs = loadCatalogs();
  const samples  = loadSamples();

  return `You are an AI that generates Ghost agent dashboard configurations.

## Widget Catalog
${catalogs || '(no catalog files found)'}

## Sample Dashboards
${samples || '(no sample files found)'}

## Task
Generate a dashboard JSON for the agent described below.
Return ONLY valid JSON matching the AgentDashboard schema — no explanation, no markdown.

AgentDashboard schema:
{
  "agentId": string,
  "widgets": Widget[],          // 2-5 widgets
  "pipeline": PipelineStage[]   // optional; only if agent has review stages
}

Widget schema:
{
  "id": string,          // "<agentId>-w<n>"
  "type": "conversation" | "chart" | "compute" | "files",
  "size": "2x1" | "2x2",
  "agentId": string,
  "state": "idle",       // always "idle" for generated configs
  "title": string,
  "config": { ... }      // type-specific config
}

## Agent Context
Agent ID: ${agentId}
${agentContext}

Generate the dashboard JSON now:`;
}

async function callClaude(prompt: string): Promise<string> {
  // Use Claude API via environment variable key
  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) throw new Error('ANTHROPIC_API_KEY not set');

  const res = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'x-api-key': apiKey,
      'anthropic-version': '2023-06-01',
      'content-type': 'application/json',
    },
    body: JSON.stringify({
      model: 'claude-opus-4-6',
      max_tokens: 2048,
      messages: [{ role: 'user', content: prompt }],
    }),
  });

  if (!res.ok) {
    const err = await res.text();
    throw new Error(`Claude API error ${res.status}: ${err}`);
  }

  const data = await res.json() as { content: Array<{ type: string; text: string }> };
  const text = data.content.find(c => c.type === 'text')?.text ?? '';

  // Extract JSON from response (in case Claude adds any wrapping text)
  const jsonMatch = text.match(/\{[\s\S]*\}/);
  if (!jsonMatch) throw new Error('No JSON found in Claude response');
  return jsonMatch[0];
}

export interface GenerateOptions {
  dryRun: boolean;
  agentFile?: string;
}

export async function generate(agentId: string, opts: GenerateOptions): Promise<void> {
  console.log(`\nGenerating dashboard for: ${agentId}`);

  // Load agent context from file if provided, else use agentId
  let agentContext = `Agent ID: ${agentId}`;
  if (opts.agentFile && fs.existsSync(opts.agentFile)) {
    agentContext = fs.readFileSync(opts.agentFile, 'utf8');
    console.log(`  Loaded agent context from: ${opts.agentFile}`);
  }

  const prompt = buildPrompt(agentId, agentContext);
  console.log('  Calling claude-opus-4-6…');

  let jsonStr: string;
  try {
    jsonStr = await callClaude(prompt);
  } catch (e) {
    console.error(`  ✗ Claude API error: ${(e as Error).message}`);
    process.exit(1);
  }

  let dashboard: AgentDashboard;
  try {
    dashboard = JSON.parse(jsonStr);
  } catch {
    console.error('  ✗ Failed to parse Claude response as JSON');
    console.error(jsonStr);
    process.exit(1);
  }

  // Validate
  const result = validateDashboard(dashboard);
  printValidation(result, agentId);

  if (!result.ok) {
    console.error('✗ Dashboard failed validation — not written.');
    process.exit(1);
  }

  if (opts.dryRun) {
    console.log('--dry-run: output (not written):');
    console.log(JSON.stringify(dashboard, null, 2));
    return;
  }

  // Write to ~/.gits/dashboards/<agentId>.json
  fs.mkdirSync(OUTPUT_DIR, { recursive: true });
  const outPath = path.join(OUTPUT_DIR, `${agentId}.json`);
  fs.writeFileSync(outPath, JSON.stringify(dashboard, null, 2));
  console.log(`✓ Written to: ${outPath}`);
}
