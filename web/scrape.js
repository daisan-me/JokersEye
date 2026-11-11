'use strict';
/* The one scraping sheet 「公開データを取得する」, in the Scraper tab. Results are saved in GDB
   (GothamDataBase.sqlite) and shown in the データ管理 tab (「登録した観測」 and the GDB sheet). While a run
   is going its state is polled on every tab, so those sheets lock their buttons and refresh when it ends. */
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
    return `前回の${status.mode==='rescrape'?'再スクレイプ':'取得'}：${states[status.state]||status.state} · 所要 ${duration(status.elapsedSeconds)} · ${status.daysDone===null||status.daysDone===undefined?'—':number(status.daysDone)} 日処理 · 1日あたり ${duration(status.secondsPerDay)}`;
  }
  // finalRun: the last finished run seen. The first poll only records it, so opening the page does not reload everything again.
  let timer=null, busy=false, finalRun;
  function mount(){
    const host=document.querySelector('#scraper-content');
    if(!host||host.querySelector('#scrape-widget'))return poll();  // other tabs: report the run's state once
    host.append(optionsSheet());
    const panel=document.createElement('section');panel.id='scrape-widget';panel.className='card';
    panel.innerHTML=`<div class="kicker">PUBLIC DATA / NO AI TOKENS</div><h2>公開データを取得する</h2>
      <p>取得したデータはGDB（GothamDataBase）に保存し、「データ管理」タブの「GothamDataBase」と「登録した観測」で確認できます。BB・RBも取得します。公開ページを読む処理にOpenAI APIは使用しません。</p>
      <div class="actions"><button id="scrape-today" class="primary">本日までの分を更新</button><button id="scrape-stop" class="secondary" disabled>取得を停止</button></div>
      <p class="hint">GDBに記録されている最後の日から本日（日本時間）までを取得します。記録がまだない場合は2024/03/01から取得します。</p>
      <div class="toolbar scrape-range"><label>開始日<input id="scrape-from" type="date" min="2024-03-01"></label><label>終了日<input id="scrape-to" type="date" min="2024-03-01"></label><button id="scrape-range-start" class="secondary">期間を指定して取得</button><button id="scrape-rescrape" class="secondary">再スクレイプ</button></div>
      <p class="hint">期間内で、記録がない日は全台表とBB・RBを取得し、BB・RBが空の日はそこだけ補完します。揃っている日・機種は再取得しません。まず1週間ほどで試してから広げると、取り方の問題に早く気づけます。</p>
      <p class="hint">「再スクレイプ」は、同じ期間の保存済みの日を読み直し、ロックしていない行の「-」のセルだけを埋めます。値が入っているセルは書き換えません。ロックした日・行は読みません。</p>
      <p id="scrape-progress" role="status">取得状況を確認中…</p><p id="scrape-run" class="hint"></p><div id="scrape-stop-reason" hidden></div>
<progress id="scrape-meter" value="0" max="1" hidden></progress><div id="scrape-summary" class="hint"></div>
      <details id="scrape-failures"><summary>欠落・未掲載・失敗（最新40日）</summary><div></div></details>
      <p class="hint">全台表で「-」と表示された値は「-」として記録します（隠されたものか、値がないものかは区別しません）。BB/RBがまだ取れていない欄は「未取得」で、0ではありません。ロックした日は取得しません。取得元に制限や確認画面が出た場合は停止します。取得用ブラウザーを閉じると停止します。</p>`;
    host.append(panel);
    const today=new Date(Date.now()+9*3600*1000).toISOString().slice(0,10);  // Japan time, as the server
    panel.querySelectorAll('#scrape-from,#scrape-to').forEach(input=>{input.max=today;});
    panel.querySelector('#scrape-today').addEventListener('click',()=>start('gdb/update',{}));
    // Only a start date means that one day, as in 「削除・ロック」.
    const range=()=>{const start=panel.querySelector('#scrape-from').value||null;return {start,end:panel.querySelector('#scrape-to').value||start};};
    panel.querySelector('#scrape-range-start').addEventListener('click',()=>start('gdb/range',range()));
    panel.querySelector('#scrape-rescrape').addEventListener('click',()=>start('gdb/rescrape',range()));
    panel.querySelector('#scrape-stop').addEventListener('click',async()=>{try{await api('scrape/stop',{reason:'button'});await poll();}catch(e){showError(e);}});
    poll();
  }
  // 「取得の設定」 sheet: how many source-browser windows read pages and how fast each may go.
  function optionsSheet(){
    const sheet=document.createElement('section');sheet.id='scrape-options';sheet.className='card';
    sheet.innerHTML=`<div class="kicker">SPEED</div><h2>取得の設定</h2>
      <div class="toolbar scrape-options"><label>画面の数<select id="scrape-windows"></select></label><label>画面あたりの速さ<select id="scrape-rate"></select></label><label>表示するモニター<select id="scrape-screen"></select></label><button id="scrape-screens-reload" class="secondary" type="button">モニターを読み直す</button>
      <p id="scrape-speed" class="scrape-speed" role="status">読み込み中…</p></div>
      <p class="hint">取得用ブラウザーを、選んだモニターに、選んだ数に分けて並べます（重ならない配置）。取得用ブラウザーはキーボードの操作を奪わずに開くので、動画などを見ていても邪魔をしません。サブモニターを選ぶと、メインの画面をふさぎません。モニターの一覧はアプリの起動時に読みます。あとからつないだ・外したときは「モニターを読み直す」を押してください。速さは1画面が1秒に読み始めるページの上限で、全体の上限は「画面あたりの速さ × 画面の数」です。実際の速さはページの表示にかかる時間で決まり、サイトの応答が遅いときは同時に読む画面を自動で減らします。</p>
      <p class="hint">速くするほど公開サイトの負担が増え、アクセス制限（停止事由3）やサイトのエラー（停止事由4）、「-」が出やすくなります。少しずつ上げて、取れ方を確かめてください。取得中は変えられません。</p>`;
    sheet.querySelectorAll('select').forEach(select=>select.addEventListener('change',saveOptions));
    sheet.querySelector('#scrape-screens-reload').addEventListener('click',async event=>{
      const button=event.currentTarget;button.disabled=true;
      try{await api('scrape/screens/refresh',{});await new Promise(done=>setTimeout(done,800));await poll();notice('モニターの一覧を読み直しました。');}
      catch(e){showError(e);}finally{button.disabled=false;}
    });
    return sheet;
  }
  // Monitors as the desktop app reported them; the main one is at 0,0.
  function screenLabel(s,i){return `モニター${i+1}${s.x===0&&s.y===0?'（メイン）':''}：${s.width}×${s.height}（位置 ${s.x}, ${s.y}）`;}
  function screenOptions(o){
    const known=o.screens.map((s,i)=>[s.id,screenLabel(s,i)]);
    if(o.screen&&!o.screens.some(s=>s.id===o.screen))known.push([o.screen,'選んだモニター（今は見つかりません。メインの画面に並べます）']);
    return [['','これまでどおり（メインの画面の左上から）'],...known];
  }
  function speedText(o){return `${o.rate} 枚/秒 × ${o.windows} 画面 = 最大 ${o.pagesPerSecond} 枚/秒（${number(o.pagesPerSecond*60)} 枚/分）`;}
  function showOptions(options,running){
    const sheet=document.querySelector('#scrape-options');if(!sheet||!options)return;
    const windows=sheet.querySelector('#scrape-windows'),rate=sheet.querySelector('#scrape-rate'),screen=sheet.querySelector('#scrape-screen');
    if(!windows.options.length){
      windows.innerHTML=options.windowChoices.map(n=>`<option value="${n}">${n}分割（${n}画面）</option>`).join('');
      rate.innerHTML=options.rateChoices.map(n=>`<option value="${n}">毎秒 ${n} 枚</option>`).join('');
    }
    const choices=screenOptions(options),html=choices.map(([id,label])=>`<option value="${esc(id)}">${esc(label)}</option>`).join('');
    if(screen.dataset.html!==html&&document.activeElement!==screen){screen.innerHTML=html;screen.dataset.html=html;}  // monitors plugged in or out
    if(document.activeElement!==windows)windows.value=String(options.windows);
    if(document.activeElement!==rate)rate.value=String(options.rate);
    if(document.activeElement!==screen)screen.value=options.screen;
    windows.disabled=rate.disabled=screen.disabled=running;
    sheet.querySelector('#scrape-speed').textContent=speedText(options);
  }
  async function saveOptions(){
    const sheet=document.querySelector('#scrape-options');if(!sheet)return;
    try{showOptions(await api('scrape/options',{windows:Number(sheet.querySelector('#scrape-windows').value),rate:Number(sheet.querySelector('#scrape-rate').value),screen:sheet.querySelector('#scrape-screen').value}),busy);}
    catch(e){showError(e);await poll();}  // puts the saved choice back
  }
  // Poll every 2 seconds while the Scraper tab is open or a run is going; otherwise stop.
  function schedule(keep){
    if(keep&&!timer)timer=setInterval(poll,2000);
    if(!keep&&timer){clearInterval(timer);timer=null;}
  }
  // What a re-read changed (filled values, saved values that changed, BB/RB kept or read again) and locked days left alone.
  function refillRecord(status){
    const r=status.refill, extra=[];
    if(r)extra.push(`取り直し ${number(r.days)} 日 · 埋まった値 ${number(r.filled)} 件 · 既存値の変化 ${number(r.changed)} 件 · BB/RB保持 ${number(r.bonusKept)} 台${r.bonusReset?` · BB/RB取り直し ${number(r.bonusReset)} 台`:''}`);
    if(status.lockedDays)extra.push(`ロック中のため取得しなかった日 ${number(status.lockedDays)} 日`);
    return extra.join(' / ');
  }
  async function start(path,payload){
    if(busy)return;busy=true;
    document.querySelectorAll('#scrape-today,#scrape-range-start,#scrape-rescrape').forEach(b=>b.disabled=true);
    try{
      const result=await api(path,payload);
      if(result.status==='up-to-date'){busy=false;notice(path==='gdb/update'?'本日まで取得済みです。新しく取得する日はありません。':path==='gdb/rescrape'?'指定した期間に、ロックしていない行の「-」はありません。':'指定した期間に取得が必要な日はありません（揃っている日は再取得しません）。');}
      else if(path==='gdb/rescrape')notice(`再スクレイプを始めました：${result.plan.dateCount}日・「-」を含む${result.plan.rows}行が対象です。`);
      await poll();
    }catch(e){showError(e);busy=false;await poll();}
  }
  async function poll(){
    const panel=document.querySelector('#scrape-widget');
    let status;
    try{status=await api('scrape/status');}
    catch(e){if(panel)panel.querySelector('#scrape-progress').textContent=e.message;else schedule(false);return;}
    const running=status.active || status.state==='running';
    busy=running;
    schedule(running||!!panel);
    if(panel)show(panel,status,running);
    showOptions(status.options,running);
    if(finalRun===undefined)finalRun=running?null:status.id;  // a run going at startup still counts as new when it ends
    else if(!running && finalRun!==status.id){
      finalRun=status.id;
      document.dispatchEvent(new CustomEvent('jokers:data-changed'));  // app.js refreshes the counts and 「登録した観測」, gdb.js the GDB view
    }
    document.dispatchEvent(new CustomEvent('jokers:scrape-state',{detail:{running}}));
  }
  function show(panel,status,running){
      panel.querySelectorAll('#scrape-today,#scrape-range-start,#scrape-rescrape').forEach(b=>b.disabled=running);
      panel.querySelector('#scrape-stop').disabled=!running;
      panel.querySelector('#scrape-progress').textContent=status.message||'取得待機中';
      panel.querySelector('#scrape-run').textContent=[runRecord(status,running),refillRecord(status)].filter(Boolean).join(' / ');
      const reason=panel.querySelector('#scrape-stop-reason');  // 停止事由 with its number, in red
      reason.hidden=running||!status.stopReason;
      reason.className='stop-reason';
      reason.innerHTML=reason.hidden?'':`<span class="stop-code">${esc(status.stopCode??'?')}</span><div><strong>停止事由 ${esc(status.stopCode??'?')}：${esc(status.stopReason)}</strong><small>${esc(status.message||'')}</small></div>`;
      const meter=panel.querySelector('#scrape-meter');meter.hidden=!running;meter.max=Math.max(1,status.total||1);meter.value=status.completed||0;
      const s=status.summary;
      panel.querySelector('#scrape-summary').textContent=`GDB ${number(s.records)} 行 / ${number(s.days)} 日 · 最終データ ${s.last||'なし'} · BB/RB未取得 ${number(s.missingBonuses)} 行${running?' · 進捗 '+number(status.completed)+' / '+number(status.total)+' 日'+(status.bonusTotal?' · BB/RB '+number(status.bonusRows)+' / '+number(status.bonusTotal)+' 台':'')+(status.parallel?' · 同時 '+status.parallel+' 画面':'')+' · 直近1分 '+number(status.pagesLastMinute)+' 枚':''}`;
      panel.querySelector('#scrape-failures>div').innerHTML=[...status.failures.map(f=>`<p>${esc(f.day)} · ${esc(({failed:'取得失敗',partial:'全台未確認','not-published':'公開一覧に未掲載'})[f.status]||f.status)}<br>${esc(f.message)}</p>`),...(status.bonusFailures||[]).map(f=>`<p>${esc(f.day)} · BB/RB追加取得 ${esc(f.status)}（${number(f.rows)}台確認済み）<br>${esc(f.message)}</p>`)].join('')||'<p>記録なし</p>';
  }
  new MutationObserver(mount).observe(document.querySelector('#content'),{childList:true});
  mount();
})();
