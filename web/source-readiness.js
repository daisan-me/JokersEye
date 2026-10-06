'use strict';

// Used by the ordinary desktop source browser and its DOM regression tests.
// Readiness is about the requested current-day table, not unrelated rankings.
function sourcePageReady(page, task) {
  if (page.readyState === 'loading' || !page.headings.length) return false;
  const tables = page.tables;
  const has = (table, names) => table.length > 1 && names.every(name => table[0].includes(name));
  if (task.kind === 'bonus-model' || task.kind === 'bonus-seat') {
    const expected = new Set((task.seats || []).map(String));
    const found = [];
    for (const table of tables.filter(t => has(t, ['台番', 'G数', 'BB', 'RB']))) {
      const index = table[0].indexOf('台番');
      for (const row of table.slice(1)) {
        if (row.length === table[0].length && /^\d+$/.test(row[index])) found.push(String(Number(row[index])));
      }
    }
    if (found.length) return found.length === expected.size && new Set(found).size === found.length && found.every(seat => expected.has(seat));
    if (task.kind !== 'bonus-seat' || expected.size !== 1) return false;
    return tables.some(t => has(t, ['機種', 'G数']) && !t[0].includes('台番') && t.length === 2) &&
      tables.some(t => t.length === 2 && t[0][0] === 'BB' && t[0][1] === 'RB' && t[1].length === t[0].length);
  }
  if (new URL(task.url).searchParams.get('kishu') === 'all') return tables.some(t => has(t, ['機種', '台番', 'G数']));
  return tables.length > 0;
}

function sourceDocumentReady(doc, task) {
  return sourcePageReady({readyState: doc.readyState,
    headings: Array.from(doc.querySelectorAll('h1'), h => h.textContent.trim()),
    tables: Array.from(doc.querySelectorAll('table'), table => Array.from(table.querySelectorAll('tr'), row =>
      Array.from(row.querySelectorAll('td,th'), cell => cell.textContent.trim())))}, task);
}

function sourceAccessRestricted(doc) {
  const text = doc.body?.innerText || '';
  if (/verify (that )?you are human|checking your browser|人間であることを確認|ロボットではないことを確認|認証が必要です/i.test(text)) return true;
  return Array.from(doc.querySelectorAll('#challenge-stage,#cf-challenge-running,.g-recaptcha,iframe[src*="hcaptcha"],input[type="password"]'))
    .some(element => element.getClientRects().length > 0);
}

if (typeof module !== 'undefined') module.exports = {sourcePageReady, sourceAccessRestricted};
