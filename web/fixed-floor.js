'use strict';

// Preserve the source. Only the explicitly authorized display offsets enlarge clearance.
const FixedFloor = (() => {
  const WIDTH = 1060, HEIGHT = 665, TILE = 10.75, MAX_ZOOM = 8;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let reference, pending, positions, byNumber, wallMarkup;
  let viewport = [0, 0, WIDTH, HEIGHT], selected = '';
  let cleanup = () => {};

  function validate(value) {
    const seats = value.faces?.flatMap(face => face.tiles.map(tile => tile.seatNumber));
    if (value.name !== 'Numbered map 1.4 ex.Is-ID' || value.faces?.length !== 32 ||
        seats?.length !== 310 || new Set(seats).size !== 310 ||
        seats.some(seat => !Number.isInteger(seat) || seat < 1 || seat > 310) ||
        !value.wallGeometry?.outline?.length || value.islandLabelsDisplayed !== false) {
      throw new Error('固定マップの台番号・配置データを検証できません。');
    }
    for (const face of value.faces) {
      if (face.count !== face.tiles.length || face.tiles.some(tile =>
        ![tile.x, tile.y, tile.angle].every(Number.isFinite))) {
        throw new Error('固定マップの島構成・座標データが不正です。');
      }
    }
    return value;
  }

  async function load() {
    if (!pending) pending = Promise.all(['/fixed-floor.json', '/fixed-floor-display.json'].map(async url => {
      const response = await fetch(url);
      if (!response.ok) throw new Error('固定マップを読み込めませんでした。');
      return response.json();
    })).then(([value, display]) => {
      const source = validate(value);
      if (display.source !== source.name || display.baseTileSize !== 8.6 || display.tileScale !== 1.25 ||
          display.centerMethod !== 'mean-of-island-tile-centers' || !Array.isArray(display.radialOutward)) {
        throw new Error('マップの表示調整データが不正です。');
      }
      const offsets = new Map(display.radialOutward.map(offset => [offset.seatNumber, offset]));
      if (offsets.size !== display.radialOutward.length) throw new Error('表示調整の台番号が重複しています。');
      const displayPositions = source.faces.flatMap(face => {
        const center = face.tiles.reduce((sum, tile) => ({x: sum.x + tile.x / face.count, y: sum.y + tile.y / face.count}), {x: 0, y: 0});
        return face.tiles.map(tile => {
          const offset = offsets.get(tile.seatNumber);
          let x = tile.x, y = tile.y;
          if (offset) {
            const dx = x - center.x, dy = y - center.y, distance = Math.hypot(dx, dy);
            if (offset.faceId !== face.id || !Number.isFinite(offset.distance) || offset.distance < 0 || !distance) {
              throw new Error('島の外向き表示調整を検証できません。');
            }
            x += dx / distance * offset.distance;
            y += dy / distance * offset.distance;
            offsets.delete(tile.seatNumber);
          }
          const angle = tile.angle * Math.PI / 180;
          const radius = TILE / 2 * (Math.abs(Math.cos(angle)) + Math.abs(Math.sin(angle)));
          return {...tile, x, y, face: face.id, radius};
        });
      });
      if (offsets.size) throw new Error('表示調整に未登録の台番号があります。');
      reference = source;
      positions = displayPositions;
      byNumber = new Map(positions.map(tile => [String(tile.seatNumber), tile]));
      wallMarkup = source.wallGeometry.outline.map(point => point.join(',')).join(' ');
    }).catch(error => { pending = null; throw error; });
    return pending;
  }

  function render(data, view, palette) {
    if (!reference) throw new Error('先に固定マップを読み込んでください。');
    const seats = new Map(data.seats.map(seat => [String(seat.seat), seat]));
    const changed = new Set(data.period.changes.map(change => String(change.seat)));
    const unknown = positions.filter(tile => !seats.get(String(tile.seatNumber))?.model).length;
    const outside = data.seats.filter(seat => !byNumber.has(String(seat.seat)));
    return `<div class="physical-plan">
      <div class="floor-tools" role="group" aria-label="マップの拡大縮小">
        <button type="button" data-zoom="out" aria-label="マップを縮小">−</button>
        <output class="floor-zoom-value" aria-live="polite">${Math.round(WIDTH / viewport[2] * 100)}%</output>
        <button type="button" data-zoom="in" aria-label="マップを拡大">＋</button>
        <input class="floor-zoom-slider" type="range" min="100" max="800" step="10" value="${Math.round(WIDTH / viewport[2] * 100)}" aria-label="マップ拡大率">
        <button type="button" data-zoom="reset">全体表示</button>
        <button type="button" data-zoom="selected" ${selected ? '' : 'disabled'}>選択台へ</button>
        <span class="hint">ドラッグで移動 · Ctrl＋ホイールで拡大</span>
      </div>
      <svg class="physical-floor-svg" xmlns="http://www.w3.org/2000/svg" viewBox="${viewport.join(' ')}" role="group" tabindex="0" aria-label="店内マップ。全310台の物理位置と台番号は固定。${view === 'numbers' ? '台番号' : '選択期間の機種'}を表示。">
        <polygon class="floor-wall" points="${wallMarkup}"/>
        ${positions.map(tile => {
          const number = String(tile.seatNumber), seat = seats.get(number), model = seat?.model || '未掲載';
          const title = `${number}番台${view === 'machine' ? ' · ' + model : ''}${changed.has(number) && view === 'machine' ? ' · この期間の開始時に機種変更' : ''}`;
          return `<foreignObject class="floor-tile" x="${tile.x - TILE / 2}" y="${tile.y - TILE / 2}" width="${TILE}" height="${TILE}" transform="rotate(${tile.angle} ${tile.x} ${tile.y})" data-face-id="${tile.face}" data-seat-number="${tile.seatNumber}">
            <button xmlns="http://www.w3.org/1999/xhtml" type="button" class="machine-seat ${palette(model)} ${changed.has(number) ? 'seat-changed' : ''}" data-seat="${number}" title="${esc(title)}" aria-label="${esc(title)}"><span class="seat-number">${number}</span><span class="seat-model">${esc(model.replace(/^(スマスロ\s*|パチスロ\s*|Lパチスロ\s*|Lスマスロ\s*)/, ''))}</span></button>
          </foreignObject>`;
        }).join('')}
      </svg>
      <div class="floor-key"><span>高彩度：ジャグラー系統</span><span>低彩度：その他の機種</span><span>位置・台番号は固定／Is-ID非表示</span></div>
      <p class="hint">Numbered map 1.4 ex.Is-ID に選択期間の機種を対応づけています。四角は一律1.25倍、重なりを避ける5台だけ島の中心から外向きに表示調整しています（正本は未変更）。${unknown ? `この期間の記録にない ${unknown} 台は「未掲載」です。` : ''}過去の壁・島形状の再現ではありません。機種名を読むときは拡大してください。</p>
      ${outside.length ? `<p class="hint">固定図の1〜310番に含まれない記録：${outside.map(seat => esc(seat.seat)).join('、')}番。図に推測で位置を追加していません。</p>` : ''}
    </div>`;
  }

  function bind(root) {
    cleanup();
    const svg = root.querySelector('.physical-floor-svg');
    if (!svg) return;
    const controller = new AbortController(), signal = controller.signal;
    let drag, frame = 0, wheelMap, wheelTimer, lastZoom, paintedBox = [...viewport];
    cleanup = () => {
      controller.abort();
      if (frame) cancelAnimationFrame(frame);
      clearTimeout(wheelTimer);
    };
    const buttons = [...root.querySelectorAll('[data-zoom]')];
    const slider = root.querySelector('.floor-zoom-slider');
    const output = root.querySelector('.floor-zoom-value');
    const zoomOut = buttons.find(button => button.dataset.zoom === 'out');
    const zoomIn = buttons.find(button => button.dataset.zoom === 'in');
    const tiles = [...svg.querySelectorAll('.floor-tile')].map(node => ({
      node, tile: byNumber.get(node.dataset.seatNumber), visible: !node.classList.contains('outside-viewport'),
    }));
    const clamp = () => {
      viewport[0] = Math.max(0, Math.min(WIDTH - viewport[2], viewport[0]));
      viewport[1] = Math.max(0, Math.min(HEIGHT - viewport[3], viewport[1]));
    };
    const redraw = () => {
      frame = 0;
      const box = viewport.join(' ');
      if (svg.getAttribute('viewBox') !== box) svg.setAttribute('viewBox', box);
      paintedBox = [...viewport];
      const zoom = WIDTH / viewport[2];
      const percent = Math.round(zoom * 100);
      if (percent !== lastZoom) {
        output.textContent = `${percent}%`;
        slider.value = percent;
        lastZoom = percent;
      }
      const atMinimum = zoom <= 1 + 1e-9, atMaximum = zoom >= MAX_ZOOM - 1e-9;
      if (zoomOut.disabled !== atMinimum) zoomOut.disabled = atMinimum;
      if (zoomIn.disabled !== atMaximum) zoomIn.disabled = atMaximum;
      // Avoid painting off-camera HTML tiles, without rebuilding the 310 seats.
      for (const item of tiles) {
        const {tile, node} = item, margin = tile.radius + TILE;
        const visible = tile.x + margin >= viewport[0] && tile.x - margin <= viewport[0] + viewport[2] &&
          tile.y + margin >= viewport[1] && tile.y - margin <= viewport[1] + viewport[3];
        if (visible !== item.visible) {
          node.classList.toggle('outside-viewport', !visible);
          item.visible = visible;
        }
      }
    };
    const schedule = () => {
      clamp();
      if (!frame) frame = requestAnimationFrame(redraw);
    };
    const clearWheel = () => { wheelMap = null; clearTimeout(wheelTimer); };
    const zoomTo = (zoom, fractionX = .5, fractionY = .5) => {
      zoom = Math.max(1, Math.min(MAX_ZOOM, zoom));
      const anchor = {x: viewport[0] + viewport[2] * fractionX, y: viewport[1] + viewport[3] * fractionY};
      const width = WIDTH / zoom, height = HEIGHT / zoom;
      viewport = [anchor.x - width * fractionX, anchor.y - height * fractionY, width, height];
      schedule();
    };
    const locate = (number, zoom = WIDTH / viewport[2]) => {
      const tile = byNumber.get(number);
      if (!tile) return;
      viewport = [tile.x - WIDTH / zoom / 2, tile.y - HEIGHT / zoom / 2, WIDTH / zoom, HEIGHT / zoom];
      schedule();
    };
    for (const button of buttons) button.addEventListener('click', () => {
      clearWheel();
      if (button.dataset.zoom === 'reset') { viewport = [0, 0, WIDTH, HEIGHT]; schedule(); }
      else if (button.dataset.zoom === 'selected') locate(selected, Math.max(5, WIDTH / viewport[2]));
      else zoomTo(WIDTH / viewport[2] * (button.dataset.zoom === 'in' ? 1.5 : 1 / 1.5));
    }, {signal});
    slider.addEventListener('input', () => { clearWheel(); zoomTo(Number(slider.value) / 100); }, {signal});
    svg.addEventListener('wheel', event => {
      if (!event.ctrlKey) return;
      event.preventDefault();
      if (drag) return;
      if (!wheelMap) {
        const matrix = svg.getScreenCTM();
        if (!matrix) return;
        wheelMap = {inverse: matrix.inverse(), box: [...paintedBox]};
      }
      const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(wheelMap.inverse);
      const fractionX = (point.x - wheelMap.box[0]) / wheelMap.box[2];
      const fractionY = (point.y - wheelMap.box[1]) / wheelMap.box[3];
      const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? 400 : 1);
      zoomTo(WIDTH / viewport[2] * Math.exp(-Math.max(-400, Math.min(400, delta)) * .0012), fractionX, fractionY);
      clearTimeout(wheelTimer);
      wheelTimer = setTimeout(clearWheel, 180);
    }, {passive: false, signal});
    svg.addEventListener('pointerdown', event => {
      if (drag || event.button !== 0 || event.target.closest('[data-seat]')) return;
      const matrix = svg.getScreenCTM();
      if (!matrix) return;
      event.preventDefault();
      clearWheel();
      drag = {x: event.clientX, y: event.clientY, box: [...viewport], inverse: matrix.inverse(), pointerId: event.pointerId};
      svg.setPointerCapture(event.pointerId);
      svg.classList.add('panning');
    }, {signal});
    svg.addEventListener('pointermove', event => {
      if (!drag || event.pointerId !== drag.pointerId) return;
      const dx = event.clientX - drag.x, dy = event.clientY - drag.y, matrix = drag.inverse;
      viewport[0] = drag.box[0] - dx * matrix.a - dy * matrix.c;
      viewport[1] = drag.box[1] - dx * matrix.b - dy * matrix.d;
      schedule();
    }, {signal});
    const stop = event => {
      if (drag && event.pointerId !== drag.pointerId) return;
      drag = null; svg.classList.remove('panning');
    };
    svg.addEventListener('pointerup', stop, {signal});
    svg.addEventListener('pointercancel', stop, {signal});
    svg.addEventListener('lostpointercapture', stop, {signal});
    svg.addEventListener('focusin', event => {
      const number = event.target.closest('[data-seat]')?.dataset.seat;
      const tile = byNumber.get(number);
      if (tile && (tile.x < viewport[0] + TILE || tile.x > viewport[0] + viewport[2] - TILE ||
          tile.y < viewport[1] + TILE || tile.y > viewport[1] + viewport[3] - TILE)) locate(number);
    }, {signal});
    svg.addEventListener('keydown', event => {
      if (event.target !== svg) return;
      clearWheel();
      if (event.key === '+' || event.key === '=' || event.key === '-') {
        event.preventDefault(); zoomTo(WIDTH / viewport[2] * (event.key === '-' ? 1 / 1.5 : 1.5));
      } else if (event.key === '0') { viewport = [0, 0, WIDTH, HEIGHT]; schedule(); }
    }, {signal});
    globalThis.addEventListener?.('resize', clearWheel, {signal});
    clamp();
    redraw();
  }

  function select(number, root) {
    selected = number;
    const button = root.querySelector('[data-zoom="selected"]');
    if (button) button.disabled = !number;
  }

  return {load, render, bind, select, validate};
})();
