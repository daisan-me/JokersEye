/* Verify the preparation-only contract without executing any estimator. */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const web = path.join(__dirname, '../web');
const catalog = JSON.parse(fs.readFileSync(path.join(web, 'juggler-specs.json'), 'utf8'));
const mathSource = fs.readFileSync(path.join(web, 'juggler-math.js'), 'utf8');
const uiSource = fs.readFileSync(path.join(web, 'juggler.js'), 'utf8');
const index = fs.readFileSync(path.join(web, 'index.html'), 'utf8');
const server = fs.readFileSync(path.join(__dirname, '../app/server.py'), 'utf8');

assert.equal(catalog.verifiedOn, '2026-10-04');
assert.equal(catalog.machines.length, 47);
assert.equal(new Set(catalog.machines.map(item => item.id)).size, 47);
assert.equal(catalog.machines.filter(item => item.generation === 6).length, 11);
assert.equal(catalog.unpublishedSeparateBonusTables.length, 5);
for (const machine of catalog.machines) {
  assert.deepEqual(machine.settings.map(row => row.setting), [1, 2, 3, 4, 5, 6]);
  assert.match(machine.sourceUrl, /^https:\/\/www\.kitadenshi\.co\.jp\/slot\//);
  for (const row of machine.settings) {
    assert(Number.isFinite(row.payoutPercent));
    if (machine.generation === 6) {
      assert(Number.isFinite(row.bbDenominator));
      assert(Number.isFinite(row.rbDenominator));
    }
  }
}
// Spot checks of the manufacturer reference only, not calculated results.
const my5 = catalog.machines.find(item => item.id === 'myjuggler5');
assert.deepEqual(my5.settings[5], {setting:6, bbDenominator:229.1, rbDenominator:229.1, combinedDenominator:114.6, payoutPercent:109.4});
const mr = catalog.machines.find(item => item.id === 'mrjuggler');
assert.equal(mr.settings[1].rbDenominator, 354.2);
assert.deepEqual(catalog.machines.find(item => item.id === 'neoimjugglerex').bonusNetCoins, {bb:252,rb:96});

const forbiddenMath = Object.create(Math);
for (const name of ['log', 'log1p', 'exp', 'pow']) forbiddenMath[name] = () => {throw new Error('An estimator must not execute during initialization');};
const context = vm.createContext({Math: forbiddenMath});
vm.runInContext(mathSource, context);
assert.equal(typeof context.JokersJugglerMath.reverseGrapes, 'function');
assert.equal(typeof context.JokersJugglerMath.estimateSettings, 'function');
const registry = context.JokersJugglerMath.createCalculators(catalog);
assert.equal(Object.keys(registry).length, 47);
for (const entry of Object.values(registry)) {
  assert.equal(typeof entry.reverseGrapes, 'function');
  assert.equal(typeof entry.estimateSettings, 'function');
  assert(Object.isFrozen(entry.reference));
}
assert(Object.isFrozen(registry));
assert(!/\b(?:estimateSettings|reverseGrapes)\s*\(/.test(uiSource), 'UI must never invoke an estimator');
assert.match(uiSource, /type="button" disabled/);
assert.match(uiSource, /form\.addEventListener\('submit', event => event\.preventDefault\(\)\)/);
assert.match(index, /data-page="juggler"/);
for (const asset of ['juggler.js', 'juggler-math.js', 'juggler.css', 'juggler-specs.json']) assert(server.includes('"/' + asset + '"'));
console.log('Juggler reference, function definitions and no-execution contract verified. No estimator was called.');
