export function esc(s: unknown): string {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

export function fmt(t: string): string {
  t = t.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, l, c) =>
    `<pre><code>${esc(c.trim())}</code><button class="ccbtn" onclick="cpc(this)">Copy</button></pre>`);
  t = t.replace(/`([^`]+)`/g, '<code>$1</code>');
  return t.split('\n').map(l => `<p>${l || '&nbsp;'}</p>`).join('');
}
