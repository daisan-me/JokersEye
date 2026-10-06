'use strict';

// Numerical tests are explicitly authorized separately from the disabled UI.
// Only synthetic observations are used; no store CSV/database is opened.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {spawnSync} = require('node:child_process');
const {test} = require('node:test');
const assert = require('node:assert/strict');

const root = path.join(__dirname, '..');
const catalog = JSON.parse(fs.readFileSync(path.join(root, 'web/juggler-specs.json'), 'utf8'));
const context = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(root, 'web/juggler-math.js'), 'utf8'), context,
  {filename: path.join(root, 'web/juggler-math.js')});
const math = context.JokersJugglerMath;
const registry = math.createCalculators(catalog);
const my5 = catalog.machines.find(machine => machine.id === 'myjuggler5');
const clone = value => JSON.parse(JSON.stringify(value));
const syntheticSpecs = Array.from({length: 6}, (_, i) => ({setting: i + 1,
  bbDenominator: 320 - i * 12, rbDenominator: 440 - i * 35}));
const obs = {games: 8000, bb: 28, rb: 25};
const grapeInput = {games: 1000, bb: 2, rb: 3, net: -932,
  replayCount: 100, cherryCoins: 0};
let seed = 0x6e0b2026;
function random() {
  seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
  return seed / 0x100000000;
}
function close(actual, expected, label, absolute = 2e-12, relative = 2e-10) {
  assert(Number.isFinite(actual), `${label}: non-finite ${actual}`);
  assert(Math.abs(actual - expected) <= absolute + relative * Math.abs(expected),
    `${label}: ${actual} != ${expected}`);
}
function rejected(run) {
  assert.throws(run, error => error && /^(?:TypeError|RangeError|Error)$/.test(error.name));
}
function normalized(result) {
  assert.deepEqual(Array.from(result.settings, row => row.setting), [1, 2, 3, 4, 5, 6]);
  for (const row of result.settings) {
    assert(Number.isFinite(row.probability) && row.probability >= 0 && row.probability <= 1);
    close(row.percent, row.probability * 100, 'percent');
  }
  close(result.settings.reduce((sum, row) => sum + row.probability, 0), 1, 'sum', 1e-14, 0);
}

// One batched subprocess, using a separately implemented Decimal power oracle.
const oracleCases = [];
for (const machine of catalog.machines) {
  const known = machine.settings.every(row => row.bbDenominator && row.rbDenominator);
  const settings = known ? machine.settings : syntheticSpecs;
  const cases = [
    {games: 1, bb: 0, rb: 0}, obs,
    {games: 8000, bb: 33, rb: 12},
    {games: 12, bb: 12, rb: 0}, {games: 12, bb: 0, rb: 12},
    {games: 100000, bb: 0, rb: 0},
    {...obs, priors: [0, 3, 0, 8, 1, 0]},
    {...obs, priors: [1e-250, 1e250, 1, 0, 0, 0]},
  ];
  for (const [i, input] of cases.entries()) oracleCases.push({machine, known,
    input, case: {...input, settings}, name: `${machine.id}: Decimal oracle ${i + 1}`});
}
for (let i = 0; i < 120; i++) {
  const games = 1 + Math.floor(random() * 100000);
  const bb = Math.floor(random() * Math.min(games + 1, 100));
  const rb = Math.floor(random() * Math.min(games - bb + 1, 100));
  const priors = Array.from({length: 6}, () => Math.pow(10, random() * 20 - 10));
  const input = {games, bb, rb, priors};
  oracleCases.push({machine: my5, known: true, input,
    case: {...input, settings: my5.settings}, name: `seeded random posterior ${i + 1}`});
}
const identical = syntheticSpecs.map(row => ({...row, bbDenominator: 300, rbDenominator: 400}));
for (const [name, settings, input] of [
  ['maximum-safe games: identical rates retain priors', identical,
    {games: Number.MAX_SAFE_INTEGER, bb: 1000, rb: 1000, priors: [1, 2, 3, 4, 5, 6]}],
  ['one billion games', my5.settings, {games: 1000000000, bb: 3500000, rb: 3000000}],
  ['one million games', my5.settings, {games: 1000000, bb: 3555, rb: 2988}],
  ['tiny other-outcome probabilities stay precise',
    syntheticSpecs.map((row, i) => ({...row, bbDenominator: 1e20, rbDenominator: 1e20 / (i + 1)})),
    {games: Number.MAX_SAFE_INTEGER, bb: 0, rb: 0}],
  ['high-probability custom theory (test fixture only)',
    syntheticSpecs.map((row, i) => ({...row, bbDenominator: 1.4 + i, rbDenominator: 8 + i})),
    {games: 4, bb: 1, rb: 1}],
  ['extreme finite denominators (test fixture only)',
    syntheticSpecs.map((row, i) => ({...row, bbDenominator: Number.MAX_VALUE / (i + 1),
      rbDenominator: 100 + i * 10})), {games: 1000, bb: 1, rb: 2}],
]) oracleCases.push({machine: my5, known: false, input,
  case: {...input, settings}, name});
