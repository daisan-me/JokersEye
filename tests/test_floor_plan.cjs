const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const vm = require('node:vm');

const map = fs.readFileSync(path.join(__dirname, '../web/map.js'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '../web/map.css'), 'utf8');
const floor = fs.readFileSync(path.join(__dirname, '../web/fixed-floor.js'), 'utf8');
const referencePath = path.join(__dirname, 'fixtures/floor');
const saved = JSON.parse(fs.readFileSync(path.join(referencePath, 'numbered-map-1.4-ex-is-id/numbered-map-1.4-ex-is-id.json'), 'utf8'));
const shipped = JSON.parse(fs.readFileSync(path.join(__dirname, '../web/fixed-floor.json'), 'utf8'));
const display = JSON.parse(fs.readFileSync(path.join(__dirname, '../web/fixed-floor-display.json'), 'utf8'));
const physical = JSON.parse(fs.readFileSync(path.join(referencePath, 'physical-skeleton-map.json'), 'utf8'));
const numbers = JSON.parse(fs.readFileSync(path.join(referencePath, 'numbered-map-1.2/numbered-map-1.2.json'), 'utf8'));
const identities = JSON.parse(fs.readFileSync(path.join(referencePath, 'numbered-map-1.3/numbered-map-1.3.json'), 'utf8'));
const history = JSON.parse(fs.readFileSync(path.join(__dirname, '../web/map-history.json'), 'utf8'));
assert.deepEqual(shipped, saved, 'Ship the exact fixed source, not recreated arcs');
const size = display.baseTileSize * display.tileScale;
assert.equal(size, 10.75);
assert.equal(display.centerMethod, 'mean-of-island-tile-centers');
assert.deepEqual(display.radialOutward.map(item => item.seatNumber).sort((a,b) => a-b), [200,223,224,231,232]);
const displayTiles = shipped.faces.flatMap(face => {
  const center = {x: face.tiles.reduce((sum,tile) => sum + tile.x / face.count, 0), y: face.tiles.reduce((sum,tile) => sum + tile.y / face.count, 0)};
  return face.tiles.map(tile => {
    const adjustment = display.radialOutward.find(item => item.seatNumber === tile.seatNumber);
    if (!adjustment) return {...tile, face: face.id};
    assert.equal(adjustment.faceId, face.id);
    const dx = tile.x - center.x, dy = tile.y - center.y, radius = Math.hypot(dx,dy);
    const x = tile.x + dx / radius * adjustment.distance, y = tile.y + dy / radius * adjustment.distance;
    assert(Math.abs((x-tile.x)*dy - (y-tile.y)*dx) < 1e-8, 'Shift must be radial');
    assert((x-tile.x)*dx + (y-tile.y)*dy > 0, 'Shift must be outward');
    return {...tile, x, y, face: face.id};
  });
});

