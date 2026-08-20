
const API='/api/plugins/action';
function toast(msg){const t=qs('#toast');if(!t)return;t.textContent=msg;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),1800)}
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function qs(id){return document.getElementById(id)}
function qsa(s){return document.querySelectorAll(s)}

async function api(path,opts={}){const r=await fetch(path,Object.assign({headers:{'Content-Type':'application/json'}},opts));return r.json()}
function log(msg){const el=qs('#ego-log');if(!el)return;const line=new Date().toLocaleTimeString()+': '+String(msg);el.textContent=(el.textContent?el.textContent+'\n':'')+line;el.scrollTop=el.scrollHeight}

const wizard={
  el(){return qs('#ego-wizard')},
  show(){const el=this.el(); if(el) el.style.display='flex';},
  hide(){const el=this.el(); if(el) el.style.display='none';},
  step:1,total:4,
  dots(){return qsa('#wizard-progress .wizard-dot');},
  steps(){return qsa('.wizard-step');},
  render(){
    this.steps().forEach((el,i)=>{ el.classList.toggle('active', i+1===this.step); });
    const dots=this.dots(); dots.forEach((d,i)=>{ d.classList.remove('done','current'); if(i+1<this.step) d.classList.add('done'); if(i+1===this.step) d.classList.add('current'); });
    qs('#wizard-btn-prev').disabled=this.step===1;
    if(this.step===this.total){ qs('#wizard-btn-next').style.display='none'; qs('#wizard-btn-save').style.display='inline-block'; this.renderSummary(); }
    else { qs('#wizard-btn-next').style.display='inline-block'; qs('#wizard-btn-save').style.display='none'; }
  },
  next(){ if(this.step<this.total){ this.step++; this.render(); }},
  prev(){ if(this.step>1){ this.step--; this.render(); }},
  collect(){
    const s={};
    ['w-name','w-basic','w-personality','w-daily','w-catch','w-forbid','w-style','w-apikey','w-model'].forEach(id=>{ const el=qs('#'+id); if(el) s[id]=el.value||''; });
    const provCard=document.querySelector('#wizard-provider-grid .card.selected');
    const protoCard=document.querySelector('#wizard-proto-grid .card.selected');
    return Object.assign(s,{
      provider: provCard?provCard.dataset.value:'',
      proto: protoCard?protoCard.dataset.value:'',
      reply_mode: qs('#w-reply-mode')?.value||'always',
      group_mode: qs('#w-group-mode')?.value||'all',
      enabled: !!qs('#w-enable')?.checked,
      example_enabled: !!qs('#w-example')?.checked,
    });
  },
  buildPersonalitySections(x){
    const sections={};
    if(x['w-name']) sections['【你是谁】']=[x['w-name'],x['w-basic']].filter(Boolean).join('，');
    if(x['w-personality']) sections['【性格】']=x['w-personality'];
    if(x['w-daily']) sections['【日常】']=x['w-daily'];
    if(x['w-style']) sections['【说话方式】']=x['w-style'];
    if(x['w-catch']) sections['【口头禅】']=x['w-catch'];
    if(x['w-forbid']) sections['【绝对不能说的话】']=x['w-forbid'];
    return sections;
  },
  renderSummary(){
    const x=this.collect();
    const lines=[
      '【人格】',
      '称呼：'+(x['w-name']||'-'),
      '基础：'+(x['w-basic']||'-'),
      '性格：'+(x['w-personality']||'-'),
      '日常：'+(x['w-daily']||'-'),
      '口头禅：'+(x['w-catch']||'-'),
      '禁止词：'+(x['w-forbid']||'-'),
      '风格：'+(x['w-style']||'-'),
      '',
      '【提供商】'+(x.provider?'：'+x.provider:'：未选'),
      '模型：'+(x['w-model']||'-'),
      'API Key：'+(x['w-apikey']?'已填写':'未填写'),
      '',
      '【协议层】'+(x.proto?'：'+x.proto:'：未选'),
      '回复模式：'+({always:'始终回复',mention:'仅@回复',idle:'空闲搭话'}[x.reply_mode]||x.reply_mode),
      '群聊策略：'+({all:'所有群',whitelist:'白名单',none:'不进入群'}[x.group_mode]||x.group_mode),
      '',
      '【全局】',
      '启用 EGO：'+(x.enabled?'是':'否'),
      'few-shot 示例：'+(x.example_enabled?'是':'否')
    ];
    const el=qs('#wizard-summary'); if(el) el.textContent=lines.join('\n');
  },
  async save(){
    const x=this.collect();
    if(!x.provider){ alert('请选择模型提供商'); this.step=2; this.render(); return; }
    if(!x.proto){ alert('请选择协议层/平台'); this.step=3; this.render(); return; }
    const sections=this.buildPersonalitySections(x);
    const payload={plugin_id:'ego',action:'set_config',params:{
      enabled:x.enabled,
      example_enabled:x.example_enabled,
      provider:x.provider,
      model:x['w-model']||'',
      api_key:x['w-apikey']||'',
      platform:x.proto,
      reply_mode:x.reply_mode,
      group_mode:x.group_mode
    }};
    let r1={}, r2={success:true};
    try{ r1=await api(API,{method:'POST',body:JSON.stringify(payload)}); }catch(e){ r1={}; }
    if(Object.keys(sections).length){
      try{ r2=await api(API,{method:'POST',body:JSON.stringify({plugin_id:'ego',action:'save_personality',params:{sections}})}); }catch(e){ r2={success:false}; }
    }
    const ok=(r1&&r1.config!==undefined)!==false && (r2&&r2.success!==false)!==false;
    alert(ok?'已保存并应用':'保存失败，请检查后端');
    if(ok){ this.hide(); }
  },
  skip(){ this.hide(); }
};

