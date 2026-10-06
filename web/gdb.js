'use strict';

/* GothamDataBase (GDB): the 11-column view of the seat records stored in GothamDataBase.sqlite.
   Scraping is started explicitly; "不足日を取得" sends only dates that have no record yet and
   "期間を指定して取得" only the dates of the chosen range that still lack a record or BB/RB. */
(() => {
  const headers = ['日付', '曜日', '台番号', '機種', 'ジャグラーかジャグラーじゃないか', 'ゲーム数', 'BB数', 'RB数', '合成', '差枚', '出率'];
  let mounted = false;
  let offset = 0;
  const limit = 100;
  let activeMode = '';
  let pollTimer = null;

  const escLocal = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const display = value => value === '' || value === null || value === undefined ? '未取得' : value;
  const count = value => Number(value || 0).toLocaleString('ja-JP');

  function shell() {
    if (document.querySelector('#gdb-card')) return document.querySelector('#gdb-app');
    const observations = document.querySelector('#observations');
    if (!observations) return null;
    const card = document.createElement('section');
    card.className = 'card';
    card.id = 'gdb-card';
    card.innerHTML = '<div id="gdb-app"><p>GDBを読み込み中…</p></div>';
    observations.closest('.card')?.after(card);
    return card.querySelector('#gdb-app');
  }

  function render(app) {
    app.innerHTML = `
      <div class="card-header"><div><div class="kicker">MIN-REPO / GOTHAM DATABASE</div><h2>GothamDataBase（GDB）</h2></div><span class="tag" id="gdb-tag">0行</span></div>
      <p>みんレポ ゴッサムシティの台別記録を、日付・台番号ごとに1行で保存して閲覧します（2024/03/01以降）。空欄は公開ページで取得できなかった値です。</p>
      <div class="gdb-actions"><button class="primary" id="gdb-update">不足日を取得</button><button class="secondary" id="gdb-bonuses">BB・RBの不足だけ補完</button><button class="secondary" id="gdb-stop" disabled>補完・取得を停止</button></div>
      <div class="toolbar gdb-range"><label>開始日<input id="range-from" type="date" min="2024-03-01"></label><label>終了日<input id="range-to" type="date" min="2024-03-01"></label><button class="secondary" id="gdb-range">期間を指定して取得</button></div>
      <p class="hint">期間内で、記録がない日は全台表とBB・RBを取得し、BB・RBが空の日はそこだけ補完します。揃っている日・機種は再取得しません。まず1週間ほどで試してから広げると、取り方の問題に早く気づけます。</p>
      <details><summary>BB・RB補完の対象期間（空欄なら保存済みの全期間）</summary><div class="toolbar"><label>開始日<input id="bonus-from" type="date" min="2024-03-01"></label><label>終了日<input id="bonus-to" type="date" min="2024-03-01"></label></div><p class="hint">保存済みの日付・台のうちBBまたはRBが空欄のものだけ対象にします。取得済み機種は再巡回しません。全台表の数値は再取得せず、機種別・個別台の当日表で補完します。多日数の補完には時間がかかります。</p></details>
      <p class="hint" id="gdb-status">状態を確認中…</p>
      <div class="gdb-filters">
        <div><label class="label" for="gdb-from">日付（開始）</label><input id="gdb-from" type="date"></div>
        <div><label class="label" for="gdb-to">日付（終了）</label><input id="gdb-to" type="date"></div>
        <div><label class="label" for="gdb-month">月</label><input id="gdb-month" type="month"></div>
        <div><label class="label" for="gdb-weekday">曜日</label><select id="gdb-weekday"><option value="">すべて</option><option value="0">月曜日</option><option value="1">火曜日</option><option value="2">水曜日</option><option value="3">木曜日</option><option value="4">金曜日</option><option value="5">土曜日</option><option value="6">日曜日</option></select></div>
        <div><label class="label" for="gdb-model">機種（部分一致）</label><input id="gdb-model" type="text" placeholder="例：マイジャグラー"></div>
        <div><label class="label" for="gdb-juggler">系統</label><select id="gdb-juggler"><option value="all">すべて</option><option value="juggler">ジャグラーのみ</option><option value="non-juggler">ジャグラー以外</option></select></div>
        <div><label class="label" for="gdb-games">ゲーム数 以上</label><input id="gdb-games" type="number" min="0" placeholder="例：5000"></div>
        <div><label class="label" for="gdb-net">差枚 以上</label><input id="gdb-net" type="number" placeholder="例：1000"></div>
        <div><label class="label" for="gdb-combined">合成 1/n 以下（n）</label><input id="gdb-combined" type="number" min="1" placeholder="例：140"></div>
        <div><label class="label" for="gdb-payout">出率 以上（%）</label><input id="gdb-payout" type="number" step="0.1" placeholder="例：105"></div>
      </div>
      <div class="actions gdb-filter-actions"><button class="primary" id="gdb-apply">条件を適用</button><button class="secondary" id="gdb-reset">条件をリセット</button></div>
      <div class="gdb-result-head"><span id="gdb-count">—</span><span class="hint">表示件数は最大100件</span></div>
      <div id="gdb-results"><p class="muted">読み込み中…</p></div>
      <div class="gdb-pagination"><button class="secondary" id="gdb-prev">← 前へ</button><span id="gdb-page">1</span><button class="secondary" id="gdb-next">次へ →</button></div>`;
    app.querySelector('#gdb-update').addEventListener('click', () => runAction('update'));
    app.querySelector('#gdb-bonuses').addEventListener('click', () => runAction('bonuses'));
    app.querySelector('#gdb-range').addEventListener('click', () => runAction('range'));
    const today = new Date(Date.now() + 9 * 3600 * 1000).toISOString().slice(0, 10);  // Japan time, as the server
    app.querySelectorAll('#range-from,#range-to,#bonus-from,#bonus-to').forEach(input => { input.max = today; });
    app.querySelector('#gdb-stop').addEventListener('click', async () => { try { await api('scrape/stop', {}); } catch (error) { showError(error); } });
    app.querySelector('#gdb-apply').addEventListener('click', () => { offset = 0; loadRows().catch(showError); });
    app.querySelector('#gdb-reset').addEventListener('click', () => { app.querySelectorAll('.gdb-filters input').forEach(input => { input.value = ''; }); app.querySelector('#gdb-juggler').value = 'all'; app.querySelector('#gdb-weekday').value = ''; offset = 0; loadRows().catch(showError); });
    app.querySelector('#gdb-prev').addEventListener('click', () => { offset = Math.max(0, offset - limit); loadRows().catch(showError); });
    app.querySelector('#gdb-next').addEventListener('click', () => { offset += limit; loadRows().catch(showError); });
    loadStatus().then(loadRows).catch(showError);
    if (activeMode) setBusy(true);
  }

  function params() {
    const root = document.querySelector('#gdb-app');
    const query = new URLSearchParams();
    [['from','gdb-from'],['to','gdb-to'],['month','gdb-month'],['weekday','gdb-weekday'],['model','gdb-model'],['juggler','gdb-juggler'],['games_min','gdb-games'],['net_min','gdb-net'],['combined_n_min','gdb-combined'],['payout_min','gdb-payout']].forEach(([key, id]) => { const value = root?.querySelector('#' + id)?.value; if (value) query.set(key, value); });
    query.set('offset', offset); query.set('limit', limit);
    return query.toString();
  }

  function setBusy(busy) {
    document.querySelectorAll('#gdb-app button').forEach(button => { button.disabled = busy; });
    const stop = document.querySelector('#gdb-stop');
    if (stop) stop.disabled = !busy;
  }

  async function loadStatus() {
    const result = await api('gdb/status');
    const tag = document.querySelector('#gdb-tag');
    const status = document.querySelector('#gdb-status');
    if (!tag || !status) return result;
    tag.textContent = `${count(result.rowCount)}行`;
    status.textContent = `保存先: ${result.path} / ${count(result.dayCount)}日分（${result.firstDate || '—'} ～ ${result.lastDate || '—'}） / 取得対象の不足日: ${count(result.actionableMissingDates.length)}日 / 未掲載: ${count(result.unpublishedDates.length)}日 / BB・RB不足: ${count(result.missingBonusRows)}行（ジャグラー ${count(result.missingJugglerBonusRows)}行）`;
    return result;
  }

  async function loadRows() {
    const root = document.querySelector('#gdb-app');
    if (!root) return;
    const result = await api('gdb/rows?' + params());
    const target = root.querySelector('#gdb-results');
    root.querySelector('#gdb-count').textContent = `${count(result.total)}件中 ${result.total ? offset + 1 : 0}～${Math.min(offset + limit, result.total)}件`;
    root.querySelector('#gdb-page').textContent = `${Math.floor(offset / limit) + 1} / ${Math.max(1, Math.ceil(result.total / limit))}`;
    root.querySelector('#gdb-prev').disabled = offset === 0;
    root.querySelector('#gdb-next').disabled = offset + limit >= result.total;
    if (!result.rows.length) { target.innerHTML = '<div class="empty"><div class="symbol">▤</div><h2>条件に一致するデータがありません</h2><p>「不足日を取得」でデータを集めるか、絞り込み条件を確認してください。</p></div>'; return; }
    target.innerHTML = `<div class="table-scroll gdb-table"><table><thead><tr>${headers.map(header => `<th>${escLocal(header)}</th>`).join('')}</tr></thead><tbody>${result.rows.map(row => `<tr>${headers.map(header => `<td>${escLocal(display(row[header]))}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  }

  async function runAction(mode) {
    if (activeMode) return;
    setBusy(true); activeMode = mode;
    const status = document.querySelector('#gdb-status');
    if (status) status.textContent = {bonuses: 'BB・RBの実値が不足する日・台だけを補完しています…', range: '指定した期間の不足を取得しています…'}[mode] || '記録のない日だけを対象に、BB・RBを含めて取得しています…';
    try {
      const payload = mode === 'bonuses' ? {start: document.querySelector('#bonus-from').value || null, end: document.querySelector('#bonus-to').value || null}
        : mode === 'range' ? {start: document.querySelector('#range-from').value || null, end: document.querySelector('#range-to').value || null} : {};
      const result = await api('gdb/' + mode, payload);
      if (result.status === 'up-to-date') {
        activeMode = ''; setBusy(false); await loadStatus(); await loadRows();
        if (mode === 'range' && status) status.textContent += ' / 指定した期間に取得が必要な日はありません。';
        return;
      }
      await pollUntilFinished(mode);
    } catch (error) {
      activeMode = ''; setBusy(false); showError(error);
    }
  }

  async function pollUntilFinished(mode) {
    if (pollTimer) clearTimeout(pollTimer);
    const tick = async () => {
      const status = document.querySelector('#gdb-status');
      try {
        const progress = await api('scrape/status');
        if (progress.active || progress.state === 'running') {
          if (status) status.textContent = `${{bonuses: 'BB・RB補完', range: '期間指定の取得'}[mode] || '不足日の取得'}: ${progress.completed || 0}/${progress.total || 0}日 / ${progress.bonusRows || 0}/${progress.bonusTotal || 0}台 / ${progress.message || '取得中…'}`;
          pollTimer = setTimeout(tick, 1800);
          return;
        }
        if (progress.state !== 'complete') throw Error(progress.message || 'スクレイピングに失敗しました。途中までの取得分は保存済みです。');
        activeMode = ''; setBusy(false); await loadStatus(); await loadRows();
        if (progress.missingBonusRows || progress.runFailures?.length) {
          if (status) status.textContent += ' / 一部未完了です。取得状況を確認し、不足だけを再実行してください。';
        }
      } catch (error) {
        activeMode = ''; setBusy(false); showError(error);
      }
    };
    await tick();
  }

  function mount() {
    if (mounted || page !== 'data') return;
    const app = shell();
    if (!app) return;
    mounted = true;
    render(app);
  }

  const observer = new MutationObserver(() => { mounted = false; mount(); });
  observer.observe(document.querySelector('#content'), {childList: true});
  mount();
})();