function corners(tile, width) {
  const angle = tile.angle * Math.PI / 180, c = Math.cos(angle), s = Math.sin(angle), h = width/2;
  return [[-h,-h],[h,-h],[h,h],[-h,h]].map(([x,y]) => [tile.x+x*c-y*s, tile.y+x*s+y*c]);
}
function overlap(a,b) {
  for (const points of [a,b]) for (let i=0;i<4;i++) {
    const start=points[i], end=points[(i+1)%4], axis=[start[1]-end[1],end[0]-start[0]];
    const pa=a.map(p=>p[0]*axis[0]+p[1]*axis[1]), pb=b.map(p=>p[0]*axis[0]+p[1]*axis[1]);
    if (Math.max(...pa)<=Math.min(...pb) || Math.max(...pb)<=Math.min(...pa)) return false;
  }
  return true;
}
const polygons = displayTiles.map(tile => corners(tile, size + .1));
for (let i=0;i<310;i++) for (let j=i+1;j<310;j++) {
  assert(!overlap(polygons[i],polygons[j]), `Tiles ${displayTiles[i].seatNumber}/${displayTiles[j].seatNumber} overlap (including clearance)`);
}
const outline = shipped.wallGeometry.outline;
function inside([x,y]) {
  let result = false;
  for(let i=0,j=outline.length-1;i<outline.length;j=i++) {
    const [ax,ay]=outline[i], [bx,by]=outline[j];
    if ((ay>y)!==(by>y) && x < (bx-ax)*(y-ay)/(by-ay)+ax) result=!result;
  }
  return result;
}
function distance(point,a,b) {
  const dx=b[0]-a[0], dy=b[1]-a[1], length=dx*dx+dy*dy;
  const t=length ? Math.max(0,Math.min(1,((point[0]-a[0])*dx+(point[1]-a[1])*dy)/length)) : 0;
  return Math.hypot(point[0]-a[0]-t*dx,point[1]-a[1]-t*dy);
}
function cross(a,b,p) {return (b[0]-a[0])*(p[1]-a[1])-(b[1]-a[1])*(p[0]-a[0]);}
function intersects(a,b,c,d) {return cross(a,b,c)*cross(a,b,d)<0 && cross(c,d,a)*cross(c,d,b)<0;}
let wallClearance=Infinity;
for(const polygon of polygons) {
  assert(polygon.every(inside), 'Every enlarged square must remain inside the wall');
  for(let i=0;i<4;i++) for(let j=0;j<outline.length;j++) {
    const a=polygon[i], b=polygon[(i+1)%4], c=outline[j], d=outline[(j+1)%outline.length];
    assert(!intersects(a,b,c,d), 'Square edge crosses wall');
    wallClearance=Math.min(wallClearance,distance(a,c,d),distance(b,c,d),distance(c,a,b),distance(d,a,b));
  }
}
assert(wallClearance > .65/2, 'Account for the wall stroke thickness');
assert(css.includes('transform:scale(.215)'));
assert(css.includes('width:6.4px;height:6.4px'));
assert(Math.abs(6.4 * .215 / (4 * .172) - 2) < 1e-12, 'Change dot radius exactly doubles');
assert(css.includes('outline-offset:-2px'), 'Focus and selection must not overflow enlarged squares');
assert(css.includes('user-select:none'), 'Background dragging must not select text');

assert(map.includes('台番号マップ'));
assert(map.includes('機種マップ'));
assert(map.includes('FixedFloor.render(data, mapView, palette)'));
assert(!map.includes('machine-seat-grid'));
assert(css.includes('stroke:var(--green)'));
assert(css.includes('--model-saturation:90%'));
assert(css.includes('--model-saturation:18%'));
assert(!floor.includes('data-island=') && !floor.includes('class="island-label"'));
for (const face of shipped.faces) {
  const source = physical.islands.find(island => island.id === face.id);
  const seats = numbers.faces.find(island => island.id === face.id);
  assert.equal(face.label, identities.faces.find(island => island.id === face.id).label);
  assert.equal(face.tiles.length, source.points.length);
  face.tiles.forEach((tile, index) => {
    for (const key of ['x', 'y', 'angle']) assert.equal(tile[key], source.points[index][key]);
    assert.equal(tile.seatNumber, seats.tiles[index].seatNumber);
  });
}

class Element extends EventTarget {
  constructor(zoom) {
    super(); this.dataset = {zoom}; this.disabled = false; this.value = '100'; this.attributes = {};
    this.classes = new Set(); this.writes = 0; this.matrixReads = 0;
    this.classList = {add: c=>this.classes.add(c), remove:c=>this.classes.delete(c), contains:c=>this.classes.has(c), toggle:(c,on)=>on?this.classes.add(c):this.classes.delete(c)};
    this.nodes = [];
  }
  setAttribute(key, value) { this.attributes[key] = value; this.writes++; }
  getAttribute(key) {return this.attributes[key];}
  querySelectorAll() {return this.nodes;}
  closest() { return null; }
  getScreenCTM() { this.matrixReads++; return {inverse: () => ({a:1,b:0,c:0,d:1,e:0,f:0})}; }
  setPointerCapture() {}
}

