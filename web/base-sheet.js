'use strict';

/* The base data sheet is a local CSV view. Scraping is started explicitly and
   the update path sends only dates that are absent from the existing sheet. */
(() => {
  const headers = ['日付', '曜日', '台番号', '機種', 'ジャグラーかジャグラーじゃないか', 'ゲーム数', 'BB数', 'RB数', '合成', '差枚', '出率'];
  let mounted = false;
  let offset = 0;
  const limit = 100;
  let activeMode = '';
  let pollTimer = null;

  const escLocal = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const display = value => value === '' || value === null || value === undefined ? '未取得' : value;

  function shell() {
    if (document.querySelector('#base-sheet-card')) return document.querySelector('#base-sheet-app');
    const observations = document.querySelector('#observations');
    if (!observations) return null;
    const card = document.createElement('section');
    card.className = 'card';
    card.id = 'base-sheet-card';
    card.innerHTML = '<div id="base-sheet-app"><p>基礎データシートを読み込み中…</p></div>';
    observations.closest('.card')?.after(card);
    return card.querySelector('#base-sheet-app');
  }

  function render(app) {
    app.innerHTML = `
      <div class="card-header"><div><div class="kicker">MIN-REPO / BASE DATA SHEET</div><h2>基礎データシート</h2></div><span class="tag" id="base-sheet-tag">未作成</span></div>
      <p>みんレポ ゴッサムシティの台別記録を、日付・台番号単位で保存して閲覧します。空欄は公開ページで取得できなかった値です。</p>
      <div class="base-sheet-actions"><button class="primary" id="base-sheet-create">CSVファイルを作成</button><button class="secondary" id="base-sheet-update">不足分だけ更新</button><a class="secondary" id="base-sheet-download" href="/api/base-sheet/download" download="jokers-eye-base-data.csv">CSVをダウンロード</a></div>
      <p class="hint" id="base-sheet-status">状態を確認中…</p>
      <div class="base-sheet-filters">
        <div><label class="label" for="base-from">日付（開始）</label><input id="base-from" type="date"></div>
        <div><label class="label" for="base-to">日付（終了）</label><input id="base-to" type="date"></div>
        <div><label class="label" for="base-month">月</label><input id="base-month" type="month"></div>
        <div><label class="label" for="base-weekday">曜日</label><select id="base-weekday"><option value="">すべて</option><option value="0">月曜日</option><option value="1">火曜日</option><option value="2">水曜日</option><option value="3">木曜日</option><option value="4">金曜日</option><option value="5">土曜日</option><option value="6">日曜日</option></select></div>
        <div><label class="label" for="base-model">機種（部分一致）</label><input id="base-model" type="text" placeholder="例：マイジャグラー"></div>
        <div><label class="label" for="base-juggler">系統</label><select id="base-juggler"><option value="all">すべて</option><option value="juggler">ジャグラーのみ</option><option value="non-juggler">ジャグラー以外</option></select></div>
        <div><label class="label" for="base-games">ゲーム数 以上</label><input id="base-games" type="number" min="0" placeholder="例：5000"></div>
        <div><label class="label" for="base-net">差枚 以上</label><input id="base-net" type="number" placeholder="例：1000"></div>
        <div><label class="label" for="base-combined">合成 1/n 以下（n）</label><input id="base-combined" type="number" min="1" placeholder="例：140"></div>
        <div><label class="label" for="base-payout">出率 以上（%）</label><input id="base-payout" type="number" step="0.1" placeholder="例：105"></div>
      </div>
      <div class="actions base-sheet-filter-actions"><button class="primary" id="base-sheet-apply">条件を適用</button><button class="secondary" id="base-sheet-reset">条件をリセット</button></div>
      <div class="base-sheet-result-head"><span id="base-sheet-count">—</span><span class="hint">表示件数は最大100件</span></div>
      <div id="base-sheet-results"><p class="muted">読み込み中…</p></div>
      <div class="base-sheet-pagination"><button class="secondary" id="base-sheet-prev">← 前へ</button><span id="base-sheet-page">1</span><button class="secondary" id="base-sheet-next">次へ →</button></div>`;
    app.querySelector('#base-sheet-create').addEventListener('click', () => runAction('create'));
    app.querySelector('#base-sheet-update').addEventListener('click', () => runAction('update'));
    app.querySelector('#base-sheet-apply').addEventListener('click', () => { offset = 0; loadRows().catch(showError); });
    app.querySelector('#base-sheet-reset').addEventListener('click', () => { app.querySelectorAll('.base-sheet-filters input').forEach(input => { input.value = ''; }); app.querySelector('#base-juggler').value = 'all'; app.querySelector('#base-weekday').value = ''; offset = 0; loadRows().catch(showError); });
    app.querySelector('#base-sheet-prev').addEventListener('click', () => { offset = Math.max(0, offset - limit); loadRows().catch(showError); });
    app.querySelector('#base-sheet-next').addEventListener('click', () => { offset += limit; loadRows().catch(showError); });
    loadStatus().then(loadRows).catch(showError);
  }

  function params() {
    const root = document.querySelector('#base-sheet-app');
    const query = new URLSearchParams();
    [['from','base-from'],['to','base-to'],['month','base-month'],['weekday','base-weekday'],['model','base-model'],['juggler','base-juggler'],['games_min','base-games'],['net_min','base-net'],['combined_n_min','base-combined'],['payout_min','base-payout']].forEach(([key, id]) => { const value = root?.querySelector('#' + id)?.value; if (value) query.set(key, value); });
    query.set('offset', offset); query.set('limit', limit);
    return query.toString();
  }

  function setBusy(busy) {
    document.querySelectorAll('#base-sheet-app button').forEach(button => { button.disabled = busy; });
  }

  async function loadStatus() {
    const result = await api('base-sheet/status');
    const tag = document.querySelector('#base-sheet-tag');
    const status = document.querySelector('#base-sheet-status');
    if (!tag || !status) return result;
    tag.textContent = result.exists ? `${Number(result.rowCount).toLocaleString('ja-JP')}行` : '未作成';
    status.textContent = result.exists ? `保存先: ${result.path} / ${result.dayCount}日分（${result.firstDate || '—'} ～ ${result.lastDate || '—'}） / 取得対象の不足日: ${(result.actionableMissingDates || result.missingDates).length}日 / 未掲載: ${(result.unpublishedDates || []).length}日` : 'まだCSVは作成されていません。作成ボタンで、2024/03/01～2026/09/30を対象にします。';
    document.querySelector('#base-sheet-download').hidden = !result.exists;
    return result;
  }

  async function loadRows() {
    const root = document.querySelector('#base-sheet-app');
    if (!root) return;
    const result = await api('base-sheet/rows?' + params());
    const target = root.querySelector('#base-sheet-results');
    root.querySelector('#base-sheet-count').textContent = `${Number(result.total).toLocaleString('ja-JP')}件中 ${result.total ? offset + 1 : 0}～${Math.min(offset + limit, result.total)}件`;
    root.querySelector('#base-sheet-page').textContent = `${Math.floor(offset / limit) + 1} / ${Math.max(1, Math.ceil(result.total / limit))}`;
    root.querySelector('#base-sheet-prev').disabled = offset === 0;
    root.querySelector('#base-sheet-next').disabled = offset + limit >= result.total;
    if (!result.rows.length) { target.innerHTML = '<div class="empty"><div class="symbol">▤</div><h2>条件に一致するデータがありません</h2><p>取得済みのデータ範囲か、絞り込み条件を確認してください。</p></div>'; return; }
    target.innerHTML = `<div class="table-scroll base-sheet-table"><table><thead><tr>${headers.map(header => `<th>${escLocal(header)}</th>`).join('')}</tr></thead><tbody>${result.rows.map(row => `<tr>${headers.map(header => `<td>${escLocal(display(row[header]))}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  }

  async function runAction(mode) {
    if (activeMode) return;
    setBusy(true); activeMode = mode;
    const status = document.querySelector('#base-sheet-status');
    if (status) status.textContent = mode === 'create' ? '全台表を日ごとに取得しています。BB/RBの追加取得失敗で後続日を止めません…' : '不足日だけを対象に、既存日を再取得せず更新しています…';
    try {
      const result = await api('base-sheet/' + mode, {});
      if (result.status === 'up-to-date' || result.status === 'exists') {
        activeMode = ''; setBusy(false); await loadStatus(); await loadRows(); return;
      }
      await pollUntilFinished(mode);
    } catch (error) {
      activeMode = ''; setBusy(false); showError(error);
    }
  }

  async function pollUntilFinished(mode) {
    if (pollTimer) clearTimeout(pollTimer);
    const status = document.querySelector('#base-sheet-status');
    const tick = async () => {
      try {
        const progress = await api('scrape/status');
        if (progress.state === 'running') {
          if (status) status.textContent = `${mode === 'create' ? '初回作成' : '不足分更新'}: ${progress.completed || 0}/${progress.total || 0}日 / ${progress.message || '取得中…'}`;
          pollTimer = setTimeout(tick, 1800);
          return;
        }
        if (progress.state !== 'complete') throw Error(progress.message || 'スクレイピングに失敗しました。');
        if (status) status.textContent = '取得が完了しました。CSVを再構成しています…';
        await api('base-sheet/build', {mode});
        activeMode = ''; setBusy(false); await loadStatus(); await loadRows();
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