const oracle = spawnSync(process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3'), [path.join(__dirname, 'juggler_oracle.py')],
  {input: JSON.stringify(oracleCases.map(item => item.case)), encoding: 'utf8', maxBuffer: 4 * 1024 * 1024});
assert.equal(oracle.status, 0, oracle.stderr || oracle.error?.message);
const expected = JSON.parse(oracle.stdout);
assert.equal(expected.length, oracleCases.length);
oracleCases.forEach((item, index) => test(item.name, () => {
  const options = {priors: item.input.priors};
  if (!item.known) options.settingSpecs = item.case.settings;
  const result = registry[item.machine.id].estimateSettings(item.input, options);
  normalized(result);
  result.settings.forEach((row, i) => close(row.probability, expected[index][i], `${item.name}, setting ${i + 1}`));
  assert.equal(result.modelId, item.machine.id);
  assert.equal(result.sourceUrl, item.machine.sourceUrl);
}));

// Independently enumerate all sequences of length 4 over BB/RB/other.
test('posterior agrees with exhaustive outcome-sequence enumeration', () => {
  const likelihood = my5.settings.map(row => {
    const probabilities = [1 / row.bbDenominator, 1 / row.rbDenominator,
      1 - 1 / row.bbDenominator - 1 / row.rbDenominator];
    let sum = 0;
    for (let code = 0; code < 81; code++) {
      let value = code, weight = 1, bb = 0, rb = 0;
      for (let i = 0; i < 4; i++) {
        const outcome = value % 3;
        value = Math.floor(value / 3);
        weight *= probabilities[outcome];
        if (outcome === 0) bb++;
        if (outcome === 1) rb++;
      }
      if (bb === 1 && rb === 1) sum += weight;
    }
    return sum;
  });
  const total = likelihood.reduce((sum, value) => sum + value, 0);
  math.estimateSettings(my5, {games: 4, bb: 1, rb: 1}).settings.forEach((row, i) =>
    close(row.probability, likelihood[i] / total, 'enumerated probability'));
});