function wizardNext(){ wizard.next(); }
function wizardPrev(){ wizard.prev(); }
function wizardSave(){ wizard.save(); }
function wizardSkip(){ wizard.skip(); }

document.querySelectorAll('#wizard-provider-grid .card, #wizard-proto-grid .card').forEach(card=>{
  card.addEventListener('click',()=>{
    const grid=card.parentElement;
    grid.querySelectorAll('.card').forEach(c=>c.classList.remove('selected'));
    card.classList.add('selected');
  });
});

async function loadModuleConfig(){
  const data=await api(API,{method:'POST',body:JSON.stringify({plugin_id:'ego',action:'get_config'})});
  log('get_config='+JSON.stringify(data));
  const cfg=(data&&(data.config||data))||{};
  const setChecked=(id,k)=>{const el=qs('#'+id);if(!el||!cfg)return;const v=cfg[k];if(typeof v==='boolean') el.checked=v; else el.checked=v!==false;};
  setChecked('ego-enabled','enabled');
  setChecked('ego-memory','memory_enabled');
  setChecked('ego-style','style_enabled');
  setChecked('ego-rhythm','rhythm_enabled');
  setChecked('ego-slang','slang_enabled');
  setChecked('example-enabled','example_enabled');
  await loadMemoryStats();
  await loadSlangPending();
  await loadExamples();
  await updatePreview();
}
async function saveTopConfig(){
  const egoEnabled=qs('#ego-enabled');const payload={plugin_id:'ego',action:'set_config',params:{enabled:!!(egoEnabled&&egoEnabled.checked)}};
  const data=await api(API,{method:'POST',body:JSON.stringify(payload)});
  log('saveTopConfig='+JSON.stringify(data));
  toast(data && data.config!==undefined ? '已保存' : '保存失败');
}
async function saveModuleConfig(){
  const payload={plugin_id:'ego',action:'set_config',params:{
    enabled:!!qs('#ego-enabled').checked,
    memory_enabled:!!qs('#ego-memory').checked,
    style_enabled:!!qs('#ego-style').checked,
    rhythm_enabled:!!qs('#ego-rhythm').checked,
    slang_enabled:!!qs('#ego-slang').checked,
    example_enabled:!!qs('#example-enabled').checked
  }};
  const data=await api(API,{method:'POST',body:JSON.stringify(payload)});
  log('saveModuleConfig='+JSON.stringify(data));
  toast(data && data.config!==undefined ? '已保存' : '保存失败');
}

