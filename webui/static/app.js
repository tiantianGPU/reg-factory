// reg-factory WebUI 前端逻辑（原生 JS，无构建）
let SCRIPTS = [];
let EMBEDS = [];
let curRun = null;     // 当前运行 run_id
let curSrc = null;     // 当前选中脚本
let evtSrc = null;     // EventSource
let smsTimer = null;   // 接码助手倒计时刷新
let k12Url = 'http://127.0.0.1:8806/';
let k12Starting = false;

const $ = (s, r=document) => r.querySelector(s);
const $$ = (s, r=document) => [...r.querySelectorAll(s)];

function setNavOpen(open){
  document.body.classList.toggle('nav-open', open);
  $('#btn-nav').setAttribute('aria-expanded', String(open));
}

$('#btn-nav').onclick = ()=>setNavOpen(!document.body.classList.contains('nav-open'));
$('#nav-backdrop').onclick = ()=>setNavOpen(false);

const telegramDialog = $('#telegram-dialog');
$('#btn-telegram').onclick = ()=>telegramDialog.showModal();
$('#btn-telegram-close').onclick = ()=>telegramDialog.close();
telegramDialog.onclick = e=>{
  if(e.target === telegramDialog) telegramDialog.close();
};

// ---------------------------------------------------------------- 状态灯轮询
async function pollStatus(){
  try{
    const s = await (await fetch('/api/status')).json();
    $('#dot-bb').classList.toggle('on', s.bitbrowser);
    const label = s.browser_provider === 'adspower' ? 'AdsPower' : 'BitBrowser';
    $('#browser-label').textContent = label;
    $('#dot-clash').classList.toggle('on', s.clash);
    $('#dot-k12').classList.remove('pending');
    $('#dot-k12').classList.toggle('on', !!s.k12);
    $('#k12-nav-state').textContent = s.k12 ? '在线' : '离线';
    $('#k12-nav-state').classList.toggle('on', !!s.k12);
    $('#version').textContent = 'v' + (s.version || '--');
    $('#node').textContent = '节点 ' + (s.node || '--');
    $('#running').textContent = s.running ? `● ${s.running} 个任务运行中` : '';
  }catch(e){}
}
setInterval(pollStatus, 5000);

// ---------------------------------------------------------------- 视图切换
function showView(v){
  setNavOpen(false);
  document.body.classList.toggle('k12-active', v==='k12');
  $('#view-run').style.display  = v==='run' ? '' : 'none';
  $('#view-env').style.display  = v==='env' ? 'block' : 'none';
  $('#view-embed').style.display = v==='embed' ? 'block' : 'none';
  $('#view-mailpool').style.display = v==='mailpool' ? 'block' : 'none';
  $('#view-k12').style.display = v==='k12' ? 'block' : 'none';
  $$('.navbtn').forEach(b=>b.classList.toggle('active', b.dataset.view===v));
  if(v==='env') loadEnv();
  if(v==='mailpool') loadMailpool();
  if(v==='k12') openK12Channel();
}
$$('.navbtn').forEach(b=> b.onclick = ()=> showView(b.dataset.view));

// ---------------------------------------------------------------- Codex K12 集成通道
function renderK12Status(status){
  const alive = !!status.alive;
  k12Url = status.url || k12Url;
  $('#k12-open').href = k12Url;
  $('#dot-k12').classList.toggle('on', alive);
  $('#k12-nav-state').textContent = alive ? '在线' : (status.ready ? '可启动' : '未安装');
  $('#k12-nav-state').classList.toggle('on', alive);
  $('#k12-channel-status').textContent = alive ? '服务在线' : (status.message || '服务离线');
  $('#k12-channel-status').classList.toggle('on', alive);
  $('#k12-offline').style.display = alive ? 'none' : 'flex';
  $('#k12-offline-detail').textContent = status.message || 'Codex K12 服务未启动';
  $('#btn-k12-start').disabled = k12Starting || !status.ready;
  $('#btn-k12-start').textContent = k12Starting ? '启动中…' : '启动服务';
  if(alive && $('#k12-frame').src !== k12Url){
    $('#k12-frame').src = k12Url;
  }
}

async function fetchK12Status(){
  try{
    const status = await (await fetch('/api/k12/status')).json();
    renderK12Status(status);
    return status;
  }catch(e){
    const status = {alive:false, ready:false, url:k12Url, message:'主面板无法读取 K12 服务状态'};
    renderK12Status(status);
    return status;
  }
}