// Forward ledger: each normal play pays its bet; replay refunds that bet,
// grape pays its award, and bonus plays add net gain separately. The reverse
// formula itself is not reused to construct expectations.
function ledger(machine, {games, bb, rb, grapes, replays, cherry = 24, other = 15}, override) {
  const options = override ?? (machine.bonusNetCoins ? {} :
    {bbNetCoins: 312, rbNetCoins: 104, grapePayoutCoins: 10}); // synthetic old-model conditions, NOT manufacturer facts
  const bbNet = options.bbNetCoins ?? machine.bonusNetCoins.bb;
  const rbNet = options.rbNetCoins ?? machine.bonusNetCoins.rb;
  const award = options.grapePayoutCoins ?? machine.grapePayoutCoins;
  const bet = options.betCoins ?? 3;
  let cash = 0;
  for (let i = 0; i < games; i++) {
    cash -= bet;
    if (i < grapes) cash += award;
    else if (i < grapes + replays) cash += bet;
    else if (i < grapes + replays + bb) cash += bbNet;
    else if (i < grapes + replays + bb + rb) cash += rbNet;
  }
  cash += cherry;
  cash += other;
  return {input: {games, bb, rb, net: cash, replayCount: replays,
    cherryCoins: cherry, otherPayoutCoins: other}, options};
}
for (const machine of catalog.machines) {
  for (const [i, values] of [
    {games: 1000, bb: 2, rb: 3, grapes: 125, replays: 137},
    {games: 1000, bb: 0, rb: 0, grapes: 0, replays: 137},
    {games: 100, bb: 2, rb: 3, grapes: 80, replays: 15, cherry: 0, other: 0},
  ].entries()) test(`${machine.id}: forward coin ledger ${i + 1}`, () => {
    const fixture = ledger(machine, values);
    const before = JSON.stringify(fixture);
    const result = registry[machine.id].reverseGrapes(fixture.input, fixture.options);
    close(result.estimatedCount, values.grapes, 'grape count', 1e-10, 0);
    close(result.probability, values.grapes / values.games, 'grape probability');
    if (!values.grapes) assert.equal(result.denominator, null);
    else close(result.denominator, values.games / values.grapes, 'grape denominator');
    assert.equal(result.approximate, true);
    assert.equal(result.modelId, machine.id);
    assert.equal(result.sourceUrl, machine.sourceUrl);
    assert.equal(JSON.stringify(fixture), before, 'input/options changed');
    if (values.replays && values.cherry) {
      const rateInput = {...fixture.input, replayDenominator: values.games / values.replays,
        cherryCoinsPerGame: values.cherry / values.games};
      delete rateInput.replayCount;
      delete rateInput.cherryCoins;
      close(registry[machine.id].reverseGrapes(rateInput, fixture.options).estimatedCount,
        values.grapes, 'rate / count equivalence', 1e-10, 0);
    }
  });
  if (!machine.bonusNetCoins) test(`${machine.id}: missing old payout parameters must reject`, () => {
    rejected(() => registry[machine.id].reverseGrapes(grapeInput));
  });
  if (machine.settings.some(row => row.bbDenominator == null || row.rbDenominator == null)) {
    test(`${machine.id}: unpublished bonus rates must reject`, () => {
      rejected(() => registry[machine.id].estimateSettings(obs));
    });
  }
}
for (let i = 0; i < 100; i++) test(`seeded random forward ledger ${i + 1}`, () => {
  const games = 1000 + Math.floor(random() * 9000);
  const values = {games, bb: Math.floor(random() * 30), rb: Math.floor(random() * 30),
    grapes: Math.floor(random() * games * 0.3), replays: Math.floor(random() * games * 0.2),
    cherry: Math.floor(random() * 100), other: Math.floor(random() * 100)};
  const fixture = ledger(my5, values, {bbNetCoins: 252, rbNetCoins: 96, grapePayoutCoins: 8, betCoins: 3});
  close(math.reverseGrapes(my5, fixture.input, fixture.options).estimatedCount, values.grapes, 'random ledger');
});

const invalidNumbers = [null, undefined, '', ' ', '\t\n', true, false, [], [0], {},
  {valueOf: () => 0}, NaN, Infinity, -Infinity, 'NaN', 'Infinity', '0x10', 1n];
