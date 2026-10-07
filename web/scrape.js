'use strict';
/* The one scraping panel. Results are saved in GDB (GothamDataBase.sqlite) and shown in
   「登録した観測」 and the GDB view; both are refreshed when a run ends. */
(() => {
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const number=v=>Number(v||0).toLocaleString('ja-JP');
  // 1時間2分3秒 / 2分3秒 / 3.4秒
  const duration=s=>{if(s===null||s===undefined)return '—';if(s<60)return `${Math.round(s*10)/10}秒`;s=Math.round(s);const h=Math.floor(s/3600),m=Math.floor(s%3600/60),r=s%60;return (h?`${h}時間`:'')+`${m}分`+(h?'':`${r}秒`);};
  const states={complete:'完了',stopped:'停止',failed:'失敗（停止）',interrupted:'中断',running:'取得中'};
  // Time taken, days done, time per day and (when stopped) the reason; while running, the time left.
  function runRecord(status,running){
    if(running)return `経過 ${duration(status.elapsedSeconds)} · ${number(status.daysDone)} 日処理 · 1日あたり ${duration(status.secondsPerDay)}${status.remainingSeconds?` · 残り約 ${duration(status.remainingSeconds)}`:''}`;
    if(!status.state||status.state==='idle')return '';
    return `前回の取得：${states[status.state]||status.state} · 所要 ${duration(status.elapsedSeconds)} · ${status.daysDone===null||status.daysDone===undefined?'—':number(status.daysDone)} 日処理 · 1日あたり ${duration(status.secondsPerDay)}`;
  }
  let timer=null, busy=false;
  function mount(){
    const content=document.querySelector('#content');
    if(!content.querySelector('#observations')||content.querySelector('#scrape-widget'))return;
    const panel=document.createElement('section');panel.id='scrape-widget';panel.className='card';
    panel.innerHTML=`<div class="kicker">PUBLIC DATA / NO AI TOKENS</div><h2>公開データを取得する</h2>
      <p>取得したデータはGDB（GothamDataBase）に保存し、下の「登録した観測」で確認できます。BB・RBも取得します。公開ページを読む処理にOpenAI APIは使用しません。</p>
      <div class="actions"><button id="scrape-today" class="primary">本日までの分を更新</button><button id="scrape-stop" class="secondary" disabled>取得を停止</button></div>
      <p class="hint">GDBに記録されている最後の日から本日（日本時間）までを取得します。記録がまだない場合は2024/03/01から取得します。</p>
      <div class="toolbar scrape-range"><label>開始日<input id="scrape-from" type="date" min="2024-03-01"></label><label>終了日<input id="scrape-to" type="date" min="2024-03-01"></label><button id="scrape-range-start" class="secondary">期間を指定して取得</button></div>
      <p class="hint">期間内で、記録がない日は全台表とBB・RBを取得し、BB・RBが空の日はそこだけ補完します。揃っている日・機種は再取得しません。まず1週間ほどで試してから広げると、取り方の問題に早く気づけます。</p>
      <p id="scrape-progress" role="status">取得状況を確認中…</p><p id="scrape-run" class="hint"></p><div id="scrape-stop-reason" hidden></div><progress id="scrape-meter" value="0" max="1" hidden></progress><div id="scrape-summary" class="hint"></div>
      <details id="scrape-failures"><summary>欠落・未掲載・失敗（最新40日）</summary><div></div></details>
      <p class="hint">公開表にない値は空欄で保存します。全台表にBB/RBがない場合、その欄は未取得のままで、0ではありません。差枚の「−」も欠測です。取得元に制限や確認画面が出た場合は停止します。取得用ブラウザーを閉じると停止します。</p>`;
    content.prepend(panel);
    const today=new Date(Date.now()+9*3600*1000).toISOString().slice(0,10);  // Japan time, as the server
    panel.querySelectorAll('#scrape-from,#scrape-to').forEach(input=>{input.max=today;});
    panel.querySelector('#scrape-today').addEventListener('click',()=>start('gdb/update',{}));
    panel.querySelector('#scrape-range-start').addEventListener('click',()=>start('gdb/range',{start:panel.querySelector('#scrape-from').value||null,end:panel.querySelector('#scrape-to').value||null}));
    panel.querySelector('#scrape-stop').addEventListener('click',async()=>{try{await api('scrape/stop',{reason:'button'});await poll();}catch(e){showError(e);}});
    poll();
    if(!timer)timer=setInterval(poll,2000);
  }
  async function start(path,payload){
    if(busy)return;busy=true;
    document.querySelectorAll('#scrape-today,#scrape-range-start').forEach(b=>b.disabled=true);
    try{
      const result=await api(path,payload);
      if(result.status==='up-to-date'){busy=false;notice(path==='gdb/update'?'本日まで取得済みです。新しく取得する日はありません。':'指定した期間に取得が必要な日はありません（揃っている日は再取得しません）。');}
      await poll();
    }catch(e){showError(e);busy=false;await poll();}
  }
  async function poll(){
    const panel=document.querySelector('#scrape-widget');if(!panel)return;
    try{
      const status=await api('scrape/status'),running=status.active || status.state==='running';
      busy=running;
      panel.querySelectorAll('#scrape-today,#scrape-range-start').forEach(b=>b.disabled=running);
      panel.querySelector('#scrape-stop').disabled=!running;
      panel.querySelector('#scrape-progress').textContent=status.message||'取得待機中';
      panel.querySelector('#scrape-run').textContent=runRecord(status,running);
      const reason=panel.querySelector('#scrape-stop-reason');  // 停止事由 with its number, in red
      reason.hidden=running||!status.stopReason;
      reason.className='stop-reason';
      reason.innerHTML=reason.hidden?'':`<span class="stop-code">${esc(status.stopCode??'?')}</span><div><strong>停止事由 ${esc(status.stopCode??'?')}：${esc(status.stopReason)}</strong><small>${esc(status.message||'')}</small></div>`;
      const meter=panel.querySelector('#scrape-meter');meter.hidden=!running;meter.max=Math.max(1,status.total||1);meter.value=status.completed||0;
      const s=status.summary;
      panel.querySelector('#scrape-summary').textContent=`GDB ${number(s.records)} 行 / ${number(s.days)} 日 · 最終データ ${s.last||'なし'} · BB/RB未取得・欠測 ${number(s.missingBonuses)} 行 · 差枚欠測 ${number(s.missingNet)} 行${running?' · 進捗 '+number(status.completed)+' / '+number(status.total)+' 日'+(status.bonusTotal?' · BB/RB '+number(status.bonusRows)+' / '+number(status.bonusTotal)+' 台':'')+(status.parallel?' · 同時 '+status.parallel+' 枚':''):''}`;
      panel.querySelector('#scrape-failures>div').innerHTML=[...status.failures.map(f=>`<p>${esc(f.day)} · ${esc(({failed:'取得失敗',partial:'全台未確認','not-published':'公開一覧に未掲載'})[f.status]||f.status)}<br>${esc(f.message)}</p>`),...(status.bonusFailures||[]).map(f=>`<p>${esc(f.day)} · BB/RB追加取得 ${esc(f.status)}（${number(f.rows)}台確認済み）<br>${esc(f.message)}</p>`)].join('')||'<p>記録なし</p>';
      if(!running && panel.dataset.finalRun!==status.id){
        panel.dataset.finalRun=status.id;await refresh();
        const count=document.querySelector('#observations')?.closest('section')?.querySelector('.card-header .tag');if(count)count.textContent=number(state.summary.records)+' RECORDS';
        await loadObservations();
        document.dispatchEvent(new CustomEvent('jokers:data-changed'));
      }
    }catch(e){panel.querySelector('#scrape-progress').textContent=e.message;}
  }
  new MutationObserver(mount).observe(document.querySelector('#content'),{childList:true});
  mount();
})();
