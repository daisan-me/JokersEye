'use strict';

// バージョン画面：このアプリをmainの最新版に更新する（app/updater.py）。GitHub上の各ブランチの版を一覧し、
// 本番データのコピーで起動する（app/versions.py）。api() / showError() / notice() / navigate() は app.js のもの。
const JokersVersions = (() => {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const short = sha => sha ? String(sha).slice(0, 7) : '不明';
  const when = value => value ? new Date(value).toLocaleString('ja-JP', {dateStyle: 'medium', timeStyle: 'short'}) : '不明';
  const megabytes = bytes => (Number(bytes || 0) / 1048576).toFixed(1) + ' MB';
  let root = null, overview = null, remote = null, remoteError = '', timer = null;
  let update = null, updateError = '', updateTimer = null, checking = null;

  function label(state) {
    const build = state.build || {}, element = document.querySelector('#build-label');
    if (element) element.textContent = (build.branch || 'ブランチ不明') + ' · ' + short(build.sha);
    document.body.classList.toggle('trial', !!state.trial);
    const pill = document.querySelector('#connection');
    if (state.trial && pill) pill.textContent = '● 試用中：' + state.trial.branch + '（' + short(state.trial.sha) + '）';
  }

  function canCheck(status) { return status && status.mode && (status.mode === 'source' || status.tokenRegistered); }

  function showBadge(status) {
    const badge = document.querySelector('#update-badge'), latest = status && status.latest;
    if (!badge) return;
    badge.hidden = !(latest && latest.newer);
    if (!badge.hidden) badge.textContent = '新しい版があります（' + (latest.version || 'main') + '）';
  }

  function resultText(result) {
    const to = result.to || {};
    if (result.status === 'ok') return `${to.version || ''}（${short(to.sha)}）に更新しました。` + (result.backup ? `前の版は ${result.backup} に残しています。` : '');
    if (result.status === 'restarted') return `mainの最新版（${short(to.sha)}）を取り込んで、起動し直しました。`;
    if (result.status === 'failed') return `更新できませんでした（${result.detail || '原因不明'}）。前の版のまま起動しています。`;
    if (result.status === 'busy') return 'アプリの終了を確認できなかったため、更新しませんでした。';
    return '前回の更新は完了しませんでした。前の版のまま起動しています。';
  }

  // One GitHub check at a time, shared by the startup check, the バージョン page and the periodic check.
  function requestCheck() {
    if (!checking) checking = api('update/check').then(status => { showBadge(status); return status; }).finally(() => { checking = null; });
    return checking;
  }

  // Called once after the app starts: report the last update, then look for a newer build quietly,
  // again every six hours while the app stays open.
  async function startup(state) {
    if (state.trial) return;
    const status = await api('update');
    if (status.lastResult) { notice(resultText(status.lastResult)); await api('update/acknowledge', {}); }
    if (!canCheck(status)) return;
    setInterval(() => requestCheck().catch(() => {}), 6 * 3600 * 1000);
    await requestCheck();
  }

  function updateCard() {
    if (!update || overview.trial) return '';
    if (!update.mode) return `<section class="card"><h2>このアプリの更新</h2><p>${escape(update.reason)}</p></section>`;
    const task = update.task || {}, latest = update.latest, busy = task.state === 'running';
    const source = update.mode === 'source';
    let body;
    if (!canCheck(update)) body = '<p>更新するには、下の「GitHubのトークンを登録する」で一度だけトークンを登録してください。</p>';
    else if (updateError) body = `<p class="version-error">${escape(updateError)}</p>`;
    else if (!latest) body = '<p>mainの最新版を確認しています…</p>';
    else if (!latest.available) body = latest.message ? `<p>${escape(latest.message)}</p>` : `<p>mainの最新版（${escape(latest.version)} · ${escape(short(latest.sha))}）を使っています。</p>`;
    else body = `<div class="row"><span>mainの最新版</span><strong>${escape(latest.version)} · <span class="path">${escape(short(latest.sha))}</span></strong></div>
      <div class="row"><span>作成日時</span><strong>${escape(when(latest.builtAt))}</strong></div>
      ${latest.blocked ? `<p class="version-error">${escape(latest.blocked)}</p>` : `<p class="hint">${source
        ? '「更新して再起動」で、このフォルダーにmainの最新版を取り込み（git pull）、アプリを起動し直します。'
        : '「更新して再起動」で、アプリを終了して新しい版に入れ替え、自動で起動し直します。データはそのまま使います。前の版は' + (update.platform === 'macos'
          ? 'データフォルダーの updates/previous に' : 'アプリのフォルダーの隣（名前の末尾が .previous）に') + '残します（次の更新で置き換わります）。'}</p>
      <label class="update-backup"><input type="checkbox" id="update-backup" checked ${busy ? 'disabled' : ''}> 更新の前にGDBのバックアップを作る</label>`}`;
    const progressBar = task.state === 'running' || task.state === 'failed'
      ? `<div class="version-progress ${escape(task.state)}" role="status"><p>${escape(task.message || '')}</p>${task.state === 'running'
        ? `<progress max="1" value="${task.step === 'download' && task.total ? Math.min(1, task.done / task.total) : task.step === 'download' ? 0 : 1}"></progress><p class="hint">${task.step === 'download' && task.total ? megabytes(task.done) + ' / ' + megabytes(task.total) : ''}</p>` : ''}</div>` : '';
    const ready = latest && latest.available && !latest.blocked && canCheck(update);
    return `<section class="card update-card"><div class="card-header"><h2>このアプリの更新</h2><span class="tag">${source ? '開発環境（git）' : '配布パッケージ'}</span></div>
      ${body}${progressBar}<div class="actions">${ready ? `<button class="primary" data-update-action="start" ${busy ? 'disabled' : ''}>更新して再起動</button>` : ''}
      ${canCheck(update) ? `<button class="secondary" data-update-action="check" ${busy ? 'disabled' : ''}>最新版を確認</button>` : ''}</div></section>`;
  }

  async function checkUpdate() {
    updateError = '';
    try { update = await requestCheck(); } catch (error) { updateError = error.message; }
  }

  async function updateAct(button) {
    const action = button.dataset.updateAction;
    if (action === 'check') { button.disabled = true; await checkUpdate(); return render(); }
    if (action === 'start') {
      if (!confirm('アプリを終了して、新しい版で起動し直します。よろしいですか？')) return;
      const backup = !!root.querySelector('#update-backup')?.checked;
      button.disabled = true;
      update = await api('update/start', {backup});
      render(); watchUpdate();
    }
  }

  // Polls the update until the app shuts itself down (the requests then fail and polling stops).
  function watchUpdate() {
    if (updateTimer) return;
    updateTimer = setInterval(async () => {
      try {
        update = await api('update');
        if (update.task.state !== 'running') { clearInterval(updateTimer); updateTimer = null; }
        if (update.task.step === 'restart') notice('新しい版で起動し直します。このウィンドウは自動で閉じます。');
        if (root && root.isConnected) render();
      } catch (error) { clearInterval(updateTimer); updateTimer = null; }
    }, 1000);
  }

  function current() {
    const build = overview.current, trial = overview.trial;
    return `<section class="card"><div class="card-header"><h2>この画面の版</h2><span class="tag">${build.packaged ? '配布パッケージ' : '開発環境'}</span></div>
      ${trial ? '<p class="version-trial">試用中の版です。データは本番のコピーを使っているため、ここで取得・変更したデータは本番に反映されません。</p>' : ''}
      <div class="row"><span>版数</span><strong>${escape(build.version)}</strong></div>
      <div class="row"><span>ブランチ</span><strong>${escape(build.branch || '不明')}</strong></div>
      <div class="row"><span>コミット</span><strong class="path">${escape(short(build.sha))}</strong></div>
      <div class="row"><span>作成日時</span><strong>${escape(when(build.builtAt))}</strong></div></section>`;
  }

  function connection() {
    if (overview.trial) return '';
    if (overview.tokenRegistered) {
      return `<section class="card"><div class="card-header"><h2>GitHubとの接続</h2><span class="tag">登録済み</span></div>
        <p>${escape(overview.repo)} の版一覧を読み込めます。</p><div class="actions"><button class="secondary" data-version-action="token-delete">トークンを削除</button></div></section>`;
    }
    return `<section class="card"><div class="kicker">FIRST TIME ONLY</div><h2>GitHubのトークンを登録する</h2>
      <p>GitHubの自動ビルド（Actions）の版をダウンロードするため、最初に一度だけ登録します（アプリの更新にも使います）。トークンはこのPCのデータフォルダーにだけ保存し、GitHub以外には送りません。</p>
      <ol class="version-steps"><li><a href="${escape(overview.tokenPage)}">GitHubのトークン作成ページ</a>を開きます。</li>
      <li>「Repository access」で「Only select repositories」→ ${escape(overview.repo)} を選びます。</li>
      <li>「Permissions」の「Actions」を「Read-only」にして作成し、表示されたトークンをコピーします。</li></ol>
      <form id="version-token"><label class="label" for="version-token-input">トークン</label><input id="version-token-input" type="password" autocomplete="off" required>
      <div class="actions"><button class="primary" type="submit">登録する</button></div></form></section>`;
  }

  function progress() {
    const task = overview.task || {};
    if (!task.state || task.state === 'idle') return '';
    const ratio = task.total ? Math.min(1, task.done / task.total) : 0;
    return `<section class="card version-progress ${escape(task.state)}" role="status"><h2>${escape(task.branch || '')}</h2><p>${escape(task.message || '')}</p>
      ${task.state === 'running' ? `<progress max="1" value="${task.step === 'download' ? ratio : 1}"></progress><p class="hint">${task.step === 'download' && task.total ? megabytes(task.done) + ' / ' + megabytes(task.total) : ''}</p>` : ''}</section>`;
  }

  function statusText(row) {
    if (!row.artifactId) {
      const n = row.newer || {};
      return n.status !== 'completed' ? '作成中' : n.conclusion === 'success' ? 'このOS用なし' : '自動テスト失敗';
    }
    if (row.newer) return row.newer.status !== 'completed' ? '新しい版を作成中' : '最新の作成は失敗（この版は以前の成功分）';
    return '';
  }

  function list() {
    if (overview.trial || !overview.tokenRegistered) return '';
    if (!overview.platform) return '<section class="card"><h2>ブランチごとの版</h2><p>この機能はWindowsとmacOSで使えます。</p></section>';
    const installed = new Map(overview.installed.map(row => [row.id, row]));
    const running = overview.task && overview.task.state === 'running';
    const rows = (remote || []).map(row => {
      const local = installed.get(row.id), status = statusText(row);
      const buttons = !row.artifactId ? '' : row.current ? '<span class="tag">この画面の版</span>'
        : local ? `<button class="primary" data-version-action="launch" data-id="${escape(row.id)}" ${local.running ? 'disabled' : ''}>${local.running ? '起動中' : '起動'}</button>`
        : `<button class="primary" data-version-action="install" data-artifact="${escape(row.artifactId)}" ${running ? 'disabled' : ''}>ダウンロードして起動</button>`;
      return `<tr><td><strong>${escape(row.branch)}</strong>${status ? `<br><small class="muted">${escape(status)}</small>` : ''}</td><td>${escape(row.version || '—')}</td><td class="path">${escape(short(row.sha))}</td>
        <td>${escape(when(row.builtAt))}</td><td>${row.artifactId ? megabytes(row.size) : '—'}</td><td><div class="version-buttons">${buttons}${row.runUrl ? ` <a href="${escape(row.runUrl)}">GitHubで見る</a>` : ''}</div></td></tr>`;
    }).join('');
    const extra = overview.installed.filter(row => !(remote || []).some(r => r.id === row.id));
    const local = overview.installed.map(row => `<tr><td><strong>${escape(row.branch)}</strong></td><td>${escape(row.version)}</td><td class="path">${escape(short(row.sha))}</td><td>${escape(when(row.installedAt))}</td>
      <td><div class="version-buttons">${extra.includes(row) ? `<button class="secondary" data-version-action="launch" data-id="${escape(row.id)}" ${row.running ? 'disabled' : ''}>${row.running ? '起動中' : '起動'}</button> ` : ''}<button class="secondary" data-version-action="remove" data-id="${escape(row.id)}" ${row.running ? 'disabled' : ''}>削除</button></div></td></tr>`).join('');
    return `<section class="card"><div class="card-header"><h2>ブランチごとの版</h2><button class="secondary" data-version-action="reload">一覧を更新</button></div>
      <p>各ブランチで自動テストに通った最新の版です。初めて起動する版には、本番データをコピーして使います（本番データは変わりません）。</p>
      ${remoteError ? `<p class="version-error">${escape(remoteError)}</p>` : remote === null ? '<p>GitHubから読み込んでいます…</p>'
        : `<div class="table-scroll"><table><thead><tr><th>ブランチ</th><th>版数</th><th>コミット</th><th>作成日時</th><th>サイズ</th><th></th></tr></thead><tbody>${rows || '<tr><td colspan="6">版がありません。</td></tr>'}</tbody></table></div>`}</section>
      ${local ? `<section class="card"><h2>このPCにある版</h2><p>削除すると、その版用にコピーしたデータも消えます。本番データは消えません。</p><div class="table-scroll"><table><thead><tr><th>ブランチ</th><th>版数</th><th>コミット</th><th>ダウンロード日時</th><th></th></tr></thead><tbody>${local}</tbody></table></div></section>` : ''}`;
  }

  function render() {
    if (!root || !root.isConnected) return stop();
    root.innerHTML = updateCard() + progress() + current() + connection() + list();
    const form = root.querySelector('#version-token');
    if (form) form.addEventListener('submit', async event => {
      event.preventDefault();
      const button = form.querySelector('button'); button.disabled = true;
      try { await api('versions/token', {token: form.querySelector('input').value}); notice('GitHubのトークンを登録しました。'); await load(true); }
      catch (error) { showError(error); button.disabled = false; }
    });
    root.querySelectorAll('[data-version-action]').forEach(button => button.addEventListener('click', () => act(button).catch(showError)));
    root.querySelectorAll('[data-update-action]').forEach(button => button.addEventListener('click', () => updateAct(button).catch(error => { showError(error); render(); })));
  }

  async function act(button) {
    const action = button.dataset.versionAction;
    if (action === 'reload') return load(true);
    if (action === 'token-delete') { if (!confirm('登録したトークンを削除しますか？')) return; await api('versions/token/delete', {}); return load(true); }
    if (action === 'remove' && !confirm('この版と、その版用にコピーしたデータを削除しますか？（本番データは消えません）')) return;
    button.disabled = true;
    if (action === 'install') await api('versions/install', {artifactId: Number(button.dataset.artifact)});
    if (action === 'launch') { await api('versions/launch', {id: button.dataset.id}); notice('別のウィンドウで起動しました。'); }
    if (action === 'remove') await api('versions/remove', {id: button.dataset.id});
    await load(false);
  }

  async function load(withRemote) {
    overview = await api('versions');
    if (!overview.trial) update = await api('update');
    if (withRemote && canCheck(update)) { render(); await checkUpdate(); }
    if (update && update.task && update.task.state === 'running') watchUpdate();
    if (withRemote && overview.tokenRegistered && !overview.trial && overview.platform) {
      remote = null; remoteError = ''; render();
      try { remote = (await api('versions/remote')).versions; } catch (error) { remoteError = error.message; }
    }
    render();
    const busy = overview.task && overview.task.state === 'running';
    if (busy && !timer) timer = setInterval(() => load(false).catch(showError), 1000);
    if (!busy && timer) { stop(); if (overview.task.state === 'done') load(true).catch(showError); }
  }

  function stop() { if (timer) clearInterval(timer); timer = null; }

  async function mount(element) {
    root = element; remote = null; remoteError = '';
    await load(true);
  }

  return {mount, label, startup};
})();