async function loadMemoryStats(){
  try{
    const scope=qs('#memory-scope')?qs('#memory-scope').value.trim()||'default':'default';
    const data=await api(API,{method:'POST',body:JSON.stringify({plugin_id:'ego',action:'memory_stats',params:{scope_id:scope}})});
    log('memory_stats='+JSON.stringify(data));
    const stats=(data&&data.stats)||{};
    const set=(id,v)=>{const el=qs('#'+id);if(el) el.textContent=v!=null?v:'-';};
    set('ego-mem-msg',stats.messages);set('ego-mem-fact',stats.facts);set('ego-mem-event',stats.events);
    const persona=await api(API,{method:'POST',body:JSON.stringify({plugin_id:'ego',action:'memory_persona',params:{scope_id:scope}})});
    set('ego-mem-persona',(persona&&persona.persona&&Object.keys(persona.persona).length)||'-');
  }catch(e){log('memory error='+e)}
}
async function exportPersona(){
  const scope=qs('#memory-scope')?qs('#memory-scope').value.trim()||'default':'default';
  const data=await api(API,{method:'POST',body:JSON.stringify({plugin_id:'ego',action:'memory_persona',params:{scope_id:scope}})});
  const persona=(data&&data.persona)||{};
  const blob=new Blob([JSON.stringify(persona,null,2)],{type:'application/json'});
  const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='ego-persona-'+scope+'.json';a.click();URL.revokeObjectURL(url);
  toast('已导出人格');
}
async function clearMemory(){
  if(!confirm('确定清空该 scope 的记忆？此操作不可恢复。')) return;
  const scope=qs('#memory-scope')?qs('#memory-scope').value.trim()||'default':'default';
  const data=await api(API,{method:'POST',body:JSON.stringify({plugin_id:'ego',action:'memory_clear',params:{scope_id:scope}})});
  log('memory_clear='+JSON.stringify(data));
  toast(data && data.success!==false ? '已清空' : '清空失败');
  loadMemoryStats();
}

function switchSlangTab(btn,tab){
  qsa('.tab').forEach(b=>b.classList.remove('active'));btn.classList.add('active');
  qs('#slang-pending').style.display=tab==='pending'?'':'none';
  qs('#slang-injected').style.display=tab==='injected'?'':'none';
  qs('#slang-stats').style.display=tab==='stats'?'':'none';
  if(tab==='pending') loadSlangPending();
  if(tab==='injected') loadSlangInjected();
  if(tab==='stats') loadSlangStats();
}
async function learnSlang(){
  const topics=[];
  const raw=qs('#slang-source').value; if(raw) topics.push(raw);
  const data=await api(API,{method:'POST',body:JSON.stringify({plugin_id:'ego',action:'slang_learn',topics:topics.length?topics:['游戏圈','直播圈','抽象圈']})});
  log('slang_learn='+JSON.stringify(data));
  toast(data && data.ok ? ('已学习 '+(data.added||0)+' 条') : '学习失败');
  loadSlangPending();
}
async function loadSlangPending(){
  const search=qs('#slang-search')?qs('#slang-search').value||'':'';
  const source=qs('#slang-source')?qs('#slang-source').value||'':'';
  const minQ=qs('#slang-min-quality')?parseFloat(qs('#slang-min-quality').value||0):0;
  const data=await api(API,{method:'POST',body:JSON.stringify({plugin_id:'ego',action:'slang_pending',params:{search,source,min_quality:minQ}})});
  log('slang_pending='+JSON.stringify(data));
  const list=Array.isArray(data&&data.pending)?data.pending:[];
  const root=qs('#slang-pending-list');
  if(!root){return;}
  if(!list.length){root.innerHTML='<div class="empty">暂无候选</div>';return;}
  root.innerHTML=list.map((item,idx)=>`<div class="list-item"><div style="flex:1"><div><strong>${esc(item.id)}</strong><span class="tag">${esc(item.source||'未知')}</span><span class="quality">${Number(item.quality||0).toFixed(2)}</span></div><div class="muted">${esc(item.meaning||'')}</div></div><div><input type="checkbox" class="slang-check" data-id="${esc(item.id)}" data-idx="${idx}"><button onclick="injectOne('${esc(item.id)}')">注入</button><button class="danger" onclick="rejectOne('${esc(item.id)}')">拒绝</button></div></div>`).join('');
}
async function loadSlangInjected(){
  const data=await api(API,{method:'POST',body:JSON.stringify({plugin_id:'ego',action:'slang_injected'})});
  log('slang_injected='+JSON.stringify(data));
  const list=Array.isArray(data&&data.injected)?data.injected:[];
  const root=qs('#slang-injected-list');
  if(!root){return;}
  if(!list.length){root.innerHTML='<div class="empty">暂无已注入</div>';return;}
  root.innerHTML=list.slice(-50).reverse().map(item=>`<div class="list-item"><div style="flex:1"><div><strong>${esc(item.id)}</strong></div><div class="muted">${esc(item.meaning||'')}</div></div><div class="muted">${esc(item.injected_at||'')}</div></div>`).join('');
}
async function loadSlangStats(){
  const data=await api(API,{method:'POST',body:JSON.stringify({plugin_id:'ego',action:'slang_stats'})});
  log('slang_stats='+JSON.stringify(data));
  const stats=(data&&data.stats)||{};
  const box=qs('#slang-stats-box');if(!box)return;
  box.innerHTML=`<div class="stat-grid"><div class="stat-card"><div class="stat-value">${stats.pending??'-'}</div><div class="stat-label">候选</div></div><div class="stat-card"><div class="stat-value">${stats.injected??'-'}</div><div class="stat-label">已注入</div></div><div class="stat-card"><div class="stat-value">${stats.rejected??'-'}</div><div class="stat-label">已拒绝</div></div><div class="stat-card"><div class="stat-value">${stats.total_learned??'-'}</div><div class="stat-label">学习次数</div></div></div><div class="muted" style="margin-top:8px">来源：${esc((stats.sources||{}).toString())}</div>`;
}
async function injectOne(id){const data=await api(API,{method:'POST',body:JSON.stringify({plugin_id:'ego',action:'slang_inject',params:{ids:[id]}})});log('inject='+JSON.stringify(data));toast(data&&data.injected?'已注入':'注入失败');loadSlangPending();loadSlangStats();}
async function rejectOne(id){const data=await api(API,{method:'POST',body:JSON.stringify({plugin_id:'ego',action:'slang_reject',params:{ids:[id]}})});log('reject='+JSON.stringify(data));toast(data&&data.rejected?'已拒绝':'拒绝失败');loadSlangPending();loadSlangStats();}
async function batchInject(){const ids=[...qs('#slang-pending-list').querySelectorAll('.slang-check:checked')].map(el=>el.dataset.id);if(!ids.length){toast('请先勾选');return;}const data=await api(API,{method:'POST',body:JSON.stringify({plugin_id:'ego',action:'slang_inject',params:{ids}})});log('batch inject='+JSON.stringify(data));toast(data&&data.injected?('已注入 '+data.injected+' 条'):'批量注入失败');loadSlangPending();loadSlangStats();}
async function batchReject(){const ids=[...qs('#slang-pending-list').querySelectorAll('.slang-check:checked')].map(el=>el.dataset.id);if(!ids.length){toast('请先勾选');return;}const data=await api(API,{method:'POST',body:JSON.stringify({plugin_id:'ego',action:'slang_reject',params:{ids}})});log('batch reject='+JSON.stringify(data));toast(data&&data.rejected?('已拒绝 '+data.rejected+' 条'):'批量拒绝失败');loadSlangPending();loadSlangStats();}
async function clearInjected(){if(!confirm('确定清空所有已注入黑话？')) return;const data=await api(API,{method:'POST',body:JSON.stringify({plugin_id:'ego',action:'slang_clear_injected'})});log('slang_clear_injected='+JSON.stringify(data));toast(data&&data.success!==false?'已清空':'清空失败');loadSlangInjected();loadSlangStats();}

