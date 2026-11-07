// Run with Node.js: node test_queue_ui.cjs [.runtime/queue-ui-next.html]
// Exercises the actual inline UI with a small DOM and deterministic network/timer harness.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const html = fs.readFileSync(path.resolve(__dirname, process.argv[2] || 'sandbox-agent.html'), 'utf8');
const source = html.split('<script>')[1].split('</script>')[0];
new vm.Script(source);

class Element {
  constructor(id = '') { this.id=id;this.value='';this.textContent='';this.hidden=false;this.disabled=false;this.dataset={};this.style={};this.children=[];this.listeners={};this.attributes={};this.scrollHeight=100;this.scrollTop=0;this.clientHeight=100;const values=new Set();this.classList={add:(...names)=>names.forEach(x=>values.add(x)),remove:(...names)=>names.forEach(x=>values.delete(x)),contains:name=>values.has(name),toggle:(name,on)=>{const use=on===undefined?!values.has(name):on;if(use)values.add(name);else values.delete(name);return use;}}; }
  addEventListener(name, fn) { this.listeners[name]=fn; }
  setAttribute(name,value) { this.attributes[name]=value; }
  getAttribute(name) { return this.attributes[name]; }
  removeAttribute(name) { delete this.attributes[name]; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children=children; }
  querySelectorAll() { return []; }
  focus() {}
  close() { this.open=false; }
  showModal() { this.open=true; }
  remove() {}
  requestSubmit() { this.submissions=(this.submissions||0)+1; }
}

function harness() {
  const elements=new Map(),timers=new Map(),documentEvents={},calls=[];
  let now=100000,sequence=0;
  const get=id=>{if(!elements.has(id))elements.set(id,new Element(id));return elements.get(id);};
  const document={hidden:false,getElementById:get,querySelectorAll:()=>[],createElement:()=>new Element(),addEventListener:(name,fn)=>{documentEvents[name]=fn;},documentElement:{style:{setProperty(){},removeProperty(){}}},body:new Element()};
  const context=vm.createContext({console,URL,URLSearchParams,Headers,FormData,Map,Set,document,navigator:{},history:{replaceState(){}},window:{location:{search:'',href:'http://localhost/'},innerWidth:1280,addEventListener(){}},performance:{now:()=>now},Date:class extends Date{static now(){return now;}},setTimeout:(fn,delay)=>{const id=++sequence;timers.set(id,{fn,due:now+delay});return id;},clearTimeout:id=>timers.delete(id)});
  vm.runInContext(source.replace('api("/api/me").then(enterApp).catch(()=>showLogin());',''),context);
  const ui=vm.runInContext('({state,requestView,setRequestState,renderQueue,renderMessages,refreshLiveState,scheduleLivePoll,showLogin,loadChats,openDeleteChat,openSettings})',context);
  ui.state.me={username:'test'};
  context.mockApi=async route=>{calls.push(route);return route==='/api/queue'?{server_busy:false,queue_waiting:0}:{id:ui.state.chat,messages:[],mode:'chat',running:false,request_state:'idle'};};
  vm.runInContext('api=(...args)=>globalThis.mockApi(...args);',context);
  return {ui,get,document,documentEvents,timers,calls,context,setNow:value=>{now=value;},now:()=>now,async tick(){const next=[...timers].sort((a,b)=>a[1].due-b[1].due)[0];assert.ok(next,'scheduled poll exists');timers.delete(next[0]);now=next[1].due;next[1].fn();await settle();}};
}
async function settle(){for(let i=0;i<12;i++)await Promise.resolve();}