async function startK12Service(){
  if(k12Starting) return;
  k12Starting = true;
  renderK12Status({alive:false, ready:true, url:k12Url, message:'正在启动 Codex K12 服务'});
  try{
    const result = await (await fetch('/api/k12/start',{method:'POST'})).json();
    renderK12Status(result);
  }catch(e){
    renderK12Status({alive:false, ready:false, url:k12Url, message:'启动请求失败: '+e});
  }finally{
    k12Starting = false;
  }
}

async function openK12Channel(){
  const status = await fetchK12Status();
  if(!status.alive && status.ready) await startK12Service();
}

$('#btn-k12-start').onclick = startK12Service;
$('#btn-k12-retry').onclick = openK12Channel;

// ---------------------------------------------------------------- 脚本导航
async function loadScripts(){
  SCRIPTS = (await (await fetch('/api/scripts')).json()).scripts;
  const nav = $('#script-nav');
  const cats = {};
  SCRIPTS.forEach(s => (cats[s.category]=cats[s.category]||[]).push(s));
  nav.innerHTML = '';

  // 内嵌功能页(Gmail 等) —— 放最上面
  try{
    EMBEDS = (await (await fetch('/api/embeds')).json()).embeds || [];
    if(EMBEDS.length){
      const t=document.createElement('div'); t.className='cat-title'; t.textContent='功能'; nav.appendChild(t);
      EMBEDS.forEach(e=>{
        const b=document.createElement('button');
        b.className='scriptbtn'; b.textContent='🌐 '+e.title; b.dataset.embed=e.id;
        b.onclick=()=>openEmbed(e.id);
        nav.appendChild(b);
      });
    }
  }catch(err){}

  for(const cat of Object.keys(cats)){
    const t = document.createElement('div');
    t.className='cat-title'; t.textContent=cat; nav.appendChild(t);
    cats[cat].forEach(s=>{
      const b=document.createElement('button');
      b.className='scriptbtn'; b.textContent=s.title; b.dataset.id=s.id;
      b.onclick=()=>{ showView('run'); selectScript(s.id); };
      nav.appendChild(b);
    });
  }
  // 外部工具链接(新标签打开)
  try{
    const links = (await (await fetch('/api/links')).json()).links || [];
    if(links.length){
      const t = document.createElement('div');
      t.className='cat-title'; t.textContent='外部工具'; nav.appendChild(t);
      links.forEach(l=>{
        const a=document.createElement('a');
        a.className='scriptbtn linkbtn'; a.href=l.url; a.target='_blank'; a.rel='noopener';
        a.title=l.desc||l.url; a.innerHTML=`🔗 ${l.title}`;
        nav.appendChild(a);
      });
    }
  }catch(e){}
}

function selectScript(id){
  curSrc = SCRIPTS.find(s=>s.id===id);
  $$('.scriptbtn').forEach(b=>b.classList.toggle('active', b.dataset.id===id));
  if(!evtSrc) setRunState('idle', '待运行');
  renderForm(curSrc);
}

// ---------------------------------------------------------------- 渲染表单
function renderForm(s){
  const p = $('#form-panel');
  p.innerHTML = '';
  const h = document.createElement('div');
  h.innerHTML = `<h2 class="form-title">${s.title}</h2><p class="form-desc">${s.desc||''}</p>`;
  p.appendChild(h);

  s.args.forEach(a=>{
    const f = document.createElement('div'); f.className='field';
    const label = a.flag.replace(/^--/,'');
    if(a.type==='bool'){
      f.className='field checkbox';
      f.innerHTML = `<input type="checkbox" id="f_${label}" ${a.default?'checked':''}>
        <label for="f_${label}">${label}</label>`;
      if(a.help){ const hh=document.createElement('div'); hh.className='fhelp'; hh.textContent=a.help; f.appendChild(hh); }
    }else if(a.type==='choice'){
      f.innerHTML = `<label>${label}</label>
        <select id="f_${label}">${a.choices.map(c=>`<option ${c==a.default?'selected':''}>${c}</option>`).join('')}</select>
        ${a.help?`<div class="fhelp">${a.help}</div>`:''}`;
    }else if(a.type==='multi'){
      const def = a.default||[];
      f.innerHTML = `<label>${label}</label>
        <div class="multi">${a.choices.map(c=>`<label><input type="checkbox" value="${c}" ${def.includes(c)?'checked':''} data-multi="${label}">${c}</label>`).join('')}</div>
        ${a.help?`<div class="fhelp">${a.help}</div>`:''}`;
    }else{
      const t = a.type==='int' ? 'number' : 'text';
      f.innerHTML = `<label>${label}</label>
        <input type="${t}" id="f_${label}" value="${a.default!==undefined&&a.default!==''?a.default:''}" placeholder="${a.help||''}">
        ${a.help?`<div class="fhelp">${a.help}</div>`:''}`;
    }
    p.appendChild(f);
  });

  const btn = document.createElement('button');
  btn.className='btn-run'; btn.textContent='▶ 运行';
  btn.onclick = runScript;
  p.appendChild(btn);
  const cmd = document.createElement('div'); cmd.className='cmd-line'; cmd.id='cmd-preview';
  p.appendChild(cmd);
}