async function loadPersonality(){
  const data=await api(API,{method:'POST',body:JSON.stringify({plugin_id:'ego',action:'get_personality'})});
  log('get_personality='+JSON.stringify(data));
  const sections=(data&&data.sections)||{};
  const fields=['你是谁','日常','性格','说话方式','群聊行为','遇到各种事','口头禅','绝对不能说的话'];
  for(const key of fields){const el=qs('#sec-'+key);if(el) el.value=sections['【'+key+'】']||'';}
  const cfg=(data&&data.config)||{};
  const exEl=qs('#example-enabled');if(exEl) exEl.checked=cfg.example_enabled!==false;
  updatePreview();
}
async function savePersonalitySection(key,value){
  const sections={}; sections['【' + key + '】'] = value;
  const data=await api(API,{method:'POST',body:JSON.stringify({plugin_id:'ego',action:'save_personality',params:{sections}})});
  log('savePersonalitySection='+JSON.stringify(data));
  return data;
}
async function saveOnePersonality(key){
  const el=qs('#sec-'+key);if(!el) return;
  const data=await savePersonalitySection(key,el.value||'');
  toast(data&&data.success!==false?'已保存':'保存失败');
}
async function saveAllPersonality(){
  const sections={};
  const fields=['你是谁','日常','性格','说话方式','群聊行为','遇到各种事','口头禅','绝对不能说的话'];
  for(const key of fields){const el=qs('#sec-'+key);if(el&&String(el.value).trim()) sections['【'+key+'】']=String(el.value).trim();}
  const data=await api(API,{method:'POST',body:JSON.stringify({plugin_id:'ego',action:'save_personality',params:{sections}})});
  log('saveAllPersonality='+JSON.stringify(data));
  toast(data&&data.success!==false?'已保存':'保存失败');
  updatePreview();
}
async function exportPersonality(){
  const data=await api(API,{method:'POST',body:JSON.stringify({plugin_id:'ego',action:'get_personality'})});
  const blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'});
  const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='ego-personality.json';a.click();URL.revokeObjectURL(url);
  toast('已导出人格');
}
async function importPersonality(e){
  const file=e.target.files[0];if(!file) return;
  const text=await file.text();let obj;try{obj=JSON.parse(text);}catch(err){toast('JSON 格式错误');return;}
  const sections=obj&&obj.sections||obj;
  const data=await api(API,{method:'POST',body:JSON.stringify({plugin_id:'ego',action:'save_personality',params:{sections}})});
  log('importPersonality='+JSON.stringify(data));
  toast(data&&data.success!==false?'已导入':'导入失败');
  loadPersonality();
}

