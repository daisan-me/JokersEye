'use strict';

/* GothamDataBase (GDB): the 11-column view of the seat records stored in GothamDataBase.sqlite.
   Viewing only; scraping runs from the 「公開データを取得する」 panel (web/scrape.js). */
(() => {
  const headers = ['日付', '曜日', '台番号', '機種', 'ジャグラーかジャグラーじゃないか', 'ゲーム数', 'BB数', 'RB数', '合成', '差枚', '出率'];
  let mounted = false;
  let offset = 0;
  const limit = 100;

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
    observations.closest('.card')?.before(card);  // above 「登録した観測」, below the scraping panel
    return card.querySelector('#gdb-app');
  }

  function render(app) {
    app.innerHTML = `
      <div class="card-header"><div><div class="kicker">MIN-REPO / GOTHAM DATABASE</div><h2>GothamDataBase（GDB）</h2></div><span class="tag" id="gdb-tag">0行</span></div>
      <p>GDBに保存したみんレポ ゴッサムシティの台別記録（2024/03/01以降、日付・台番号ごとに1行）を、条件で絞り込んで閲覧します。「-」は公開ページで「-」と表示された値（理由は区別しません）、「未取得」はまだ取得していない値です。</p>
      <p class="hint" id="gdb-status">状態を確認中…</p>
      <details class="gdb-manage" id="gdb-manage"><summary>削除・ロック</summary>
        <p class="hint">期間内の全台のデータを削除して空欄（未取得）に戻せます。削除した日は「期間を指定して取得」で取り直せます（「本日までの分を更新」はGDBの最後の日より後だけを取るため、それより前の削除した日は取り直しません）。目視で問題がないと確認した日はロックすると、削除・上書き・取得の対象から外れます。取得中は操作できません。</p>
        <div class="toolbar"><label>開始日<input id="gdb-manage-from" type="date" min="2024-03-01"></label><label>終了日<input id="gdb-manage-to" type="date" min="2024-03-01"></label>
          <button class="danger" id="gdb-delete">期間のデータを削除</button><button class="secondary" id="gdb-lock">期間をロック</button><button class="secondary" id="gdb-unlock">ロックを解除</button></div>
        <p class="hint" id="gdb-locked">ロック中の日: —</p>
        <div class="toolbar"><button class="secondary" id="gdb-lock-rows">11列そろいの行をすべてロック</button><button class="secondary" id="gdb-unlock-rows">行のロックをすべて解除</button></div>
        <p class="hint">11列すべてに数値がある行（日付×台番号）をまとめてロックします。ロックした行は上書き・削除されません（その日のほかの行は取得・削除できます）。押した時点でそろっている行が対象で、あとからそろった行は、もう一度押すとロックされます。</p>
        <p class="hint" id="gdb-locked-rows">ロック中の行: —</p>
      </details>
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
    const today = new Date(Date.now() + 9 * 3600 * 1000).toISOString().slice(0, 10);  // Japan time, as the server
    app.querySelectorAll('#gdb-manage-from,#gdb-manage-to').forEach(input => { input.max = today; });
    app.querySelector('#gdb-delete').addEventListener('click', () => manage('delete').catch(showError));
    app.querySelector('#gdb-lock').addEventListener('click', () => manage('lock').catch(showError));
    app.querySelector('#gdb-unlock').addEventListener('click', () => manage('unlock').catch(showError));
    app.querySelector('#gdb-lock-rows').addEventListener('click', () => manageRows('lock-complete-rows').catch(showError));
    app.querySelector('#gdb-unlock-rows').addEventListener('click', () => manageRows('unlock-rows').catch(showError));
    app.querySelector('#gdb-apply').addEventListener('click', () => { offset = 0; loadRows().catch(showError); });
    app.querySelector('#gdb-reset').addEventListener('click', () => { app.querySelectorAll('.gdb-filters input').forEach(input => { input.value = ''; }); app.querySelector('#gdb-juggler').value = 'all'; app.querySelector('#gdb-weekday').value = ''; offset = 0; loadRows().catch(showError); });
    app.querySelector('#gdb-prev').addEventListener('click', () => { offset = Math.max(0, offset - limit); loadRows().catch(showError); });
    app.querySelector('#gdb-next').addEventListener('click', () => { offset += limit; loadRows().catch(showError); });
    loadStatus().then(loadRows).catch(showError);
  }

  // Consecutive locked days shown as ranges: 2024-03-01～2024-03-05, 2024-04-01
  function ranges(days) {
    const out = [];
    days.forEach(day => {
      const last = out[out.length - 1];
      const next = last && new Date(Date.parse(last[1] + 'T00:00:00Z') + 86400000).toISOString().slice(0, 10);
      if (last && next === day) last[1] = day; else out.push([day, day]);
    });
    return out.map(([a, b]) => a === b ? a : `${a}～${b}`).join(', ');
  }

  async function manage(action) {
    const root = document.querySelector('#gdb-app');
    const start = root.querySelector('#gdb-manage-from').value, end = root.querySelector('#gdb-manage-to').value || start;
    if (!start) throw new Error('開始日を指定してください。');
    const span = start === end ? start : `${start} ～ ${end}`;
    if (action === 'delete' && !confirm(`${span} の全台のデータを削除します。ロック中の日は残します。削除すると元に戻せません（「期間を指定して取得」で取り直せます）。削除しますか？`)) return;
    if (action === 'unlock' && !confirm(`${span} のロックを解除しますか？解除した日は削除・上書き・取得の対象に戻ります。`)) return;
    const result = await api('gdb/' + action, {start, end});
    if (action === 'delete') notice(`${span}：${count(result.deletedDays)}日・${count(result.deletedRows)}行を削除しました。${result.lockedKept.length ? `ロック中の${count(result.lockedKept.length)}日は残しました。` : ''}${result.lockedRowsKept ? `ロック中の${count(result.lockedRowsKept)}行は残しました。` : ''}`);
    else notice(`${span}（${count(result.days)}日）を${action === 'lock' ? 'ロックしました' : 'ロック解除しました'}。`);
    await loadStatus(); await loadRows();
    document.dispatchEvent(new CustomEvent('jokers:data-changed'));
  }

  async function manageRows(action) {
    if (action === 'unlock-rows' && !confirm('ロック中の行をすべて解除しますか？解除した行は上書き・削除の対象に戻ります。')) return;
    const result = await api('gdb/' + action, {});
    notice(action === 'lock-complete-rows' ? `11列そろいの行を${count(result.added)}行ロックしました（ロック中 ${count(result.lockedRows)}行）。` : `行のロックを${count(result.removed)}行解除しました。`);
    await loadStatus(); await loadRows();
    document.dispatchEvent(new CustomEvent('jokers:data-changed'));
  }

  function params() {
    const root = document.querySelector('#gdb-app');
    const query = new URLSearchParams();
    [['from','gdb-from'],['to','gdb-to'],['month','gdb-month'],['weekday','gdb-weekday'],['model','gdb-model'],['juggler','gdb-juggler'],['games_min','gdb-games'],['net_min','gdb-net'],['combined_n_min','gdb-combined'],['payout_min','gdb-payout']].forEach(([key, id]) => { const value = root?.querySelector('#' + id)?.value; if (value) query.set(key, value); });
    query.set('offset', offset); query.set('limit', limit);
    return query.toString();
  }

  async function loadStatus() {
    const result = await api('gdb/status');
    const tag = document.querySelector('#gdb-tag');
    const status = document.querySelector('#gdb-status');
    if (!tag || !status) return result;
    tag.textContent = `${count(result.rowCount)}行`;
    status.textContent = `保存先: ${result.path} / ${count(result.dayCount)}日分（${result.firstDate || '—'} ～ ${result.lastDate || '—'}） / 記録がない日: ${count(result.actionableMissingDates.length)}日 / 未掲載: ${count(result.unpublishedDates.length)}日 / BB・RB未取得: ${count(result.missingBonusRows)}行（ジャグラー ${count(result.missingJugglerBonusRows)}行） / ロック中: ${count(result.lockedDates.length)}日`;
    const locked = document.querySelector('#gdb-locked');
    if (locked) locked.textContent = `ロック中の日（${count(result.lockedDates.length)}日）: ${result.lockedDates.length ? ranges(result.lockedDates) : 'なし'}`;
    const lockedRows = document.querySelector('#gdb-locked-rows');
    if (lockedRows) lockedRows.textContent = `ロック中の行: ${count(result.lockedRows)}行`;
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
    if (!result.rows.length) { target.innerHTML = '<div class="empty"><div class="symbol">▤</div><h2>条件に一致するデータがありません</h2><p>上の「公開データを取得する」でデータを集めるか、絞り込み条件を確認してください。</p></div>'; return; }
    target.innerHTML = `<div class="table-scroll gdb-table"><table><thead><tr>${headers.map(header => `<th>${escLocal(header)}</th>`).join('')}</tr></thead><tbody>${result.rows.map(row => `<tr>${headers.map(header => `<td>${escLocal(display(row[header]))}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  }

  function mount() {
    if (mounted || page !== 'data') return;
    const app = shell();
    if (!app) return;
    mounted = true;
    render(app);
  }

  // Deleting or locking is refused while a run is going: disable the buttons meanwhile.
  document.addEventListener('jokers:scrape-state', event => { document.querySelectorAll('#gdb-delete,#gdb-lock,#gdb-unlock,#gdb-lock-rows,#gdb-unlock-rows').forEach(button => { button.disabled = event.detail.running; }); });
  // A finished scraping run changes GDB: show the new rows without reopening the page.
  document.addEventListener('jokers:data-changed', () => { if (document.querySelector('#gdb-app')) loadStatus().then(loadRows).catch(showError); });
  const observer = new MutationObserver(() => { mounted = false; mount(); });
  observer.observe(document.querySelector('#content'), {childList: true});
  mount();
})();