function collectArgs(s){
  const args = {};
  s.args.forEach(a=>{
    const label = a.flag.replace(/^--/,'');
    if(a.type==='bool'){
      args[a.flag] = $(`#f_${label}`).checked;
    }else if(a.type==='multi'){
      args[a.flag] = $$(`input[data-multi="${label}"]:checked`).map(x=>x.value);
    }else{
      const v = $(`#f_${label}`).value.trim();
      if(v!=='') args[a.flag] = a.type==='int' ? parseInt(v,10) : v;
    }
  });
  return args;
}

// ---------------------------------------------------------------- 运行 + SSE 日志
function setRunState(state, label){
  const el = $('#run-state');
  el.className = `run-state ${state}`;
  el.textContent = label;
}

async function runScript(){
  if(curRun && evtSrc){ evtSrc.close(); evtSrc = null; }
  const args = collectArgs(curSrc);
  const log = $('#log'); log.textContent='';
  $('#log-title').textContent = `运行日志 — ${curSrc.title}`;
  setRunState('running', '运行中');
  let r;
  try{
    r = await (await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({script:curSrc.id, args})})).json();
  }catch(e){
    log.textContent='启动失败: '+e;
    setRunState('failed', '启动失败');
    return;
  }
  if(r.error){
    log.textContent='错误: '+r.error;
    setRunState('failed', '启动失败');
    return;
  }
  curRun = r.run_id;
  $('#cmd-preview').textContent = '$ '+r.cmd;
  $('#btn-stop').disabled = false;
  const stream = new EventSource(`/api/logs/${curRun}`);
  evtSrc = stream;
  stream.onmessage = e=>{ log.textContent += e.data+'\n'; log.scrollTop = log.scrollHeight; };
  stream.addEventListener('done', e=>{
    let result = {};
    try{ result = JSON.parse(e.data); }catch(err){}
    if(result.stopped) setRunState('stopped', '已停止');
    else if(result.returncode===0) setRunState('success', '已成功');
    else setRunState('failed', `失败 (${result.returncode ?? '?'})`);
    stream.close();
    if(evtSrc === stream){ evtSrc = null; curRun = null; }
    $('#btn-stop').disabled = true;
    pollStatus();
  });
  stream.onerror = ()=>{
    stream.close();
    if(evtSrc !== stream) return;
    evtSrc = null;
    curRun = null;
    $('#btn-stop').disabled = true;
    if($('#run-state').classList.contains('running')) setRunState('failed', '日志连接中断');
  };
}

$('#btn-stop').onclick = async ()=>{
  if(!curRun) return;
  setRunState('stopped', '停止中');
  await fetch(`/api/stop/${curRun}`,{method:'POST'});
  $('#btn-stop').disabled = true;
};