async function loadExamples(){
  const scene=qs('#example-scene')?qs('#example-scene').value.trim():'';
  const data=await api(API,{method:'POST',body:JSON.stringify({plugin_id:'ego',action:'get_examples',params:{scene}})});
  log('get_examples='+JSON.stringify(data));
  const list=Array.isArray(data&&data.examples)?data.examples:[];
  const exJson=qs('#example-json');if(exJson)exJson.value=JSON.stringify(list,null,2);
  const root=qs('#example-list');
  if(!root){return;}
  if(!list.length){root.innerHTML='<div class="empty">暂无示例</div>';return;}
  root.innerHTML=list.map((ex,idx)=>`<div class="list-item"><div style="flex:1"><div class="muted">用户</div><div>${esc(ex.user||ex.input||'')}</div><div class="muted" style="margin-top:4px">小a</div><div>${esc(ex.bot||ex.reply||'')}</div></div><div><button class="danger" onclick="deleteExample(${idx})">删除</button></div></div>`).join('');
}
async function saveExamples(){
  const raw=qs('#example-json').value||'[]';let parsed;try{parsed=JSON.parse(raw);}catch(e){toast('JSON 格式错误');return;}
  const data=await api(API,{method:'POST',body:JSON.stringify({plugin_id:'ego',action:'save_examples',params:{examples:parsed}})});
  log('save_examples='+JSON.stringify(data));
  toast(data&&data.success!==false?'已保存':'保存失败');
  loadExamples();
}
async function addExample(){
  const user=prompt('用户消息：');if(user==null) return;
  const bot=prompt('小a回复：');if(bot==null) return;
  const raw=qs('#example-json').value||'[]';let list;try{list=JSON.parse(raw);}catch(e){list=[];}
  list.push({user,bot});
  const exJson=qs('#example-json');if(exJson)exJson.value=JSON.stringify(list,null,2);
  await saveExamples();
}
async function deleteExample(idx){
  if(!confirm('删除这条示例？')) return;
  let raw=qs('#example-json').value||'[]';let list;try{list=JSON.parse(raw);}catch(e){return;}
  list.splice(idx,1);const exJson=qs('#example-json');if(exJson)exJson.value=JSON.stringify(list,null,2);await saveExamples();
}

async function updatePreview(){
  const scope=qs('#preview-scope')?qs('#preview-scope').value.trim()||'default':'default';
  const user=qs('#preview-user')?qs('#preview-user').value.trim():'';
  const data=await api(API,{method:'POST',body:JSON.stringify({plugin_id:'ego',action:'preview_prompt',params:{user_msg:user,scope_id:scope}})});
  log('preview_prompt='+JSON.stringify(data));
  const prompt=(data&&data.prompt)||data||'暂无预览';
  const previewEl=qs('#preview');if(previewEl)previewEl.textContent=typeof prompt==='string'?prompt:JSON.stringify(prompt,null,2);
}
async function reloadModule(){
  const data=await api(API,{method:'POST',body:JSON.stringify({plugin_id:'ego',action:'reload'})});
  log('reload='+JSON.stringify(data));
  toast(data&&data.success!==false?'已重载':'重载失败');
}

function clearLog(){const el=qs('#ego-log');if(el)el.textContent='暂无日志'}

(async function init(){
  await loadModuleConfig();
  await loadPersonality();
  await loadExamples();
  await updatePreview();
})();