for (const field of ['games', 'bb', 'rb']) for (const [i, value] of invalidNumbers.entries()) {
  test(`reject ${field} invalid numeric type ${i + 1}`, () => {
    rejected(() => math.estimateSettings(my5, {...obs, [field]: value}));
    rejected(() => math.reverseGrapes(my5, {...grapeInput, [field]: value}));
  });
}
for (const field of ['games', 'bb', 'rb']) for (const value of [-1, 0.5, Number.MAX_SAFE_INTEGER + 1]) {
  test(`reject ${field} out-of-range ${value}`, () => {
    rejected(() => math.estimateSettings(my5, {...obs, [field]: value}));
    rejected(() => math.reverseGrapes(my5, {...grapeInput, [field]: value}));
  });
}
test('reject zero games and total bonus count above games', () => {
  for (const input of [{games: 0, bb: 0, rb: 0}, {games: 5, bb: 3, rb: 3},
    {games: Number.MAX_SAFE_INTEGER, bb: Number.MAX_SAFE_INTEGER, rb: 1}]) {
    rejected(() => math.estimateSettings(my5, input));
    rejected(() => math.reverseGrapes(my5, {...grapeInput, ...input}));
  }
});
test('decimal numeric strings are accepted consistently', () => {
  const result = math.estimateSettings(my5, {games: '8000', bb: '28', rb: '25'});
  const numeric = math.estimateSettings(my5, obs);
  assert.deepEqual(result, numeric);
  const strings = Object.fromEntries(Object.entries(grapeInput).map(([key, value]) => [key, String(value)]));
  assert.deepEqual(math.reverseGrapes(my5, strings), math.reverseGrapes(my5, grapeInput));
});
test('posterior is invariant to common prior scaling', () => {
  const priors = [1, 2, 3, 4, 5, 6];
  const first = math.estimateSettings(my5, obs, {priors});
  for (const scale of [1e-250, 1e250]) {
    const next = math.estimateSettings(my5, obs, {priors: priors.map(value => value * scale)});
    next.settings.forEach((row, i) => close(row.probability, first.settings[i].probability, 'scaled prior'));
  }
});
for (const [name, priors] of [
  ['all zero', [0, 0, 0, 0, 0, 0]], ['negative', [1, -1, 1, 1, 1, 1]],
  ['wrong length', [1, 1]], ['sparse', [1, , , , , 1]], ['not array', {}],
  ['null item', [1, 1, null, 1, 1, 1]], ['blank', [1, 1, ' ', 1, 1, 1]],
  ['infinite', [1, 1, Infinity, 1, 1, 1]],
]) test(`reject prior weights: ${name}`, () => rejected(() => math.estimateSettings(my5, obs, {priors})));
test('point prior remains a point mass', () => {
  const result = math.estimateSettings(my5, obs, {priors: [0, 0, 0, 0, Number.MIN_VALUE, 0]});
  assert.deepEqual(Array.from(result.settings, row => row.probability), [0, 0, 0, 0, 1, 0]);
});
for (const [name, specs] of [
  ['sparse', new Array(6)], ['null row', [null, ...my5.settings.slice(1)]],
  ['duplicate setting', my5.settings.map(row => ({...row, setting: 1}))],
  ['wrong order', [...my5.settings].reverse()], ['wrong length', my5.settings.slice(1)],
  ['wrong shape', {}],
  ['probability sum >= 1', syntheticSpecs.map(row => ({...row, bbDenominator: 2, rbDenominator: 2}))],
  ['missing denominator', syntheticSpecs.map(row => ({...row, rbDenominator: null}))],
  ['denominator below 1', syntheticSpecs.map(row => ({...row, bbDenominator: 0.1}))],
]) test(`reject setting table: ${name}`, () => rejected(() => math.estimateSettings(my5, obs, {settingSpecs: specs})));