// ---------------------------------------------------------------- 配置页
async function loadEnv(){
  const data = await (await fetch('/api/env')).json();
  const wrap = $('#env-groups'); wrap.innerHTML='';
  data.groups.forEach(g=>{
    const box = document.createElement('div'); box.className='env-group';
    const tests = (g.tests||[]).map(t=>
      `<button class="btn-test" data-test="${t.target}">${t.label}</button>`).join('');
    box.innerHTML = `<div class="env-group-title">
        <span>${g.group}</span>
        <span class="test-area">${tests}<span class="test-result" data-result-for="${g.group}"></span></span>
      </div>`;
    g.items.forEach(it=>{
      const row = document.createElement('div'); row.className='env-item';
      const type = it.secret ? 'password':'text';
      const value = it.value || it.default || '';
      const control = it.type === 'choice'
        ? `<select data-env="${it.key}">${(it.choices||[]).map(c=>`<option value="${c}" ${c===value?'selected':''}>${c}</option>`).join('')}</select>`
        : `<input type="${type}" data-env="${it.key}" value="${(it.value||'').replace(/"/g,'&quot;')}"
                 placeholder="${it.default? '默认 '+it.default : ''}">`;
      row.innerHTML = `
        <div class="k">${it.key}${it.required?'<span class="req">*</span>':''}</div>
        <div class="v">
          ${control}
          ${it.help?`<div class="ehelp">${it.help}</div>`:''}
        </div>`;
      box.appendChild(row);
    });
    // 绑定该组的测试按钮
    box.querySelectorAll('.btn-test').forEach(btn=>{
      btn.onclick = ()=> runTest(btn.dataset.test, btn);
    });
    wrap.appendChild(box);
  });
}

// 连通测试：把当前页面所有 .env 输入(含未保存的)一起发过去，用最新值测
async function runTest(target, btn){
  const env = {};
  $$('input[data-env],select[data-env]').forEach(i=>{ if(i.value!=='') env[i.dataset.env]=i.value; });
  const old = btn.textContent;
  btn.disabled = true; btn.textContent = '测试中…';
  const res = btn.closest('.env-group').querySelector('.test-result');
  res.textContent=''; res.className='test-result';
  try{
    const r = await (await fetch(`/api/test/${target}`,{method:'POST',
      headers:{'Content-Type':'application/json'}, body:JSON.stringify({env})})).json();
    res.textContent = (r.ok?'✓ ':'✗ ') + r.msg;
    res.classList.add(r.ok?'ok':'bad');
  }catch(e){
    res.textContent = '✗ 测试请求失败: '+e; res.classList.add('bad');
  }finally{
    btn.disabled=false; btn.textContent=old;
  }
}

$('#btn-save-env').onclick = async ()=>{
  const env = {};
  $$('input[data-env],select[data-env]').forEach(i=>{ env[i.dataset.env] = i.value; });
  const r = await (await fetch('/api/env',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({env})})).json();
  const msg = $('#env-msg');
  msg.textContent = r.ok
    ? ('✓ 已保存 '+r.saved+' 项，新任务立即生效，无需重启')
    : ('保存失败: '+(r.error||''));
  setTimeout(()=>msg.textContent='', 5000);
};

// ---------------------------------------------------------------- 内嵌页 + 接码助手
function openEmbed(id){
  const e = EMBEDS.find(x=>x.id===id);
  if(!e) return;
  showView('embed');
  $$('.scriptbtn').forEach(b=>b.classList.toggle('active', b.dataset.embed===id));
  $('#embed-title').textContent = e.title;
  $('#embed-open').href = e.url;
  $('#embed-frame').src = e.url;
  const helper = $('#sms-helper');
  if(e.sms_helper){
    helper.style.display='block';
    if(e.sms_service_default) $('#sms-service').placeholder = e.sms_service_default;
    refreshRents();
  }else{
    helper.style.display='none';
  }
}

async function copyText(txt, btn){
  try{ await navigator.clipboard.writeText(txt); if(btn){const o=btn.textContent;btn.textContent='已复制';setTimeout(()=>btn.textContent=o,1200);} }
  catch(e){ alert('复制失败,请手动选择: '+txt); }
}

function fmtRemain(sec){
  sec=Math.max(0,sec); const m=Math.floor(sec/60), s=sec%60;
  return `${m}:${String(s).padStart(2,'0')}`;
}