(async () => {
  const frames = new Map(); let frameId=0;
  const flush = () => {const current=[...frames.values()]; frames.clear(); current.forEach(callback=>callback());};
  const context = vm.createContext({
    fetch: async url => ({ok: true, json: async () => url === '/fixed-floor.json' ? shipped : display}),
    requestAnimationFrame: callback => {frames.set(++frameId, callback); return frameId;},
    cancelAnimationFrame: id => frames.delete(id), setTimeout, clearTimeout,
    AbortController, DOMPoint: class {constructor(x,y) {this.x=x; this.y=y;} matrixTransform() {return this;}},
  });
  vm.runInContext(floor + '\nglobalThis.plan = FixedFloor;', context);
  vm.runInContext(map.replace('return {mount};', 'return {mount, palette};') + '\nglobalThis.palette = JokersMap.palette;', context);
  await context.plan.load();
  assert(context.palette('ﾏｲｼﾞｬｸﾞﾗｰⅤ').includes('juggler-model'));
  assert(context.palette('ジャグラーガールズSS').includes('juggler-model'));
  assert(context.palette('北斗の拳').includes('muted-model'));
  assert(context.palette('').includes('muted-model'));
  let expectedGeometry;
  const physicalSnapshot = JSON.stringify(shipped);
  for (const period of history.periods) {
    const seats = period.seatRanges.flatMap(([start,end,model]) =>
      Array.from({length:end-start+1}, (_,i) => ({seat:String(start+i), model})));
    const html = context.plan.render({period, seats}, 'machine', context.palette);
    const tiles = [...html.matchAll(/<foreignObject[^>]+>/g)].map(match => match[0]);
    assert.equal(tiles.length, 310);
    assert.equal([...html.matchAll(/data-seat="(\d+)"/g)].length, 310);
    const geometry = tiles.join('\n');
    if (!expectedGeometry) expectedGeometry = geometry;
    assert.equal(geometry, expectedGeometry, 'Period switch must not change tile geometry');
    for (const tile of displayTiles) {
      assert(geometry.includes(`x="${tile.x - size/2}" y="${tile.y - size/2}" width="${size}" height="${size}" transform="rotate(${tile.angle} ${tile.x} ${tile.y})" data-face-id="${tile.face}"`));
      const seat = seats.find(seat => Number(seat.seat) === tile.seatNumber);
      const model = seat?.model || '未掲載';
      const escaped = model.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
      assert(html.includes(`data-seat="${tile.seatNumber}" title="${tile.seatNumber}番台 · ${escaped}`));
    }
  }
  const fixture = {period: {changes: []}, seats: [{seat: '1', model: '<script>未確認</script>'}]};
  const escaped = context.plan.render(fixture, 'machine', context.palette);
  assert(!escaped.includes('<script>未確認'));
  assert(escaped.includes('309 台は「未掲載」'));
  assert.equal(JSON.stringify(shipped), physicalSnapshot, 'Render must not mutate reference');
  assert.throws(() => context.plan.validate({...shipped, faces: []}));

  const svg = new Element(), slider = new Element(), output = new Element();
  svg.nodes = displayTiles.map(tile=>{const node=new Element();node.dataset.seatNumber=String(tile.seatNumber);return node;});
  const buttons = ['in','out','reset','selected'].map(zoom => new Element(zoom));
  const root = {
    querySelectorAll: () => buttons,
    querySelector: selector => selector === '.physical-floor-svg' ? svg : selector === '.floor-zoom-slider' ? slider :
      selector === '.floor-zoom-value' ? output : buttons.find(button => selector.includes(`"${button.dataset.zoom}"`)),
  };
  context.plan.bind(root);
  assert.equal(svg.attributes.viewBox, '0 0 1060 665');
  assert(buttons.find(button => button.dataset.zoom === 'out').disabled);
  buttons[0].dispatchEvent(new Event('click'));
  flush();
  assert.equal(output.textContent, '150%');
  const zoomed = context.plan.render(fixture, 'machine', context.palette).match(/viewBox="([^"]+)"/)[1];
  assert.equal(zoomed, svg.attributes.viewBox, 'Camera survives period rerender');
  context.plan.select('24', root);
  buttons[3].dispatchEvent(new Event('click'));
  flush();
  assert.equal(output.textContent, '500%');
  const tile24 = shipped.faces[0].tiles[23];
  const box = svg.attributes.viewBox.split(' ').map(Number);
  assert(tile24.x >= box[0] && tile24.x <= box[0] + box[2]);
  slider.value = '800'; slider.dispatchEvent(new Event('input'));
  flush();
  assert(buttons[0].disabled);
  const paintedCount=svg.nodes.filter(node=>!node.classes.has('outside-viewport')).length;
  assert(paintedCount < 80, 'Zoomed view should paint only nearby tiles');
  const event=(type,properties)=>{const e=new Event(type,{cancelable:true});Object.assign(e,properties);return e;};
  const reads=svg.matrixReads, writes=svg.writes;
  const panStart=svg.attributes.viewBox.split(' ').map(Number);
  const pointerDown=event('pointerdown',{button:0,clientX:100,clientY:100,pointerId:1});
  svg.dispatchEvent(pointerDown);
  assert(pointerDown.defaultPrevented,'Prevent native text selection during pan');
  for(let i=1;i<=100;i++) svg.dispatchEvent(event('pointermove',{clientX:100-i*.1,clientY:100-i*.1,pointerId:1}));
  assert.equal(svg.matrixReads-reads,1,'Only one screen-transform read per drag');
  assert.equal(svg.writes,writes,'No synchronous writes for a burst of pointer events');
  assert.equal(frames.size,1,'100 pan events coalesce into one frame');
  flush();
  assert.equal(svg.writes-writes,1);
  const panEnd=svg.attributes.viewBox.split(' ').map(Number);
  assert(Math.abs(panEnd[0]-panStart[0]-10)<1e-8 && Math.abs(panEnd[1]-panStart[1]-10)<1e-8,'Pan uses the start camera, not accumulating stale deltas');
  svg.dispatchEvent(event('pointerup',{pointerId:1}));
  const wheelReads=svg.matrixReads;
  const normalWheel=event('wheel',{ctrlKey:false,deltaY:10,clientX:300,clientY:300});
  svg.dispatchEvent(normalWheel);
  assert(!normalWheel.defaultPrevented);
  for(let i=0;i<100;i++) svg.dispatchEvent(event('wheel',{ctrlKey:true,deltaY:1,clientX:300,clientY:300}));
  assert.equal(svg.matrixReads-wheelReads,1,'Wheel gesture caches its screen transform');
  assert.equal(frames.size,1,'100 wheel events coalesce into one frame');
  flush();
  assert(Number(output.textContent.replace('%',''))<800);
  buttons[0].dispatchEvent(new Event('click'));
  assert.equal(frames.size,1);
  context.plan.bind(root);
  assert.equal(frames.size,0,'Rerender cancels detached-frame callbacks');
  buttons[2].dispatchEvent(new Event('click'));
  flush();
  assert.equal(svg.attributes.viewBox, '0 0 1060 665');
  assert(svg.nodes.every(node=>!node.classes.has('outside-viewport')),'Reset restores all 310 tiles');
  assert.equal(JSON.stringify(shipped), physicalSnapshot, 'Zoom must not mutate reference');
  console.log(`Fixed map: 310 equal 1.25x squares, 5 outward offsets, no overlaps, wall clearance ${wallClearance.toFixed(2)}; ${history.periods.length} periods, identities and source preserved. 100 pan/wheel events -> 1 frame, ${paintedCount}/310 tiles painted at 800%.`);
})().catch(error => {console.error(error); process.exitCode = 1;});
