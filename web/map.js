'use strict';

// Period assignments are joined by seat number to the immutable Numbered map 1.4 ex.Is-ID.
const JokersMap = (() => {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const day = value => value ? value.split('-').map(Number).join('/') : '未確認';
  const label = period => `${day(period.validFrom)}–${day(period.validToExclusive)}`;
  const normalize = model => (model || '').normalize('NFKC').replace(/\s+/g, '');
  const color = model => {
    let hash = 0;
    for (const c of normalize(model)) hash = (Math.imul(hash, 31) + c.codePointAt(0)) >>> 0;
    return hash % 12;
  };
  const palette = model => `model-color-${color(model)} ${normalize(model).includes('ジャグラー') ? 'juggler-model' : 'muted-model'}`;
  let root, request, data, periodId = '', selectedSeat = '', query = '', changedOnly = false, generation = 0;
  let mapView = 'machine', numberQuery = '';
  const histories = new Map();

  async function mount(element, api) {
    root = element;
    request = api;
    await FixedFloor.load();
    await load(periodId ? {period: periodId} : {});
  }

  async function load(params) {
    const ticket = ++generation, target = root, focusTab = document.activeElement?.classList.contains('period-tab');
    target.setAttribute('aria-busy', 'true');
    try {
      const result = await request('map' + (Object.keys(params).length ? '?' + new URLSearchParams(params) : ''));
      if (ticket !== generation || !target.isConnected || root !== target) return;
      data = result;
      periodId = data.period?.id || '';
      render();
      if (focusTab) root.querySelector('.period-tab.active')?.focus({preventScroll:true});
    } finally {
      if (ticket === generation && target.isConnected) target.removeAttribute('aria-busy');
    }
  }

  const sourceLink = (url, text) => `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(text)} ↗</a>`;

  function boundaryText(p) {
    if (p.boundaryStatus === 'first-observation') return 'この期間の初めての記録。導入日の確定ではありません。';
    if (p.boundaryStatus === 'after-gap') {
      return p.changeWindow
        ? `前回確認 ${day(p.previousObservedDate)} → 初確認 ${day(p.validFrom)}。この間に変わった配置です。変更日は未確定です。`
        : `ログの欠落後、${day(p.validFrom)} に同じ配置を再確認しました。`;
    }
    return `${day(p.previousObservedDate)} と ${day(p.validFrom)} の全台表を照合した機種割当の変更日です。筐体そのものの移動は未同定です。`;
  }

  function render() {
    const p = data.period, coverage = data.coverage;
    root.classList.toggle('number-map-view', mapView === 'numbers');
    document.querySelector('#map-view-title').textContent = mapView === 'numbers' ? '台番号マップ' : '機種マップ';
    const index = data.periods.findIndex(x => x.id === p?.id);
    const displayDate = data.requestedDate || p?.validFrom || '';
    root.innerHTML = `
      <div class="map-view-switch" role="group" aria-label="マップ表示切替"><button type="button" data-map-view="numbers" aria-pressed="${mapView === 'numbers'}">台番号マップ</button><button type="button" data-map-view="machine" aria-pressed="${mapView === 'machine'}">機種マップ</button></div>
      <div class="history-coverage"><span>${day(coverage.first)} → ${day(coverage.last)} の記録</span><strong>${coverage.reportCount.toLocaleString('ja-JP')} 日 / ${coverage.periodCount} 期間</strong></div>
      <div class="period-controls">
        <label class="period-picker"><span class="sr-only">マップの期間</span><select id="map-period"><option value="" ${p ? 'hidden' : 'selected'}>期間を選択</option>${data.periods.map(v => `<option value="${esc(v.id)}" ${v.id === p?.id ? 'selected' : ''}>${label(v)}</option>`).join('')}</select><span class="picker-arrow" aria-hidden="true">▽</span></label>
        <button class="period-step" id="period-prev" ${index <= 0 ? 'disabled' : ''} aria-label="前の配置期間">‹</button>
        <button class="period-step" id="period-next" ${index < 0 || index >= data.periods.length - 1 ? 'disabled' : ''} aria-label="次の配置期間">›</button>
        <label class="day-picker">日付から探す <input id="map-date" type="date" value="${esc(displayDate)}" aria-label="確認したい日付"></label>
      </div>
      <div class="period-tabs" role="tablist" aria-label="配置が変わった日で区切った期間">${data.periods.map(v => `<button type="button" class="period-tab ${v.id === p?.id ? 'active' : ''}" role="tab" aria-selected="${v.id === p?.id}" tabindex="${v.id === p?.id ? '0' : '-1'}" data-period="${esc(v.id)}"><span>${label(v)}</span><small>${v.boundaryStatus === 'daily-confirmed' ? `${v.changeCount} 台の配置変更` : v.boundaryStatus === 'first-observation' ? '基準配置' : '変更日未確定・初確認日'}</small></button>`).join('')}</div>
      <p class="period-caption">終了日は含みません。配置変更の前後のログと告知から期間を設定しています。期間内でも記録のない日を選ぶと、当日のマップは「未確認」に切り替わります。</p>
      ${p ? activeMap(p) : `<div class="empty"><div class="symbol">◷</div><h2>${day(data.requestedDate)} の配置は未確認です</h2><p>この日付の台番―機種の対応を確認できるログがありません。上の期間から確認済みのマップを選択できます。</p></div>`}
      <details class="original-floor"><summary>公開フロア図について · ${day(data.metadata.mapImageDate)} ごろ（推定）</summary><p>店舗の公開フロア図は同梱していません。${sourceLink(data.metadata.mapPageUrl, 'DMMぱちタウンの掲載ページ')}で確認できます。${esc(data.metadata.mapImageDateBasis)}</p><p>この公開図は選択した過去期間の配置を証明するものではありません。上のマップには現地確認済みの固定台番号図を使用しています。</p></details>
      ${coverage.gaps.length || coverage.missingReports ? `<details class="coverage-details"><summary>記録がない期間・未取得ログについて</summary><p>全台表を取得できていない公開ログ：${coverage.missingReports} 日。公開一覧自体に記録がない日も含め、欠落区間を跨ぐ配置継続は確定していません。</p><div class="coverage-gaps">${coverage.gaps.map(g => `<span>${day(g.from)}–${day(g.toExclusive)}（終了日を除く）</span>`).join('')}</div></details>` : ''}`;

    root.querySelectorAll('[data-map-view]').forEach(button => {
      button.addEventListener('click', () => switchView(button.dataset.mapView));
      button.addEventListener('keydown', e => {
        if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
          e.preventDefault();
          switchView(e.key === 'ArrowLeft' ? 'numbers' : 'machine');
        }
      });
    });
    root.querySelector('#map-period').addEventListener('change', e => { if (e.target.value) switchPeriod(e.target.value); });
    root.querySelector('#period-prev').addEventListener('click', () => switchPeriod(data.periods[index - 1].id));
    root.querySelector('#period-next').addEventListener('click', () => switchPeriod(data.periods[index + 1].id));
    root.querySelector('#map-date').addEventListener('change', e => { if (e.target.value) load({date: e.target.value}).catch(showFailure); });
    const tabs = [...root.querySelectorAll('[data-period]')];
    tabs.forEach((tab, i) => {
      tab.addEventListener('click', () => switchPeriod(tab.dataset.period));
      tab.addEventListener('keydown', e => {
        const next = e.key === 'ArrowRight' ? tabs[i + 1] : e.key === 'ArrowLeft' ? tabs[i - 1] : e.key === 'Home' ? tabs[0] : e.key === 'End' ? tabs.at(-1) : null;
        if (next) { e.preventDefault(); next.focus(); switchPeriod(next.dataset.period); }
      });
    });
    const active = root.querySelector('.period-tab.active');
    if (active) {
      const strip = root.querySelector('.period-tabs');
      strip.scrollLeft = Math.max(0, active.offsetLeft - strip.offsetLeft - strip.clientWidth / 2 + active.clientWidth / 2);
    }
    if (!p) return;
    const search = root.querySelector('#map-search');
    search.value = mapView === 'numbers' ? numberQuery : query;
    search.placeholder = mapView === 'numbers' ? '例：152' : '例：152 / 北斗';
    root.querySelector('label[for="map-search"]').textContent = mapView === 'numbers' ? '台番号' : '台番号・機種';
    search.addEventListener('input', e => {
      if (mapView === 'numbers') numberQuery = e.target.value;
      else query = e.target.value;
      applyFilter();
    });
    root.querySelector('#map-changed-only').addEventListener('change', e => { changedOnly = e.target.checked; applyFilter(); });
    root.querySelectorAll('[data-seat]').forEach(button => button.addEventListener('click', () => selectSeat(button.dataset.seat)));
    if (mapView === 'numbers') {
      root.querySelector('.period-summary .kicker').textContent = 'SEAT NUMBER MAP';
      root.querySelector('.map-view-note strong').textContent = '台番号マップ（固定配置）';
      root.querySelector('.physical-floor-svg').setAttribute('aria-label', '固定配置の台番号マップ。全310台。');
      root.querySelector('.machine-map-panel > .hint').textContent = '番号をクリックして選択できます。機種マップへ切り替えても、選択した番号と期間はそのままです。';
      root.querySelectorAll('.machine-seat').forEach(button => {
        const name = button.dataset.seat + '番台';
        button.title = name;
        button.setAttribute('aria-label', name);
      });
    }
    root.querySelectorAll('[data-model]').forEach(button => button.addEventListener('click', () => {
      query = button.dataset.model;
      root.querySelector('#map-search').value = query;
      applyFilter();
    }));
    FixedFloor.bind(root);
    applyFilter();
    if (selectedSeat) selectSeat(selectedSeat);
  }

  function activeMap(p) {
    const changed = new Set(p.changes.map(c => c.seat));
    const models = new Map();
    for (const s of data.seats) models.set(s.model, (models.get(s.model) || 0) + 1);
    return `<div class="period-summary"><div><div class="kicker">MACHINE MAP</div><h3>${label(p)}</h3><p>${boundaryText(p)}</p>${p.gaps?.length ? `<p class="hint">この期間には ${p.gaps.length} 区間の未確認日があります。日付から選択すると当日の確認状況を表示します。</p>` : ''}</div><span class="tag">${p.seatCount} 台 · ${p.reportCount} 日分を照合</span></div>
      <div class="map-evidence">${p.previousReportUrl ? sourceLink(p.previousReportUrl + '?kishu=all', `変更前 ${day(p.previousObservedDate)}`) : ''}${sourceLink(p.reportUrl + '?kishu=all', `期間開始 ${day(p.validFrom)}`)}${p.observedThrough !== p.validFrom ? sourceLink(p.lastReportUrl + '?kishu=all', `最終確認 ${day(p.observedThrough)}`) : ''}${p.announcements.map(a => sourceLink(a.url, `${a.matchKind === 'same-day' || !a.matchKind ? '同日の告知' : '変更候補区間の告知'}：${a.title}`)).join('')}</div>
      ${p.labelNotes?.length ? `<details class="coverage-details"><summary>掲載名の表記差・推定を含む台があります（${new Set(p.labelNotes.map(n => n.seat)).size} 台）</summary><p>下の機種名は比較用に統合した表示です。表記の揺れだけで台移動の期間を増やしていません。「推定」は機種同定の確定ではありません。</p>${p.labelNotes.map(n => `<p>${esc(n.seat)}番：${esc(n.rawModel)} → ${esc(n.model)} · ${n.status === 'inferred-name-variant' ? '推定' : '表記差'}（${n.days.length}日）<br><small>${esc(n.basis)}</small> ${sourceLink(n.reportUrl + '?num=' + n.seat, '元の掲載名')} ${sourceLink(n.evidenceUrl, '機種資料')}</p>`).join('')}</details>` : ''}
      <div class="map-toolbar toolbar"><label for="map-search">台番号・機種</label><input id="map-search" type="search" value="${esc(query)}" placeholder="例：152 / 北斗"><label class="changed-toggle"><input id="map-changed-only" type="checkbox" ${changedOnly ? 'checked' : ''}>変わった台だけ強調</label><span id="map-filter-count" class="hint"></span></div>
      <div class="map-layout"><div class="machine-map-panel"><div class="map-view-note"><strong>固定配置の機種マップ</strong><span>Numbered map 1.4 ex.Is-ID · 全310台</span></div>${FixedFloor.render(data, mapView, palette)}<p class="hint">枠のマークは前回の記録から機種が変わった台です。番号をクリックすると設置履歴を表示します。</p></div><section id="map-seat-detail" class="seat-detail" aria-label="選択台の設置履歴" aria-live="polite"><div class="seat-detail-empty"><div>◈</div><h3>台番号を選択</h3><p>当時の機種と、この台番号に入った機種の変遷がここに表示されます。</p></div></section></div>
      <details class="machine-legend"><summary>この期間の機種一覧 · ${models.size} 機種</summary><div class="machine-model-list">${[...models].sort((a,b)=>b[1]-a[1]).map(([model,count]) => `<button type="button" class="${palette(model)}" data-model="${esc(model)}"><span>${esc(model)}</span><strong>${count}台</strong></button>`).join('')}</div></details>
      ${p.changes.length ? `<details class="map-changes"><summary>${p.boundaryStatus === 'daily-confirmed' ? day(p.validFrom) + ' の' : '前回確認からの'}配置変更 · ${p.changes.length} 台</summary><div class="change-counts">${p.modelCountChanges.map(c => `<span>${esc(c.model)} <strong>${c.before} → ${c.after}台</strong></span>`).join('') || '<p>機種別の台数は同じで、台番号への割当が変わっています。</p>'}</div><div class="table-scroll"><table><thead><tr><th>台番号</th><th>以前の機種</th><th>この期間の機種</th></tr></thead><tbody>${p.changes.map(c => `<tr><td><button class="change-seat" data-seat="${esc(c.seat)}">${esc(c.seat)}</button></td><td>${esc(c.before || '未掲載')}</td><td>${esc(c.after || '未掲載')}</td></tr>`).join('')}</tbody></table></div><p class="hint">同じ機種の個体識別は公開表にありません。台番号への機種割当の変更を表示しています。</p></details>` : ''}`;
  }

  function applyFilter() {
    const q = normalize(mapView === 'numbers' ? numberQuery : query).toLocaleLowerCase(), number = /^\d+$/.test(q) ? String(Number(q)) : null;
    const changed = new Set(data.period.changes.map(c => c.seat));
    let count = 0;
    const seats = new Map(data.seats.map(s => [s.seat, s]));
    root.querySelectorAll('.machine-seat').forEach(button => {
      const seat = seats.get(button.dataset.seat) || {seat: button.dataset.seat, model: ''};
      const match = (!q || (number ? seat.seat === number : mapView === 'machine' && normalize(seat.model).toLocaleLowerCase().includes(q))) && (mapView === 'numbers' || !changedOnly || changed.has(seat.seat));
      button.classList.toggle('dimmed', !match);
      if (match) count++;
    });
    root.querySelector('#map-filter-count').textContent = count ? `${count} 台を強調` : '該当なし';
  }

  function showFailure(error) {
    if (root?.isConnected) {
      const old = root.querySelector('.map-load-error');
      if (old) old.remove();
      const message = document.createElement('p');
      message.className = 'map-load-error';
      message.setAttribute('role','alert');
      message.textContent = error.message || String(error);
      root.prepend(message);
    }
  }

  function switchPeriod(id) { load({period:id}).catch(showFailure); }

  function switchView(view) {
    mapView = view;
    render();
    root.querySelector(`[data-map-view="${view}"]`).focus({preventScroll:true});
  }

  async function selectSeat(number) {
    selectedSeat = number;
    FixedFloor.select(number, root);
    root.querySelectorAll('.machine-seat').forEach(b => {
      b.classList.toggle('selected', b.dataset.seat === number);
      b.setAttribute('aria-pressed', String(b.dataset.seat === number));
    });
    const target = root.querySelector('#map-seat-detail');
    const s = data.seats.find(s => s.seat === number), current = data.period;
    if (mapView === 'numbers') {
      target.innerHTML = `<div class="kicker">SEAT NUMBER</div><h3>${esc(number)}番台</h3><p>${label(current)}</p><p class="hint">現地確認済みの固定位置です。台番号マップと機種マップで同じ位置を共有しています。</p><button type="button" class="secondary" id="view-seat-model">この台の機種・設置履歴を見る →</button>`;
      target.querySelector('#view-seat-model').addEventListener('click', () => switchView('machine'));
      return;
    }
    const stamp = generation;
    target.innerHTML = `<div class="kicker">SEAT HISTORY</div><h3>${esc(number)}番台</h3><div class="seat-current ${palette(s?.model)}"><strong>${esc(s?.model || 'この期間は未掲載')}</strong><span>${label(current)}</span></div><p>設置履歴を読み込み中…</p>`;
    try {
      if (!histories.has(number)) histories.set(number, request('map/seat-history?seat=' + encodeURIComponent(number)).catch(error => { histories.delete(number); throw error; }));
      const result = await histories.get(number);
      if (stamp !== generation || selectedSeat !== number || !target.isConnected) return;
      target.innerHTML = `<div class="kicker">SEAT HISTORY</div><h3>${esc(number)}番台</h3><div class="seat-current ${palette(s?.model)}"><strong>${esc(s?.model || 'この期間は未掲載')}</strong><span>${label(current)}</span></div>${s ? sourceLink(s.source, 'この期間の台番表') : ''}${s?.labelNotes?.length ? `<p class="hint">掲載名の統合を含みます：${s.labelNotes.map(n => `${esc(n.rawModel)}（${n.status === 'inferred-name-variant' ? '同定は推定' : '表記差'}）`).join('、')}</p>` : ''}<h4>この台番号の機種の変遷</h4><div class="seat-timeline">${[...result.entries].reverse().map(e => `<article class="seat-history-entry ${e.validFrom <= current.validFrom && current.validFrom < e.validToExclusive ? 'current' : ''}"><button type="button" data-jump="${esc(e.periodId)}">${label(e)}</button><strong>${esc(e.model || '未掲載')}</strong><small>${e.reportCount}日確認${e.boundaryStatus === 'after-gap' ? ' · 前にログ欠落あり' : ''}${e.gaps?.length ? ' · 期間内に未確認日あり' : ''}${e.labelNotes?.some(n => n.status === 'inferred-name-variant') ? ' · 掲載名の同定に推定あり' : ''}</small>${sourceLink(e.reportUrl, '根拠')}</article>`).join('')}</div>`;
      target.querySelectorAll('[data-jump]').forEach(button => button.addEventListener('click', () => switchPeriod(button.dataset.jump)));
    } catch (error) {
      if (stamp === generation && selectedSeat === number && target.isConnected) {
        const message = document.createElement('p');
        message.textContent = error.message || String(error);
        target.append(message);
      }
    }
  }
  return {mount};
})();
