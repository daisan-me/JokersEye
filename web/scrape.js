'use strict';
(() => {
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const number=v=>Number(v||0).toLocaleString('ja-JP');
  let timer=null, busy=false, includeBonus=false;
  function mount(){
    const content=document.querySelector('#content');
    if(!content.querySelector('#import-form')||content.querySelector('#scrape-widget'))return;
    const panel=document.createElement('section');panel.id='scrape-widget';panel.className='card';
    panel.innerHTML=`<div class="kicker">PUBLIC DATA / NO AI TOKENS</div><h2>公開データを取得する</h2><p>最後に取得した日から本日（日本時間）までを追加します。過去の取得失敗・未掲載日も再確認します。公開ページを読む処理にOpenAI APIは使用しません。</p><div class="actions"><button id="scrape-today" class="primary">本日までのデータをスクレイプ</button><button id="scrape-stop" class="secondary" disabled>取得を停止</button><a class="secondary" href="/api/scrape/csv" download="gotham-city.csv">取得済みデータをCSVで保存 ↓</a></div><details class="scrape-range"><summary>初回の取得範囲を指定</summary><div class="toolbar"><label>開始日<input id="scrape-from" type="date" value="2023-04-27" min="2023-04-27"></label><label>終了日<input id="scrape-to" type="date" value="2026-09-01" min="2023-04-27"></label><button id="scrape-range-start" class="secondary">指定範囲を取得</button></div></details><p id="scrape-progress" role="status">取得状況を確認中…</p><progress id="scrape-meter" value="0" max="1" hidden></progress><div id="scrape-summary" class="hint"></div><details id="scrape-failures"><summary>欠落・未掲載・失敗（最新40日）</summary><div></div></details><p class="hint">公開表にない値は空欄で保存します。全台表にBB/RBがない場合、その欄は未取得のままで、0ではありません。差枚の「−」も欠測です。取得元に制限や確認画面が出た場合は停止します。取得用ブラウザーを閉じると停止します。</p>`;
    content.prepend(panel);
    panel.querySelector('.actions').insertAdjacentHTML('afterend','<label class="hint"><input id="scrape-bonus" type="checkbox"> BB・RBも取得（機種別一覧を追加巡回するため時間がかかります。負荷抑制のため間隔を長くします）</label>');
    panel.querySelector('#scrape-bonus').checked=includeBonus;
    panel.querySelector('#scrape-bonus').addEventListener('change',e=>{includeBonus=e.target.checked;});
    panel.querySelector('#scrape-today').addEventListener('click',()=>start({}));
    panel.querySelector('#scrape-range-start').addEventListener('click',()=>start({start:panel.querySelector('#scrape-from').value,end:panel.querySelector('#scrape-to').value}));
    panel.querySelector('#scrape-stop').addEventListener('click',async()=>{try{await api('scrape/stop',{});await poll();}catch(e){showError(e);}});
    poll();
    if(!timer)timer=setInterval(poll,2000);
  }
  async function start(values){
    if(busy)return;busy=true;
    document.querySelectorAll('#scrape-today,#scrape-range-start').forEach(b=>b.disabled=true);
    try{await api('scrape/start',{...values,include_bonus:includeBonus});await poll();}catch(e){showError(e);busy=false;await poll();}
  }
  async function poll(){
    const panel=document.querySelector('#scrape-widget');if(!panel)return;
    try{
      const status=await api('scrape/status'),running=status.state==='running';
      busy=running;
      panel.querySelectorAll('#scrape-today,#scrape-range-start').forEach(b=>b.disabled=running);
      panel.querySelector('#scrape-stop').disabled=!running;
      panel.querySelector('#scrape-bonus').disabled=running;
      panel.querySelector('#scrape-progress').textContent=status.message||'取得待機中';
      const meter=panel.querySelector('#scrape-meter');meter.hidden=!running;meter.max=Math.max(1,status.total||1);meter.value=status.completed||0;
      const s=status.summary;
      panel.querySelector('#scrape-summary').textContent=`取得済み ${number(s.records)} 行 / ${number(s.days)} 日 · 最終データ ${s.last||'なし'} · BB/RB未取得・欠測 ${number(s.missingBonuses)} 行 · 差枚欠測 ${number(s.missingNet)} 行${running?' · 進捗 '+number(status.completed)+' / '+number(status.total)+' 日':''}`;
      panel.querySelector('#scrape-failures>div').innerHTML=[...status.failures.map(f=>`<p>${esc(f.day)} · ${esc(({failed:'取得失敗',partial:'全台未確認','not-published':'公開一覧に未掲載'})[f.status]||f.status)}<br>${esc(f.message)}</p>`),...(status.bonusFailures||[]).map(f=>`<p>${esc(f.day)} · BB/RB追加取得 ${esc(f.status)}（${number(f.rows)}台確認済み）<br>${esc(f.message)}</p>`)].join('')||'<p>記録なし</p>';
      if(!running && panel.dataset.finalRun!==status.id){panel.dataset.finalRun=status.id;await refresh();const count=document.querySelector('#observations')?.closest('section')?.querySelector('.card-header .tag');if(count)count.textContent=number(state.summary.records)+' RECORDS';await loadObservations();}
    }catch(e){panel.querySelector('#scrape-progress').textContent=e.message;}
  }
  new MutationObserver(mount).observe(document.querySelector('#content'),{childList:true});
  mount();
})();
