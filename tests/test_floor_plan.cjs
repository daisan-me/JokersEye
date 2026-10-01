const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

const map = fs.readFileSync(path.join(__dirname, '../web/map.js'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '../web/map.css'), 'utf8');

assert(map.includes('machine-seat-grid'));
assert(map.includes('台番号マップ'));
assert(map.includes('機種マップ'));
assert(!map.includes('FloorPlan.render'));
assert(css.includes('.machine-seat-grid'));
assert(css.includes('grid-template-columns:repeat(20'));
console.log('Square tile map rendering: passed');
