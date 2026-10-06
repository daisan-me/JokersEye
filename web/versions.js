'use strict';

// バージョン画面：GitHub上の各ブランチの版を一覧し、本番データのコピーで起動する。
// サーバー側は app/versions.py。api() / showError() / notice() は app.js のもの。
const JokersVersions = (() => {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const short = sha => sha ? String(sha).slice(0, 7) : '不明';
  const when = value => value ? new Date(value).toLocaleString('ja-JP', {dateStyle: 'medium', timeStyle: 'short'}) : '不明';
  const megabytes = bytes => (Number(bytes || 0) / 1048576).toFixed(1) + ' MB';
  let root = null, overview = null, remote = null, remoteError = '', timer = null;

  function label(state) {
    const build = state.build || {}, element = document.querySelector('#build-label');
    if (element) element.textContent = (build.branch || 'ブランチ不明') + ' · ' + short(build.sha);
    document.body.classList.toggle('trial', !!state.trial);
    const pill = document.querySelector('#connection');
    if (state.trial && pill) pill.textContent = '● 試用中：' + state.trial.branch + '（' + short(state.trial.sha) + '）';
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
      <p>非公開リポジトリの版を読み込むため、最初に一度だけ登録します。トークンはこのPCのデータフォルダーにだけ保存し、GitHub以外には送りません。</p>
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
    root.innerHTML = progress() + current() + connection() + list();
    const form = root.querySelector('#version-token');
    if (form) form.addEventListener('submit', async event => {
      event.preventDefault();
      const button = form.querySelector('button'); button.disabled = true;
      try { await api('versions/token', {token: form.querySelector('input').value}); notice('GitHubのトークンを登録しました。'); await load(true); }
      catch (error) { showError(error); button.disabled = false; }
    });
    root.querySelectorAll('[data-version-action]').forEach(button => button.addEventListener('click', () => act(button).catch(showError)));
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

  return {mount, label};
})();
