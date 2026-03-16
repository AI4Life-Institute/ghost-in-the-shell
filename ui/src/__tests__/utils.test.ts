import { describe, it, expect } from 'vitest';
import { esc, fmt } from '../utils';

describe('esc()', () => {
  it('escapes & < >', () => {
    expect(esc('<b>test & "it"</b>')).toBe('&lt;b&gt;test &amp; "it"&lt;/b&gt;');
  });
  it('leaves safe strings alone', () => {
    expect(esc('hello world')).toBe('hello world');
  });
  it('handles non-string input', () => {
    expect(esc(42)).toBe('42');
    expect(esc(null)).toBe('null');
    expect(esc(undefined)).toBe('undefined');
  });
  it('handles empty string', () => {
    expect(esc('')).toBe('');
  });
  it('escapes multiple occurrences', () => {
    expect(esc('a < b && b > c')).toBe('a &lt; b &amp;&amp; b &gt; c');
  });
});

describe('fmt()', () => {
  it('wraps plain text in <p> tags', () => {
    expect(fmt('hello')).toBe('<p>hello</p>');
  });
  it('converts inline code', () => {
    expect(fmt('use `foo` here')).toContain('<code>foo</code>');
  });
  it('converts code blocks', () => {
    const out = fmt('```js\nconsole.log(1)\n```');
    expect(out).toContain('<pre>');
    expect(out).toContain('<code>');
    expect(out).toContain('console.log(1)');
  });
  it('renders multiline as multiple <p> tags', () => {
    const out = fmt('line1\nline2');
    expect(out).toContain('<p>line1</p>');
    expect(out).toContain('<p>line2</p>');
  });
  it('renders empty line as &nbsp;', () => {
    const out = fmt('a\n\nb');
    expect(out).toContain('&nbsp;');
  });
});