async function refreshRents(){
  let data;
  try{ data = await (await fetch('/api/sms/rents')).json(); }catch(e){ return; }
  const wrap = $('#sms-rents'); wrap.innerHTML='';
  (data.rents||[]).forEach(r=>{
    const card = document.createElement('div'); card.className='rent-card';
    const codesHtml = r.codes.length
      ? r.codes.map(c=>`<span class="code-chip">${c}<button class="mini" data-copy="${c}">复制</button></span>`).join('')
      : '<span class="dim">暂无验证码</span>';
    card.innerHTML = `
      <div class="rent-phone">
        <b>+${r.phone}</b>
        <button class="mini" data-copy="${r.phone}">复制号码</button>
        <span class="multi-badge ${r.can_multi?'ok':'no'}">${r.can_multi?'多次接码':'单次'}</span>
        <span class="remain" data-remain="${r.remain}">剩 ${fmtRemain(r.remain)}</span>
      </div>
      <div class="rent-actions">
        <button class="btn-sm getcode" data-pkey="${r.pkey}">获取验证码</button>
        <button class="btn-sm release" data-pkey="${r.pkey}">完成/释放</button>
      </div>
      <div class="codes">${codesHtml}</div>
      <div class="sms-msg" data-msg="${r.pkey}"></div>`;
    wrap.appendChild(card);
  });
  // 绑定
  wrap.querySelectorAll('[data-copy]').forEach(b=> b.onclick=()=>copyText(b.dataset.copy,b));
  wrap.querySelectorAll('.getcode').forEach(b=> b.onclick=()=>getCode(b.dataset.pkey,b));
  wrap.querySelectorAll('.release').forEach(b=> b.onclick=()=>releaseNum(b.dataset.pkey));
  // 倒计时滴答
  if(smsTimer) clearInterval(smsTimer);
  if((data.rents||[]).length){
    smsTimer = setInterval(()=>{
      $$('.remain').forEach(el=>{
        let r=parseInt(el.dataset.remain,10)-1; el.dataset.remain=r;
        el.textContent = r>0 ? '剩 '+fmtRemain(r) : '已过期';
      });
    },1000);
  }
}

$('#btn-rent').onclick = async ()=>{
  const btn=$('#btn-rent'); const o=btn.textContent; btn.disabled=true; btn.textContent='租号中…';
  const body={ service: $('#sms-service').value.trim()||undefined, country: $('#sms-country').value.trim()||undefined,
    prefer_multi: $('#sms-prefer-multi') ? $('#sms-prefer-multi').checked : true };
  try{
    const r = await (await fetch('/api/sms/rent',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
    if(!r.ok){ alert('获取号码失败: '+r.msg); }
    await refreshRents();
  }finally{ btn.disabled=false; btn.textContent=o; }
};

async function getCode(pkey, btn){
  const o=btn.textContent; btn.disabled=true; btn.textContent='等待验证码…';
  const msg = document.querySelector(`[data-msg="${pkey}"]`);
  if(msg) msg.textContent='';
  try{
    const r = await (await fetch('/api/sms/code',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pkey})})).json();
    if(r.ok){ await refreshRents(); }
    else if(msg){ msg.textContent = (r.expired?'⏰ ':'') + r.msg; }
  }finally{ btn.disabled=false; btn.textContent=o; }
}

async function releaseNum(pkey){
  await fetch('/api/sms/release',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pkey})});
  await refreshRents();
}

// ---------------------------------------------------------------- 邮箱池
async function loadMailpool(){
  try{
    const d = await (await fetch('/api/mailpool')).json();
    $('#mailpool-total').textContent = `当前池中 ${d.total} 个邮箱`;
  }catch(e){}
}

$('#btn-import-mail').onclick = async ()=>{
  const text = $('#mailpool-input').value;
  if(!text.trim()){ $('#mailpool-msg').textContent='请先粘贴邮箱'; return; }
  const btn=$('#btn-import-mail'); const o=btn.textContent; btn.disabled=true; btn.textContent='导入中…';
  const msg=$('#mailpool-msg'); msg.textContent='';
  try{
    const r = await (await fetch('/api/mailpool',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({text})})).json();
    if(r.ok){
      let m = `✓ 导入 ${r.added}，跳过重复 ${r.skipped}`;
      if(r.bad) m += `，格式错误 ${r.bad}`;
      m += `，池中共 ${r.total}`;
      msg.textContent = m;
      if(r.bad && r.bad_samples.length) msg.textContent += `（错误样例：${r.bad_samples[0]}…）`;
      $('#mailpool-total').textContent = `当前池中 ${r.total} 个邮箱`;
      if(r.added) $('#mailpool-input').value='';
    }else{ msg.textContent='导入失败: '+(r.msg||''); }
  }catch(e){ msg.textContent='导入请求失败: '+e; }
  finally{ btn.disabled=false; btn.textContent=o; }
};

// ---------------------------------------------------------------- 启动
loadScripts();
pollStatus();
