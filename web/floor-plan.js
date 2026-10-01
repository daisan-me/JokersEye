'use strict';

// Preserve the published plan's geometry. Never distribute numbers around
// invented arcs: an unknown coordinate stays off the physical layer.
const FloorPlan = (() => {
  const esc = v => String(v ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let viewport = [0,0,640,360];
  function render(data, view, color) {
    const positions = new Map(data.geometry.positions.map(p=>[p.seat,p]));
    const changed = new Set(data.period.changes.map(c=>c.seat));
    const date = data.metadata.mapImageDate.replaceAll('-','/');
    const seats = data.seats;
    return `<div class="physical-plan">
      <div class="floor-heading"><div><div class="kicker">TOP VIEW / GOTHAM CITY</div><h3>実際のフロア図</h3></div><span class="tag">位置登録 ${positions.size} / ${seats.length} 台</span></div>
      <p class="floor-warning">図の形・入口・通路は公開図のまま。背景に印刷された機種名は ${date} ごろの参考資料で、選択期間の機種ではありません。重ねた番号・機種だけが選択期間のデータです。</p>
      <div class="floor-tools" role="group" aria-label="フロア図の拡大"><button type="button" data-zoom="in" aria-label="拡大">＋</button><button type="button" data-zoom="out" aria-label="縮小">−</button><button type="button" data-zoom="reset">全体表示</button><span class="hint">ドラッグで移動 · Ctrl＋ホイールで拡大</span></div>
      <svg class="physical-floor-svg" viewBox="${viewport.join(' ')}" role="img" aria-label="ゴッサムシティを真上から見たフロア図。位置未登録の台は重ねていません。">
        <image href="/floor-map.webp" width="640" height="360" class="floor-source-image"/>
        ${seats.filter(s=>positions.has(s.seat)).map(s=>{
          const p=positions.get(s.seat), x=p.x*640,y=p.y*360;
          return `<g class="machine-seat physical-seat model-color-${color(s.model)} ${changed.has(s.seat)?'seat-changed':''}" data-seat="${esc(s.seat)}" role="button" tabindex="0" aria-label="${esc(s.seat)}番 ${esc(view==='numbers'?'':s.model)} · 利用者登録位置" transform="translate(${x} ${y})"><title>${esc(s.seat)}番 · ${esc(s.model)}\n位置根拠：${esc(p.evidence)}</title><rect x="-4.5" y="-4" width="9" height="8" rx="1" transform="rotate(${p.angle})"/><text class="physical-number" y="1.5" text-anchor="middle">${esc(s.seat)}</text>${view==='machine'?`<text class="physical-model" y="-7" text-anchor="middle">${esc(s.model)}</text>`:''}</g>`;
        }).join('')}
        <g id="floor-draft" hidden><circle r="5"/><path d="M -9 0 H 9 M 0 -9 V 9"/></g>
      </svg>
      <p class="floor-status">${positions.size ? '重ねた台位置は利用者の根拠付き登録です。自動照合・現地検証済みの表示ではありません。' : '台番号の物理位置は未照合です。円形の島に推測で番号を割り振っていません。'} 過去期間の島形状も、この図だけでは確定できません。</p>
      <details class="floor-calibration"><summary>実位置を登録・修正する</summary><p>台番号を入力して、下の「位置指定」を有効にし、フロア図上の実際の台をクリックします。保存範囲は選択中の期間だけです。写真・現地確認など、その期間の位置が分かる根拠が必要です。</p>
        <form id="floor-position-form"><div class="floor-fields"><label>台番号<input name="seat" type="number" min="1" max="9999" required placeholder="例：152"></label><label>台の向き（度）<input name="angle" type="number" min="-180" max="180" value="0" required></label><label class="floor-pick-toggle"><input name="pick" type="checkbox">図をクリックして位置指定</label></div>
        <label>位置を確認した根拠<textarea name="evidence" required maxlength="2000" placeholder="確認日、写真・図面の出典URL、現地確認した台番号など"></textarea></label><div class="floor-tools"><button type="submit">この期間の位置を保存</button><button type="button" id="floor-remove">この台の位置登録を解除</button><span id="floor-position-status" role="status">位置未選択</span></div></form>
      </details>
      <details class="unlocated-seats" ${positions.size?'':'open'}><summary>位置未照合の台 · ${seats.length-positions.size} 台（実位置ではない一覧）</summary><div class="unlocated-list">${seats.filter(s=>!positions.has(s.seat)).map(s=>`<button type="button" class="machine-seat model-color-${color(s.model)} ${changed.has(s.seat)?'seat-changed':''}" data-seat="${esc(s.seat)}" title="${esc(s.seat)}番 · ${esc(s.model)} · 位置未照合"><span class="seat-number">${esc(s.seat)}</span><span class="seat-model">${esc(s.model)}</span><small>位置未照合</small></button>`).join('')}</div></details>
    </div>`;
  }
  function bind(root, data, api, reload, select) {
    const svg=root.querySelector('.physical-floor-svg');
    if (!svg) return;
    const form=root.querySelector('#floor-position-form'), status=root.querySelector('#floor-position-status');
    let draft=null, drag=null;
    const point=e=>new DOMPoint(e.clientX,e.clientY).matrixTransform(svg.getScreenCTM().inverse());
    const redraw=()=>svg.setAttribute('viewBox',viewport.join(' '));
    const zoom=f=>{
      const width=Math.max(100,Math.min(640,viewport[2]*f)), height=width*360/640;
      viewport=[viewport[0]+(viewport[2]-width)/2,viewport[1]+(viewport[3]-height)/2,width,height];
      clamp();
    };
    const clamp=()=>{
      viewport[0]=Math.max(0,Math.min(640-viewport[2],viewport[0]));
      viewport[1]=Math.max(0,Math.min(360-viewport[3],viewport[1]));
      redraw();
    };
    root.querySelectorAll('[data-zoom]').forEach(b=>b.addEventListener('click',()=>{
      if(b.dataset.zoom==='reset'){viewport=[0,0,640,360];redraw();}else zoom(b.dataset.zoom==='in'?0.7:1/0.7);
    }));
    svg.addEventListener('wheel',e=>{if(e.ctrlKey){e.preventDefault();zoom(e.deltaY>0?1.15:1/1.15);}}, {passive:false});
    svg.addEventListener('pointerdown',e=>{
      if(e.button!==0 || e.target.closest('[data-seat]') || form.elements.pick.checked)return;
      drag={start:point(e),box:[...viewport],clientX:e.clientX,clientY:e.clientY};svg.setPointerCapture(e.pointerId);
    });
    svg.addEventListener('pointermove',e=>{
      if(!drag)return;
      const scale=drag.box[2]/svg.getBoundingClientRect().width;
      viewport=[drag.box[0]-(e.clientX-drag.clientX)*scale,drag.box[1]-(e.clientY-drag.clientY)*scale,drag.box[2],drag.box[3]];clamp();
    });
    svg.addEventListener('pointerup',()=>{drag=null;});svg.addEventListener('pointercancel',()=>{drag=null;});
    const setDraft=p=>{
      draft=p;const marker=root.querySelector('#floor-draft');
      marker.removeAttribute('hidden');marker.setAttribute('transform',`translate(${p.x*640} ${p.y*360})`);
      status.textContent=`位置選択済み (${(p.x*100).toFixed(1)}%, ${(p.y*100).toFixed(1)}%) · 未保存`;
    };
    svg.addEventListener('click',e=>{
      if(!form.elements.pick.checked)return;
      const p=point(e);if(p.x<0||p.x>640||p.y<0||p.y>360)return;
      setDraft({x:p.x/640,y:p.y/360});
    });
    form.elements.seat.addEventListener('input',()=>{
      draft=null;root.querySelector('#floor-draft').setAttribute('hidden','');status.textContent='位置未選択';
      const p=data.geometry.positions.find(p=>p.seat===String(Number(form.elements.seat.value)));
      if(p){setDraft(p);form.elements.angle.value=p.angle;form.elements.evidence.value=p.evidence;}
      else {form.elements.evidence.value='';form.elements.angle.value='0';}
    });
    let saving=false;
    async function save(remove=false){
      if(saving)return;
      if(!remove && !draft){status.textContent='先に図上の位置を選んでください。';return;}
      saving=true;form.querySelectorAll('button').forEach(b=>b.disabled=true);
      try{
        await api('map/position',{period:data.period.id,imageRevision:data.geometry.imageRevision,seat:form.elements.seat.value,angle:Number(form.elements.angle.value),evidence:form.elements.evidence.value,...draft,remove});
        await reload();
      }catch(e){status.textContent=e.message;}
      finally{saving=false;form.querySelectorAll('button').forEach(b=>b.disabled=false);}
    }
    form.addEventListener('submit',e=>{e.preventDefault();save();});
    root.querySelector('#floor-remove').addEventListener('click',()=>save(true));
    root.querySelectorAll('.physical-seat').forEach(g=>g.addEventListener('keydown',e=>{
      if(e.key==='Enter'||e.key===' '){e.preventDefault();select(g.dataset.seat);}
    }));
  }
  return {render,bind};
})();