async function main(){
  let count=0;
  const test=async(name,fn)=>{await fn();console.log('ok - '+name);count++;};
  await test('queued, active, cancelling and idle have distinct accessible controls',()=>{
    const h=harness();h.ui.setRequestState({running:true,request_state:'queued',queue_position:3});
    assert.equal(h.get('run-status').textContent,'대기 3번째 · 앞선 답변이 끝나면 자동으로 시작합니다.');assert.equal(h.get('cancel-button').textContent,'대기 취소');assert.equal(h.get('cancel-button').disabled,false);assert.equal(h.get('send-button').hidden,true);assert.equal(h.get('run-status').classList.contains('busy'),false);
    h.ui.setRequestState({request_state:'running'});assert.equal(h.get('cancel-button').textContent,'답변 중지');assert.equal(h.get('run-status').classList.contains('busy'),true);
    h.ui.setRequestState({request_state:'cancelling'});assert.equal(h.get('cancel-button').disabled,true);assert.equal(h.get('run-status').textContent,'요청을 중지하고 있습니다…');
    h.ui.setRequestState({request_state:'idle'});assert.equal(h.get('send-button').hidden,false);assert.equal(h.get('run-status').textContent,'');assert.equal(h.get('mode-agent').disabled,false);
    assert.match(html,/id="run-status" role="status" aria-live="polite" aria-atomic="true"/);assert.match(html,/aria-describedby="queue-help"/);
  });
  await test('legacy running state does not invent a queue position',()=>{const h=harness();const view=h.ui.requestView({running:true});assert.equal(view.phase,'running');assert.doesNotMatch(view.text,/번째/);h.ui.setRequestState({request_state:'queued',queue_position:null});assert.match(h.get('run-status').textContent,/^대기 중/);});
  await test('queued empty assistant is an admission placeholder, not generation',()=>{const h=harness();h.ui.renderMessages({title:'test',running:true,request_state:'queued',messages:[{id:'a',role:'assistant',content:''}]});const node=h.ui.state.nodes.get('a');assert.match(node._content.textContent,/순서가 되면 답변을 시작/);assert.doesNotMatch(node._content.textContent,/작성하고/);assert.equal(node._copyButton.disabled,true);});
  await test('global status uses aggregate fields only',()=>{const h=harness();h.ui.renderQueue({server_busy:true,queue_waiting:2,username:'SECRET_USER',content:'SECRET_CONTENT'});assert.equal(h.get('queue-summary').textContent,'답변 처리 중 · 대기 2개');h.ui.renderQueue({server_busy:false,queue_waiting:0});assert.equal(h.get('queue-summary').textContent,'모델 사용 가능');});
  await test('429 admission keeps text and attachments unchanged',async()=>{const h=harness();h.ui.state.chat='chat1';h.ui.state.pending=[{id:'file1',name:'notes.txt'}];h.get('message-input').value='keep this draft';h.context.mockApi=async()=>{throw new Error('요청이 많습니다. 잠시 후 다시 시도해 주세요.');};await h.get('message-form').listeners.submit({preventDefault(){}});assert.equal(h.get('message-input').value,'keep this draft');assert.equal(h.ui.state.pending[0].id,'file1');assert.equal(h.ui.state.requestBusy,false);assert.equal(h.ui.state.running,false);assert.match(h.get('global-error').textContent,/요청이 많습니다/);});
  await test('successful admission shows queued immediately and clears draft once',async()=>{const h=harness();h.ui.state.chat='chat1';h.get('message-input').value='accepted';h.context.mockApi=async()=>({status:'queued',request_state:'queued',queue_position:2,running:true,server_busy:true,queue_waiting:2});await h.get('message-form').listeners.submit({preventDefault(){}});assert.equal(h.get('message-input').value,'');assert.equal(h.ui.state.requestState,'queued');assert.equal(h.get('cancel-button').textContent,'대기 취소');assert.equal(h.timers.size,1);});
  await test('queue cancellation becomes idle and schedules one refresh',async()=>{const h=harness();h.ui.state.chat='chat1';h.ui.setRequestState({request_state:'queued',queue_position:1});h.context.mockApi=async()=>({status:'cancelled'});await h.get('cancel-button').listeners.click();assert.equal(h.ui.state.running,false);assert.equal(h.get('send-button').hidden,false);assert.equal(h.timers.size,1);});
  await test('one polling loop refreshes aggregate at five seconds and active chat at two',async()=>{const h=harness();h.ui.state.chat='chat1';h.context.mockApi=async route=>{h.calls.push(route);return route==='/api/queue'?{server_busy:true,queue_waiting:1}:{request_state:'queued',running:true,queue_position:1,messages:[],mode:'chat'};};await h.ui.refreshLiveState();assert.equal(h.timers.size,1);await h.tick();assert.equal(h.now(),102000);await h.tick();assert.equal(h.now(),104000);await h.tick();assert.equal(h.now(),105000);assert.equal(h.calls.filter(x=>x==='/api/queue').length,2);assert.equal(h.calls.filter(x=>x.includes('/api/chats/')).length,3);assert.equal(h.timers.size,1);});
  await test('concurrent refreshes and chat switches discard stale responses',async()=>{const h=harness();h.ui.state.chat='old';const resolvers=[];h.context.mockApi=route=>{h.calls.push(route);return new Promise(resolve=>resolvers.push({route,resolve}));};const first=h.ui.refreshLiveState();await h.ui.refreshLiveState();assert.equal(h.calls.length,2);h.ui.state.chat='new';h.ui.state.epoch++;h.ui.state.nextChatPoll=0;h.ui.state.nextQueuePoll=0;h.ui.scheduleLivePoll();for(const item of resolvers)item.resolve(item.route==='/api/queue'?{server_busy:true,queue_waiting:7}:{title:'STALE',request_state:'running',messages:[],mode:'agent'});await first;assert.notEqual(h.get('chat-title').textContent,'STALE');assert.equal(h.ui.state.mode,'chat');assert.notEqual(h.get('queue-summary').textContent,'답변 처리 중 · 대기 7개');assert.equal(h.timers.size,1);});
  await test('hidden or unauthenticated tabs do not poll; visible tabs restart once',async()=>{const h=harness();h.ui.scheduleLivePoll();assert.equal(h.timers.size,1);h.document.hidden=true;h.documentEvents.visibilitychange();assert.equal(h.timers.size,0);await h.ui.refreshLiveState();assert.equal(h.calls.length,0);h.document.hidden=false;h.documentEvents.visibilitychange();assert.equal(h.timers.size,1);h.ui.showLogin();assert.equal(h.timers.size,0);await h.ui.refreshLiveState();assert.equal(h.calls.length,0);});
  await test('legacy queue 404 stays quiet and keeps ordinary chat polling',async()=>{const h=harness();h.ui.state.chat='chat1';h.context.mockApi=async route=>{if(route==='/api/queue')throw new Error('Not Found');return{running:true,messages:[],mode:'chat'};};await h.ui.refreshLiveState();assert.equal(h.get('global-error').hidden,true);assert.equal(h.ui.state.requestState,'running');assert.equal(h.timers.size,1);});
  await test('Enter, Shift+Enter and IME composition behavior remains intact',()=>{const h=harness();const input=h.get('message-input');const key=input.listeners.keydown;let prevented=0;const event={key:'Enter',shiftKey:false,isComposing:false,keyCode:13,preventDefault(){prevented++;}};key({...event,shiftKey:true});key({...event,isComposing:true});assert.equal(h.get('message-form').submissions||0,0);key(event);assert.equal(h.get('message-form').submissions,1);assert.equal(prevented,1);h.ui.setRequestState({request_state:'queued'});key(event);assert.equal(h.get('message-form').submissions,1);});
  await test('deletion requires explicit confirmation and clears only the selected conversation',async()=>{
    const h=harness();h.ui.state.chat='delete-me';let deletes=0;
    h.context.mockApi=async(route,options={})=>{if(options.method==='DELETE'){deletes++;assert.equal(route,'/api/chats/delete-me');return{deleted:'delete-me'};}return[];};
    h.ui.openDeleteChat({id:'delete-me',title:'<script>untrusted title</script>'});
    assert.equal(deletes,0);assert.equal(h.get('delete-chat-title').textContent,'<script>untrusted title</script>');assert.equal(h.get('delete-chat-dialog').open,true);
    await h.get('delete-chat-cancel').listeners.click();assert.equal(deletes,0);assert.equal(h.ui.state.chat,'delete-me');
    h.ui.openDeleteChat({id:'delete-me'});await h.get('delete-chat-confirm').listeners.click();
    assert.equal(deletes,1);assert.equal(h.ui.state.chat,null);assert.equal(h.get('delete-chat-dialog').open,false);assert.equal(h.get('empty-chat').hidden,false);
  });
  await test('deleting another conversation preserves the current draft and files',async()=>{
    const h=harness();h.ui.state.chat='current';h.get('message-input').value='keep draft';h.ui.state.pending=[{id:'file'}];
    h.context.mockApi=async(route,options={})=>options.method==='DELETE'?{deleted:'old'}:[];
    h.ui.openDeleteChat({id:'old'});await h.get('delete-chat-confirm').listeners.click();
    assert.equal(h.ui.state.chat,'current');assert.equal(h.get('message-input').value,'keep draft');assert.equal(h.ui.state.pending[0].id,'file');
  });
  await test('active-request deletion refusal retains history and shows a retryable error',async()=>{
    const h=harness();h.ui.state.chat='active';h.context.mockApi=async()=>{throw new Error('답변 중지 후 삭제');};
    h.ui.openDeleteChat({id:'active'});await h.get('delete-chat-confirm').listeners.click();
    assert.equal(h.ui.state.chat,'active');assert.equal(h.get('delete-chat-dialog').open,true);assert.match(h.get('delete-chat-error').textContent,/답변 중지/);assert.equal(h.get('delete-chat-confirm').disabled,false);
  });
  await test('every history row offers an accessible separate delete control',async()=>{
    const h=harness();h.context.mockApi=async()=>[{id:'a',title:'My chat',mode:'chat'}];await h.ui.loadChats();
    const row=h.get('chat-list').children[0];assert.equal(row.children.length,2);assert.equal(row.children[1].getAttribute('aria-label'),'My chat 대화 삭제');
    row.children[1].listeners.click();assert.equal(h.ui.state.deleteChatId,'a');assert.equal(h.ui.state.chat,null);
  });
  await test('late history fetch cannot restore a deleted row',async()=>{
    const h=harness();let old;h.context.mockApi=()=>new Promise(resolve=>{old=resolve;});const pending=h.ui.loadChats();
    h.context.mockApi=async()=>[];await h.ui.loadChats();old([{id:'old',title:'deleted'}]);await pending;
    assert.equal(h.get('chat-list').children[0].textContent,'저장된 대화가 없습니다.');
  });
  await test('queue help displays the effective configured minutes',()=>{
    const h=harness();h.ui.renderQueue({limits:{max_runtime_seconds:3600,max_wait_seconds:7200}});assert.match(h.get('queue-help').textContent,/응답 최대 60분 · 대기 최대 120분/);
    h.ui.renderQueue({limits:{max_runtime_seconds:5400,max_wait_seconds:10800}});assert.match(h.get('queue-help').textContent,/90분 · 대기 최대 180분/);
  });
  await test('web settings load shared limits and hide remote shutdown authority',async()=>{
    const h=harness();h.context.mockApi=async()=>({max_response_minutes:60,max_queue_wait_minutes:120,can_shutdown:false});
    await h.ui.openSettings();assert.equal(h.get('settings-dialog').open,true);assert.equal(h.get('response-minutes').value,60);assert.equal(h.get('shutdown-open').disabled,true);assert.match(h.get('shutdown-help').textContent,/서버 PC/);
  });
  await test('valid settings are saved without clearing the current draft',async()=>{
    const h=harness();h.get('response-minutes').value='90';h.get('wait-minutes').value='180';h.get('message-input').value='keep draft';h.ui.state.chat='current';
    h.context.mockApi=async(route,options)=>{assert.equal(route,'/api/settings');assert.equal(options.method,'PUT');assert.equal(options.body.max_response_minutes,90);return options.body;};
    await h.get('settings-form').listeners.submit({preventDefault(){}});assert.equal(h.get('settings-success').hidden,false);assert.match(h.get('settings-success').textContent,/90분/);assert.equal(h.get('message-input').value,'keep draft');assert.equal(h.ui.state.chat,'current');
  });
  await test('invalid limits never send a settings request',async()=>{
    const h=harness();h.context.mockApi=async()=>{throw new Error('must not call');};h.get('response-minutes').value='90';h.get('wait-minutes').value='60';
    await h.get('settings-form').listeners.submit({preventDefault(){}});assert.match(h.get('settings-error').textContent,/대기 제한/);assert.equal(h.ui.state.settingsBusy,false);
  });
  await test('settings write failure keeps inputs and leaves a retry control',async()=>{
    const h=harness();h.get('response-minutes').value='90';h.get('wait-minutes').value='180';h.context.mockApi=async()=>{throw new Error('write failed');};
    await h.get('settings-form').listeners.submit({preventDefault(){}});assert.equal(h.get('response-minutes').value,'90');assert.match(h.get('settings-error').textContent,/write failed/);assert.equal(h.get('settings-save').disabled,false);
  });
  await test('shutdown requires a separate explicit confirmation and stops polling on acceptance',async()=>{
    const h=harness();h.ui.state.canShutdown=true;let requests=0;h.context.mockApi=async(route,options)=>{requests++;assert.equal(route,'/api/server/shutdown');assert.equal(options.body.confirmed,true);return{status:'stopping'};};
    h.get('shutdown-open').listeners.click();assert.equal(requests,0);assert.equal(h.get('shutdown-dialog').open,true);
    h.get('shutdown-cancel').listeners.click();assert.equal(requests,0);assert.equal(h.get('shutdown-dialog').open,false);
    h.get('shutdown-open').listeners.click();h.ui.scheduleLivePoll();await h.get('shutdown-confirm').listeners.click();assert.equal(requests,1);assert.equal(h.get('stopped-dialog').open,true);assert.equal(h.ui.state.serverStopped,true);assert.equal(h.timers.size,0);h.ui.scheduleLivePoll();assert.equal(h.timers.size,0);await h.ui.refreshLiveState();assert.equal(requests,1);
  });
  await test('busy shutdown response keeps the app usable',async()=>{
    const h=harness();h.ui.state.canShutdown=true;h.context.mockApi=async()=>{throw new Error('진행 중 요청을 완료');};h.get('shutdown-open').listeners.click();await h.get('shutdown-confirm').listeners.click();assert.equal(h.ui.state.serverStopped,false);assert.match(h.get('shutdown-error').textContent,/진행 중/);assert.equal(h.get('shutdown-confirm').disabled,false);
  });
  console.log(`${count} queue UI checks passed.`);
}
main().catch(error=>{console.error(error);process.exitCode=1;});
