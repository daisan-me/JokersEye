// Current-table readiness shared with the actual desktop source browser.
const assert = require('node:assert/strict');
const {sourcePageReady, sourceAccessRestricted} = require('../web/source-readiness.js');
const page = tables => ({readyState:'complete',headings:['10/3(土) ゴッサムシティ'],tables});
const task = {kind:'bonus-model',seats:['1','2'],url:'https://min-repo.com/3389999/?kishu=test'};
assert.equal(sourcePageReady(page([['順位','店名'],['1','TEST']]),task),false);
assert.equal(sourcePageReady(page([[['台番','G数','BB','RB'],['1','0','0','0']]]),task),false);
const full = [[['台番','G数','BB','RB'],['1','0','0','0'],['平均','0','0','0'],['2','100','1','-']]];
assert.equal(sourcePageReady(page(full),task),true);
assert.equal(sourcePageReady({...page(full),readyState:'loading'},task),false);
assert.equal(sourcePageReady(page([[['台番','G数','BB','RB'],['1','0','0','0'],['1','0','0','0']]]),task),false);
const single={...task,kind:'bonus-seat',seats:['259']};
const base=[['機種','差枚','G数','出率'],['TEST','-','100','-']];
const past=[['日付','G数','BB','RB'],['10/2(金)','200','2','3']];
assert.equal(sourcePageReady(page([base,past]),single),false);
assert.equal(sourcePageReady(page([base,past,[['BB','RB','合成'],['0','0','-']]]),single),true);
assert.equal(sourcePageReady(page([base,past,[['BB','RB','合成'],['0']]]),single),false);
assert.equal(sourceAccessRestricted({body:{innerText:'Verify you are human'},querySelectorAll:()=>[]}),true);
assert.equal(sourceAccessRestricted({body:{innerText:'10/3(土) ゴッサムシティ'},querySelectorAll:()=>[]}),false);
assert.equal(sourceAccessRestricted({body:{innerText:''},querySelectorAll:()=>[{getClientRects:()=>[{}]}]}),true);
const captures = require('./fixtures/min-repo-bonuses.json');
const all = captures[1].tables.flat().filter(row => /^\d+$/.test(row.cells[1] || ''));
const normalized = text => text.replace(/\s+/g,' ').trim();
for (const capture of captures.filter(item => item.model)) {
  const seats = all.filter(row => normalized(row.cells[0]) === normalized(capture.model)).map(row => row.cells[1]);
  const task = {kind:capture.singletonSeat ? 'bonus-seat' : 'bonus-model',seats,url:capture.url};
  assert.equal(sourcePageReady(page(capture.tables.map(table => table.map(row => row.cells))),task),true,capture.model);
}
console.log('source readiness: unrelated/incomplete/duplicate/past-day tables rejected; zero/missing current fields accepted');
