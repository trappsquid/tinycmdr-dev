
const log=document.getElementById('log'),inp=document.getElementById('in'),
      sendBtn=document.getElementById('send'),stopBtn=document.getElementById('stop'),
      stateEl=document.getElementById('state');
let token=localStorage.fb_token||'';
if(!token){token=prompt('tinycmdr token (leave empty if loopback):')||'';localStorage.fb_token=token;}
const H={'Content-Type':'application/json','X-tinycmdr-Token':token};
let runId=null,since=0,timer=null;

const nodes=new Map();
function add(kind,text,t,i){
 let d=(i===undefined||i===null)?null:nodes.get(i);
 if(!d){d=document.createElement('div');d.className='msg '+kind;
  if(i!==undefined&&i!==null)nodes.set(i,d);
  log.appendChild(d);log.scrollTop=log.scrollHeight;
  if(t!==undefined&&t!==null){const s=document.createElement('span');s.className='stamp';
   s.textContent=t+'s';d.appendChild(s);}
  d.appendChild(document.createTextNode(text));return d;}
 d.className='msg '+kind;
 d.textContent='';
 if(t!==undefined&&t!==null){const s=document.createElement('span');s.className='stamp';
  s.textContent=t+'s';d.appendChild(s);}
 d.appendChild(document.createTextNode(text));
 return d;
}
function status(j){
 stateEl.textContent=j.done?('done in '+j.elapsed+'s, '+j.steps+' tool calls')
   :((j.status||'working')+' - '+j.elapsed+'s, '+j.steps+' tool calls');
}
function busy(on){document.body.classList.toggle('busy',on);
 sendBtn.style.display=on?'none':'inline-block';
 inp.placeholder=on?'Steer it mid-run, or press Stop...':'Message tinycmdr... ( /help )';}

async function steer(t){
 try{await fetch('/api/steer',{method:'POST',headers:H,
   body:JSON.stringify({run_id:runId,message:t})});}
 catch(e){add('error','steer failed: '+e);}
}

async function send(){
 const t=inp.value.trim();if(!t)return;inp.value='';inp.style.height='auto';
 if(runId){if(t.startsWith('/')){add('system','commands wait until this run finishes');return;}
  return steer(t);}
 if(t.startsWith('/')){
  add('you',t);
  try{const r=await fetch('/api/chat',{method:'POST',headers:H,body:JSON.stringify({message:t})});
   const j=await r.json();add(r.ok?'say':'error',j.reply||j.error||'(no reply)');}
  catch(e){add('error',''+e);}
  return;}
 try{
  const r=await fetch('/api/run',{method:'POST',headers:H,body:JSON.stringify({message:t})});
  const j=await r.json();
  if(j.immediate){add('say',j.reply);return;}
  if(j.error){add('error',j.error);return;}
  runId=j.run_id;since=0;busy(true);stateEl.textContent='starting';
  if(j.busy)add('system','a run was already going; your message was queued into it');
  poll();
 }catch(e){add('error',''+e);}
}

async function poll(){
 if(!runId)return;
 try{
  const r=await fetch('/api/events?run_id='+runId+'&since='+since,
    {headers:{'X-tinycmdr-Token':token}});
  if(r.ok){
   const j=await r.json();
   for(const l of j.lines){since=l.i+1;add(l.kind,l.text,l.kind==='final'?l.t:null,l.i);}
   status(j);
   if(j.done){runId=null;busy(false);return;}
  }else if(r.status===404){add('error','that run is gone');runId=null;busy(false);return;}
 }catch(e){}
 timer=setTimeout(poll,700);
}

async function stop(){
 if(!runId)return;
 try{await fetch('/api/stop',{method:'POST',headers:H,body:JSON.stringify({run_id:runId})});
  add('system','stop requested - waiting for this step to finish');}
 catch(e){add('error',''+e);}
}

sendBtn.onclick=send;stopBtn.onclick=stop;
inp.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send();}});
inp.addEventListener('input',()=>{inp.style.height='auto';inp.style.height=Math.min(inp.scrollHeight,240)+'px';});
(async()=>{
 try{const r=await fetch('/api/health');const j=await r.json();
  document.getElementById('ver').textContent=j.version||'';}catch(e){}
 add('system','no chat server needed: this page drives the same agent. /help for commands.');
})();
