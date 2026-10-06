'use strict';

(() => {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const probability = value => value === null || value === undefined ? '未掲載' : '1/' + Number(value).toFixed(1);
  let catalogPromise;
  let calculators;
  let selectedId = 'myjuggler5';
  let selectedTab = 'specs';
  const drafts = new Map();

  async function catalog() {
    if (!catalogPromise) {
      catalogPromise = fetch('/juggler-specs.json').then(async response => {
        if (!response.ok) throw new Error('ジャグラーの機種別資料を読み込めませんでした。');
        const value = await response.json();
        calculators = JokersJugglerMath.createCalculators(value);
        return value;
      }).catch(error => { catalogPromise = null; throw error; });
    }
    return catalogPromise;
  }

  function specTable(machine) {
    return `<div class="table-scroll juggler-spec-table"><table><caption>${escape(machine.name)}の設定別スペック</caption>
      <thead><tr><th scope="col">設定</th><th scope="col">BB確率</th><th scope="col">RB確率</th><th scope="col">合成確率</th><th scope="col">出玉率</th></tr></thead>
      <tbody>${machine.settings.map(row => `<tr><th scope="row">設定 ${row.setting}</th><td>${probability(row.bbDenominator)}</td><td class="juggler-rb">${probability(row.rbDenominator)}</td><td>${probability(row.combinedDenominator)}</td><td>${escape(row.payoutPercent)}<small>%</small></td></tr>`).join('')}</tbody></table></div>`;
  }

  function sources(machine) {
    return `<div class="juggler-sources"><a href="${escape(machine.sourceUrl)}" target="_blank" rel="noopener noreferrer">北電子・公式スペック ↗</a>${machine.payoutSourceUrl ? `<a href="${escape(machine.payoutSourceUrl)}" target="_blank" rel="noopener noreferrer">公式配当表 ↗</a>` : ''}<span>参照日 ${escape(machine.verifiedOn)}</span></div>`;
  }

  function input(name, label, note = '', value = '', {minimum = 0, step = 1} = {}) {
    return `<div><label class="label" for="juggler-${name}">${label}</label><input id="juggler-${name}" name="${name}" type="number" ${minimum === null ? '' : `min="${minimum}"`} step="${step}" value="${escape(value)}" placeholder="未入力">${note ? `<small>${note}</small>` : ''}</div>`;
  }

  function observedInputs() {
    return input('games', '通常時ゲーム数', 'ボーナス中を含めない') + input('bb', 'BB回数') + input('rb', 'RB回数');
  }

  function pendingButton(label) {
    return `<div class="juggler-pending-action"><button class="primary" type="button" disabled>${label}（未実行）</button><span>今回は関数の準備まで。入力・機種切替で計算は実行しません。</span></div>`;
  }

  function specsPanel(machine, data) {
    const hasBonusTable = machine.settings.every(row => row.bbDenominator !== null && row.rbDenominator !== null);
    return `<section class="card juggler-reference-card"><div class="card-header"><div><div class="kicker">OFFICIAL / SETTING REFERENCE</div><h2>${escape(machine.name)}</h2></div><span class="tag">${machine.generation}号機</span></div>${specTable(machine)}
      <p class="hint">メーカー公表値。出玉率は工場データから算出した予測値です。${hasBonusTable ? '' : 'この旧機種はメーカーの表にBB・RB別の確率が未掲載です。'}</p>${sources(machine)}</section>
      <details class="card juggler-all-specs"><summary>全機種の設定別スペックを見る <span>${data.machines.length} 製品・パネル</span></summary><p class="hint">北電子の製品一覧に掲載された2007年以降のジャグラー。6号機と過去の5号機を区別しています。未掲載の数値は空欄扱いです。</p><div class="juggler-reference-grid">${data.machines.map(item => `<section><div class="card-header"><h3>${escape(item.name)}</h3><span class="tag">${item.generation}号機</span></div>${specTable(item)}${sources(item)}</section>`).join('')}</div></details>`;
  }

  function grapesPanel(machine) {
    return `<div class="juggler-work-grid"><section class="card"><div class="card-header"><div><div class="kicker">GRAPE / REVERSE ESTIMATOR</div><h2>ぶどう逆算の入力</h2></div><span class="tag">関数準備済み</span></div>
      <form class="juggler-input-form" data-tool="grapes"><div class="juggler-input-grid">${observedInputs()}${input('net', '差枚', 'プラス・マイナスの実枚数', '', {minimum: null})}</div>
      <details class="juggler-assumptions" open><summary>逆算の条件</summary><div class="juggler-input-grid">${input('bbNetCoins', 'BB1回の純獲得枚数', '実際の獲得枚数で上書き可能', machine.bonusNetCoins?.bb ?? '')}${input('rbNetCoins', 'RB1回の純獲得枚数', '', machine.bonusNetCoins?.rb ?? '')}${input('grapePayoutCoins', 'ぶどう1回の払い出し枚数', '通常時・3枚掛け', machine.grapePayoutCoins ?? '')}${input('replayDenominator', 'リプレイ確率の分母', '1/n の n。見込み回数を使う条件', '', {minimum: 1, step: 'any'})}${input('cherryCoinsPerGame', 'チェリーの見込み枚数 / 1G', '取りこぼし・有効ラインを含む平均獲得枚数', '', {minimum: 0, step: 'any'})}${input('otherPayoutCoins', 'その他小役の獲得枚数（合計）', 'ベル・ピエロ等。除外する場合は0')}</div></details>
      ${pendingButton('ぶどうを逆算')}</form></section>
      <section class="card juggler-output"><div class="kicker">RESULT / NOT CALCULATED</div><h2>ぶどう逆算の結果</h2><div class="juggler-grape-result"><span>ぶどう確率</span><strong>1 / —</strong><small>未計算</small></div><div class="row"><span>推定ぶどう回数</span><strong>— 回</strong></div><p>差枚と純獲得枚数の収支から、リプレイの掛け枚数分と、チェリー・その他小役の獲得枚数を差し引く関数を用意しています。</p><p class="hint">条件に見込み値を使う場合、ぶどうは概算です。チェリーの配当表の1ライン分を、そのまま1回の総獲得枚数とは扱いません。過去の5号機は獲得枚数・払い出し枚数の指定が必要です。</p>${sources(machine)}</section></div>`;
  }

  function settingPanel(machine) {
    const hasBonusTable = machine.settings.every(row => row.bbDenominator !== null && row.rbDenominator !== null);
    return `<div class="juggler-work-grid"><section class="card"><div class="card-header"><div><div class="kicker">SETTING / PROBABILITY ESTIMATOR</div><h2>設定推測の入力</h2></div><span class="tag">関数準備済み</span></div>
      <form class="juggler-input-form" data-tool="settings"><div class="juggler-input-grid">${observedInputs()}</div><div class="juggler-method"><h3>BB・RBから設定1〜6を比較</h3><p>設定ごとのBB・RB確率と、ボーナス非当選ゲームの確率を使うベイズ推測。事前の重みは各設定を均等にする定義です。</p><p class="hint">ぶどう逆算値と出玉率は、BB・RBとは別の証拠として自動加算しません。${hasBonusTable ? '' : 'この旧機種のBB・RB別の理論値は未掲載のため、関数の利用には出典確認済みの理論値の指定が必要です。'}</p></div>${pendingButton('設定確率を推測')}</form></section>
      <section class="card juggler-output"><div class="card-header"><div><div class="kicker">SETTING DISTRIBUTION</div><h2>設定別の推測確率</h2></div><span class="tag">未計算</span></div><div class="juggler-probabilities">${machine.settings.map(row => `<div class="juggler-probability-row"><span>設定 ${row.setting}</span><div class="juggler-probability-track" aria-hidden="true"></div><strong>—<small>%</small></strong></div>`).join('')}</div><p class="hint">計算関数は「設定1：n% … 設定6：n%」用の確率を返します。現時点では計算を実行していません。</p>${sources(machine)}</section></div>`;
  }

  function saveDraft(root) {
    const form = root.querySelector('.juggler-input-form');
    if (form) drafts.set(selectedId + ':' + form.dataset.tool, Object.fromEntries(new FormData(form)));
  }

  function render(root, data) {
    const machine = data.machines.find(item => item.id === selectedId) || data.machines[0];
    selectedId = machine.id;
    root.innerHTML = `<section class="card juggler-selector"><div class="card-header"><div><div class="kicker">JUGGLER / RESEARCH WORKSPACE</div><h2>機種と理論値を選ぶ</h2></div><span class="tag">参照・入力のみ</span></div><div class="juggler-model-toolbar"><div><label class="label" for="juggler-model">ジャグラー機種</label><select id="juggler-model">${[6, 5].map(generation => `<optgroup label="${generation === 6 ? '6号機' : '過去資料・5号機'}">${data.machines.filter(item => item.generation === generation).map(item => `<option value="${escape(item.id)}" ${item.id === selectedId ? 'selected' : ''}>${escape(item.name)} · ${escape(item.introduced)}</option>`).join('')}</optgroup>`).join('')}</select></div><div class="juggler-reference-meta"><span>北電子 公式資料</span><strong>設定 1〜6</strong><small>参照日 ${escape(data.verifiedOn)} / パネル別仕様を保持</small></div></div>
      <div class="juggler-tabs" role="tablist" aria-label="ジャグラー分析の表示">${[['specs','設定別スペック'],['grapes','ぶどう逆算機'],['settings','設定推測機']].map(([id, label]) => `<button type="button" role="tab" id="juggler-tab-${id}" data-juggler-tab="${id}" aria-selected="${id === selectedTab}" aria-controls="juggler-panel" tabindex="${id === selectedTab ? 0 : -1}">${label}</button>`).join('')}</div></section>
      <div id="juggler-panel" role="tabpanel" aria-labelledby="juggler-tab-${selectedTab}" tabindex="0">${selectedTab === 'specs' ? specsPanel(machine, data) : selectedTab === 'grapes' ? grapesPanel(machine) : settingPanel(machine)}</div>`;
    root.querySelector('#juggler-model').addEventListener('change', event => {
      saveDraft(root); selectedId = event.target.value; render(root, data);
    });
    root.querySelectorAll('[data-juggler-tab]').forEach(button => {
      button.addEventListener('click', () => {
        saveDraft(root); selectedTab = button.dataset.jugglerTab; render(root, data);
        root.querySelector('#juggler-tab-' + selectedTab).focus();
      });
      button.addEventListener('keydown', event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const tabs = ['specs', 'grapes', 'settings'];
        const index = tabs.indexOf(selectedTab);
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? 2 : (index + (event.key === 'ArrowRight' ? 1 : 2)) % tabs.length;
        saveDraft(root); selectedTab = tabs[next]; render(root, data);
        root.querySelector('#juggler-tab-' + selectedTab).focus();
      });
    });
    const form = root.querySelector('.juggler-input-form');
    if (form) {
      const draft = drafts.get(selectedId + ':' + form.dataset.tool);
      if (draft) Object.entries(draft).forEach(([name, value]) => { form.elements.namedItem(name).value = value; });
      form.addEventListener('input', () => saveDraft(root));
      // Enter and form submission cannot call an estimator or reload the page.
      form.addEventListener('submit', event => event.preventDefault());
    }
  }

  async function mount(root) {
    root.innerHTML = '<section class="card"><p>ジャグラーの公式資料を読み込み中…</p></section>';
    try {
      const data = await catalog();
      if (root.isConnected) render(root, data);
    } catch (error) {
      if (root.isConnected) root.innerHTML = `<section class="card"><p>${escape(error.message)}</p></section>`;
    }
  }

  // There is deliberately no call to reverseGrapes / estimateSettings in this
  // module. Functions are available for a later, explicit execution feature.
  window.JokersJuggler = Object.freeze({mount, get calculators() { return calculators; }});
})();
