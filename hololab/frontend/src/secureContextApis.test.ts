/// <reference types="vite/client" />
import { describe, expect, it } from 'vitest';
import * as ts from 'typescript';

// The only approved capability adapters. Each checks availability and has
// HTTP coverage; new call sites must use these adapters, not browser globals.
const adapters: Record<string, string[]> = {
  './newId.ts': ['randomUUID'],
  './canvas/referenceAction.ts': ['clipboard'],
  './canvas/JobLogModal.tsx': ['clipboard'],
};
const restricted = new Set(['randomUUID', 'subtle', 'clipboard']);
function accesses(source: string, filename = "source.tsx"): string[] {
  const file = ts.createSourceFile(filename, source, ts.ScriptTarget.Latest, true, /\.[jt]sx$/.test(filename) ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const result: string[] = [];
  function visit(node: ts.Node) {
    let property: string | undefined;
    if (ts.isPropertyAccessExpression(node)) property = node.name.text;
    if (ts.isElementAccessExpression(node) && ts.isStringLiteralLike(node.argumentExpression)) property = node.argumentExpression.text;
    if (ts.isBindingElement(node)) {
      const key = node.propertyName ?? node.name;
      if (ts.isIdentifier(key) || ts.isStringLiteralLike(key)) property = key.text;
    }
    if (property && restricted.has(property)) result.push(property);
    ts.forEachChild(node, visit);
  }
  visit(file);
  return result;
}

describe('HTTP-compatible browser API policy', () => {
  it('recognizes direct, optional, bracket and destructured access, ignoring comments', () => {
    expect(accesses(`crypto.randomUUID(); globalThis.crypto?.subtle; navigator['clipboard']; const {randomUUID: uuid} = crypto; // crypto.subtle\n`))
      .toEqual(['randomUUID', 'subtle', 'clipboard', 'randomUUID']);
    expect(accesses('const id = <T>(value: T) => crypto.randomUUID();', 'generic.ts')).toEqual(['randomUUID']);
  });
  it('restricts secure-context APIs to tested fallback adapters throughout app source', () => {
    const sources = import.meta.glob('./**/*.{ts,tsx,js,jsx,mjs}', { eager: true, query: '?raw', import: 'default' }) as Record<string,string>;
    const violations = Object.entries(sources).filter(([path]) => !/\.(test|spec)\./.test(path)).flatMap(([path, source]) =>
      accesses(source, path).filter(name => !adapters[path]?.includes(name)).map(name => `${path}: ${name} — use newId() / writeToClipboard() or a tested HTTP-compatible adapter`));
    expect(violations).toEqual([]);
  });
});