test('no payout/grape evidence is counted twice in setting inference', () => {
  const options = {priors: [1, 2, 3, 4, 5, 6]};
  const before = JSON.stringify({my5, obs, options});
  const first = math.estimateSettings(my5, obs, options);
  const second = math.estimateSettings({...my5,
    settings: my5.settings.map(row => ({...row, payoutPercent: 999}))},
    {...obs, net: 999999, grapes: 99999}, options);
  assert.deepEqual(first, second);
  assert.equal(JSON.stringify({my5, obs, options}), before);
});
for (const [name, change] of [
  ['missing replay', input => {delete input.replayCount;}],
  ['both replay modes', input => {input.replayDenominator = 7.3;}],
  ['negative replay', input => {input.replayCount = -1;}],
  ['fractional replay count', input => {input.replayCount = 0.5;}],
  ['too many replays', input => {input.replayCount = 996;}],
  ['zero replay denominator', input => {delete input.replayCount; input.replayDenominator = 0;}],
  ['missing cherries', input => {delete input.cherryCoins;}],
  ['both cherry modes', input => {input.cherryCoinsPerGame = 0;}],
  ['negative cherries', input => {input.cherryCoins = -1;}],
  ['negative cherry rate', input => {delete input.cherryCoins; input.cherryCoinsPerGame = -1;}],
  ['negative other payouts', input => {input.otherPayoutCoins = -1;}],
  ['missing net', input => {delete input.net;}],
  ['fractional net', input => {input.net = 0.1;}],
  ['negative grape count', input => {input.net = -99999;}],
  ['impossible grape count', input => {input.net = 99999;}],
]) test(`reject inverse-grape input: ${name}`, () => {
  const input = {...grapeInput};
  change(input);
  rejected(() => math.reverseGrapes(my5, input));
});
for (const field of ['net', 'replayCount', 'cherryCoins', 'otherPayoutCoins']) {
  for (const [i, value] of invalidNumbers.entries()) {
    // Optional other payouts allow undefined/null to mean omission (default 0).
    if (field === 'otherPayoutCoins' && value == null) continue;
    test(`reject ${field} numeric type ${i + 1}`, () =>
      rejected(() => math.reverseGrapes(my5, {...grapeInput, [field]: value})));
  }
}
for (const field of ['bbNetCoins', 'rbNetCoins', 'grapePayoutCoins', 'betCoins']) {
  for (const value of [-1, Infinity, NaN, ' ', [], {}]) test(`reject ${field}: ${String(value)}`, () =>
    rejected(() => math.reverseGrapes(my5, grapeInput, {[field]: value})));
}
test('zero bet and zero grape award are rejected, zero bonus net is allowed', () => {
  rejected(() => math.reverseGrapes(my5, grapeInput, {betCoins: 0}));
  rejected(() => math.reverseGrapes(my5, grapeInput, {grapePayoutCoins: 0}));
  const fixture = ledger(my5, {games: 100, bb: 2, rb: 3, grapes: 10, replays: 10, cherry: 0, other: 0},
    {bbNetCoins: 0, rbNetCoins: 0, grapePayoutCoins: 8, betCoins: 3});
  close(math.reverseGrapes(my5, fixture.input, fixture.options).estimatedCount, 10, 'zero net bonus');
});
test('arithmetic overflow must never escape as NaN/Infinity', () => {
  rejected(() => math.reverseGrapes(my5, {...grapeInput, net: 0},
    {betCoins: Number.MAX_VALUE, bbNetCoins: Number.MAX_VALUE}));
  rejected(() => math.reverseGrapes(my5, {...grapeInput, net: 0, cherryCoins: undefined,
    cherryCoinsPerGame: Number.MAX_VALUE}));
});
test('inverse-grape results reject insufficient numeric precision', () => {
  rejected(() => math.reverseGrapes(my5, grapeInput, {grapePayoutCoins: Number.MIN_VALUE}));
  rejected(() => math.reverseGrapes(my5, {games: 1, bb: 0, rb: 0, net: 0,
    replayCount: 0, cherryCoins: 1 - Number.EPSILON},
    {betCoins: 1, grapePayoutCoins: Number.MAX_VALUE}));
  rejected(() => math.reverseGrapes(my5, {games: 1, bb: 0, rb: 0, net: 0,
    replayCount: 0, cherryCoins: 1 - Number.EPSILON},
    {betCoins: 1, grapePayoutCoins: 1e300}));
});
test('optional other payouts default to zero, not to a fabricated observation', () => {
  const base = math.reverseGrapes(my5, grapeInput);
  for (const otherPayoutCoins of [0, undefined, null]) {
    assert.deepEqual(math.reverseGrapes(my5, {...grapeInput, otherPayoutCoins}), base);
  }
});
test('input/machine/options object shape is validated for both calculators', () => {
  for (const input of [null, undefined, [], 0, '8000']) {
    rejected(() => math.estimateSettings(my5, input));
    rejected(() => math.reverseGrapes(my5, input));
  }
  for (const machine of [null, undefined, [], {}, {...my5, id: ' '}]) {
    rejected(() => math.estimateSettings(machine, obs));
    rejected(() => math.reverseGrapes(machine, grapeInput));
  }
  for (const options of [null, [], 0, '']) {
    rejected(() => math.estimateSettings(my5, obs, options));
    rejected(() => math.reverseGrapes(my5, grapeInput, options));
  }
});
test('roundoff near a valid zero-grape boundary does not reject', () => {
  const input = {games: 100, bb: 0, rb: 0, net: -300,
    replayCount: 0, cherryCoins: 0};
  const result = math.reverseGrapes(my5, input, {betCoins: 2.9999999999999996});
  assert.equal(result.estimatedCount, 0);
  assert.equal(result.denominator, null);
});
test('roundoff near the upper grape-count boundary is clamped only at the boundary', () => {
  const result = math.reverseGrapes(my5, {games: 100, bb: 0, rb: 0, net: 500,
    replayCount: 0, cherryCoins: 0}, {betCoins: 3.0000000000000004});
  assert.equal(result.estimatedCount, 100);
  const input = {games: 100, bb: 0, rb: 0, net: 501, replayCount: 0, cherryCoins: 0};
  rejected(() => math.reverseGrapes(my5, input));
});
test('fractional inverse result is preserved (not rounded to a count)', () => {
  const result = math.reverseGrapes(my5, {...grapeInput, net: grapeInput.net + 1});
  close(result.estimatedCount % 1, 0.125, 'fractional grape count');
});
test('registry clones and freezes the reference recursively', () => {
  const local = clone(catalog);
  const calculators = math.createCalculators(local);
  const entry = calculators[my5.id];
  assert(Object.isFrozen(calculators) && Object.isFrozen(entry));
  for (const item of [entry.reference, entry.reference.settings,
    entry.reference.settings[0], entry.reference.bonusNetCoins]) assert(Object.isFrozen(item));
  local.machines.find(machine => machine.id === my5.id).settings[0].bbDenominator = 99;
  assert.equal(entry.reference.settings[0].bbDenominator, my5.settings[0].bbDenominator);
  const input = clone(obs);
  const first = entry.estimateSettings(input);
  first.settings[0].probability = -1;
  first.evidence.games = 1;
  first.priorWeights[0] = 999;
  normalized(entry.estimateSettings(input));
  assert.deepEqual(input, obs);
});
test('registry rejects duplicate IDs and malformed references', () => {
  for (const local of [null, {}, {machines: {}}, {machines: [null]},
    {machines: [{...my5, id: ''}]}, {machines: [{...my5, id: 42}]},
    {machines: [my5, my5]}, {machines: new Array(2)}]) rejected(() => math.createCalculators(local));
});
test('registry handles prototype-like IDs safely', () => {
  const ids = ['__proto__', 'constructor', 'toString'];
  const local = math.createCalculators({machines: ids.map(id => ({...my5, id}))});
  assert.equal(Object.keys(local).length, ids.length);
  for (const id of ids) assert.equal(local[id].estimateSettings(obs).modelId, id);
});
test('empty registry has no fabricated machines', () => {
  const result = math.createCalculators({machines: []});
  assert.equal(Object.keys(result).length, 0);
  assert(Object.isFrozen(result));
});
test('direct and bound function paths give identical outputs for every model', () => {
  for (const machine of catalog.machines) {
    const options = machine.settings[0].bbDenominator ? {} : {settingSpecs: syntheticSpecs};
    assert.deepEqual(clone(registry[machine.id].estimateSettings(obs, options)), clone(math.estimateSettings(machine, obs, options)));
    const fixture = ledger(machine, {games: 1000, bb: 2, rb: 3, grapes: 125, replays: 137});
    assert.deepEqual(registry[machine.id].reverseGrapes(fixture.input, fixture.options),
      math.reverseGrapes(machine, fixture.input, fixture.options));
  }
});
