import type { AgentDashboard, WidgetType, WidgetSize, WidgetState } from './types.js';

export interface ValidationResult {
  ok: boolean;
  checks: Array<{ label: string; pass: boolean; detail?: string }>;
}

const VALID_TYPES  = new Set<WidgetType>(['conversation', 'chart', 'compute', 'files']);
const VALID_SIZES  = new Set<WidgetSize>(['2x1', '2x2']);
const VALID_STATES = new Set<WidgetState>(['running', 'review', 'done', 'idle']);

export function validateDashboard(db: unknown): ValidationResult {
  const checks: ValidationResult['checks'] = [];
  const d = db as AgentDashboard;

  // 1. agentId present and non-empty
  checks.push({
    label: 'agentId is present and non-empty',
    pass: typeof d?.agentId === 'string' && d.agentId.length > 0,
  });

  // 2. widgets is a non-empty array
  const hasWidgets = Array.isArray(d?.widgets) && d.widgets.length > 0;
  checks.push({ label: 'widgets is a non-empty array', pass: hasWidgets });

  // 3. all widget types are known
  const unknownTypes = hasWidgets
    ? d.widgets.filter(w => !VALID_TYPES.has(w.type)).map(w => `${w.id}:${w.type}`)
    : [];
  checks.push({
    label: 'all widget types are known',
    pass: unknownTypes.length === 0,
    detail: unknownTypes.length ? `Unknown types: ${unknownTypes.join(', ')}` : undefined,
  });

  // 4. all widget sizes are valid
  const badSizes = hasWidgets
    ? d.widgets.filter(w => !VALID_SIZES.has(w.size)).map(w => `${w.id}:${w.size}`)
    : [];
  checks.push({
    label: 'all widget sizes are valid (2x1 or 2x2)',
    pass: badSizes.length === 0,
    detail: badSizes.length ? `Invalid sizes: ${badSizes.join(', ')}` : undefined,
  });

  // 5. all widget states are valid
  const badStates = hasWidgets
    ? d.widgets.filter(w => !VALID_STATES.has(w.state)).map(w => `${w.id}:${w.state}`)
    : [];
  checks.push({
    label: 'all widget states are valid',
    pass: badStates.length === 0,
    detail: badStates.length ? `Invalid states: ${badStates.join(', ')}` : undefined,
  });

  // 6. pipeline widgetIds reference existing widgets
  const widgetIds = new Set(hasWidgets ? d.widgets.map(w => w.id) : []);
  const missingRefs = (d?.pipeline ?? [])
    .filter(s => !widgetIds.has(s.widgetId))
    .map(s => `stage ${s.id} → ${s.widgetId}`);
  checks.push({
    label: 'pipeline widgetIds reference existing widgets',
    pass: missingRefs.length === 0,
    detail: missingRefs.length ? `Missing refs: ${missingRefs.join(', ')}` : undefined,
  });

  return { ok: checks.every(c => c.pass), checks };
}

export function printValidation(result: ValidationResult, agentId: string): void {
  console.log(`\nValidating dashboard: ${agentId}`);
  for (const c of result.checks) {
    const ico = c.pass ? '✓' : '✗';
    console.log(`  ${ico} ${c.label}`);
    if (!c.pass && c.detail) console.log(`      → ${c.detail}`);
  }
  console.log(result.ok ? '\n✓ Dashboard is valid.\n' : '\n✗ Validation failed.\n');
}
