'use strict';
const $ = s => document.querySelector(s);
const fmt = value => Number.isFinite(Number(value)) ? Math.round(Number(value)).toLocaleString() : '—';
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const display = value => typeof value === 'object' ? JSON.stringify(value) : String(value ?? 'Unavailable');
let currentView = 'overview';
let localTools = [];
const conversationId=crypto.randomUUID();
let chatBusy = false;
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  let data; try { data = await response.json(); } catch { throw new Error(`The server returned an unreadable response (${response.status}).`); }
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : data.error || `Request failed (${response.status}).`);
  return data;
}
function setError(message) { $('#error-banner').textContent = message || ''; $('#error-banner').hidden = !message; }
function statusText(value) { if (value === true) return 'Connected'; if (value === false) return 'Unavailable'; return display(value); }
function renderStatus(status={}) {
  for (const key of ['postgres','clickhouse','cdc']) $(`#status-${key}`).textContent = statusText(status[key]);
  $('#capabilities').innerHTML = Object.entries(status).map(([key,value]) => `<div class="capability-row"><span>${esc(key === 'embedding' ? 'Embedding provider' : key === 'agent' ? 'Agent runtime' : key === 'cdc' ? 'Change capture' : key === 'search' ? 'Search execution' : key)}</span><span>${esc(statusText(value))}</span></div>`).join('');
  $('.composer-note').textContent = statusText(status.agent);
  $('#system-label').textContent = status.clickhouse ? `ClickHouse · ${statusText(status.clickhouse)}` : 'Connection status unavailable';
}
function renderDashboard(data) {
  if (data.profile?.name) {
    $('#profile-name').textContent = data.profile.name;
    $('.profile .avatar').textContent = data.profile.name.split(' ').map(part=>part[0]).join('');
  }
  const today = data.today || {};
  $('#calories').textContent=fmt(today.calories);
  for (const field of ['protein','carbs','fat']) $(`#${field}`).textContent=fmt(today[field])+' g';
  $('#steps').textContent=fmt(today.steps);
  $('#active-minutes').textContent=fmt(today.active_minutes);
  const history=(data.history || []).slice(-10);
  const max=Math.max(1,...history.map(d=>Number(d.steps)||0));
  $('#history-chart').innerHTML=history.length ? history.map(day=> {
    const date = new Date(`${String(day.day).slice(0,10)}T12:00:00`);
    const label=Number.isNaN(date.getTime()) ? day.day : date.toLocaleDateString(undefined,{weekday:'short'});
    return `<div class="bar-column"><div class="bar" style="height:${Math.max(2,(Number(day.steps)||0)/max*110)}px" title="${esc(day.day)}: ${fmt(day.steps)} steps"><span class="bar-value">${fmt(day.steps)}</span></div><label>${esc(label)}</label></div>`;
  }).join('') : '<div class="empty-state">Activity history will appear after events arrive.</div>';
  const meals=data.meals || [];
  $('#meal-count').textContent=`${meals.length} meals`;
  $('#meal-list').innerHTML=meals.length ? meals.map((meal,i)=>`<div class="meal-row"><span class="meal-art">${['◒','◍','◓'][i%3]}</span><div class="meal-info"><strong>${esc(meal.label)}</strong><small>${fmt(meal.protein)} g protein${meal.logged_at ? ' · '+esc(formatTime(meal.logged_at)) : ''}</small></div><div class="meal-calories">${fmt(meal.calories)}<small>kcal</small></div><button class="remove-meal" data-meal-id="${esc(meal.id)}" type="button">Remove</button></div>`).join('') : '<div class="empty-state">No meals logged today.</div>';
  for(const button of document.querySelectorAll('.remove-meal'))button.addEventListener('click',()=>mealAction(button.dataset.mealId,false,button));
  renderStatus(data.status);
  $('#event-count').textContent=`${fmt(data.event_count)} WEARABLE EVENTS`;
}
function formatTime(value) { const date=new Date(value); return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleTimeString(undefined,{hour:'numeric',minute:'2-digit',timeZone:'UTC'}); }
async function refresh(propagateError=false) {
  $('#refresh').disabled=true;
  try { renderDashboard(await api('/api/dashboard')); setError(''); if(currentView==='engineering') await loadEngineering(); }
  catch(error) { setError(error.message); $('#system-label').textContent='Connection needs attention'; if(propagateError===true) throw error; }
  finally { $('#refresh').disabled=false; }
}
function toolsMarkup(tools) {
  return tools.map(tool=>`<div class="tool-row"><strong>${esc(tool.name || tool.tool || 'Tool call')}</strong><span class="duration">${Number.isFinite(Number(tool.ms)) ? `${fmt(tool.ms)} ms` : ''}</span>${tool.detail ? `<p>${esc(display(tool.detail))}</p>` : ''}${tool.sql ? `<pre>${esc(tool.sql)}</pre>` : ''}</div>`).join('');
}
function addMessage(role,text) {
  const element=document.createElement('div'); element.className=`message ${role}`;
  if(role==='assistant') { const label=document.createElement('div'); label.className='message-label'; label.textContent='DAYLIGHT';element.appendChild(label); }
  const content=document.createElement('div'); content.textContent=text;element.appendChild(content);
  $('#conversation').appendChild(element); return element;
}
function scrollChat() { $('#conversation').scrollTop=$('#conversation').scrollHeight; }
function draftCard(draft, sessionId=conversationId) {
  const card=document.createElement('div');card.className='draft-card';card.dataset.draftId=draft.id;
  function paint(value) {
    draft=value;
    card.innerHTML=`<h3>${esc(draft.label || 'Your meal')}</h3><p class="draft-subtitle">Check the portions before adding this to your day.</p><div class="draft-items">${(draft.items||[]).map((item,index)=>`<label class="draft-item"><span>${esc(item.name)}</span><input type="number" aria-label="${esc(item.name)} grams" data-index="${index}" min="1" max="2000" step="1" value="${Number(item.grams)||100}"><small>g</small></label>`).join('')}</div><div class="draft-total"><span>${fmt(draft.calories)} kcal</span><span>${fmt(draft.protein)} g protein</span></div><div class="draft-actions"><button class="update-button" type="button">Update portions</button><button class="confirm-button" type="button">Confirm & log meal</button></div><p class="draft-notice">Nutrition comes from matched catalog entries. Nothing is logged until you confirm.</p>`;
    const buttons=card.querySelectorAll('button');
    const portions=()=>Array.from(card.querySelectorAll('input')).map(input=>({food_id:draft.items[Number(input.dataset.index)].food_id,grams:Number(input.value)}));
    const readPortions=()=>{ if(Array.from(card.querySelectorAll('input')).some(input=>!input.checkValidity())) throw new Error('Choose a portion between 1 and 2,000 grams.'); return portions(); };
    async function action(confirm) {
      buttons.forEach(b=>b.disabled=true);
      try {
        const values=readPortions();
        const changed=values.some((v,i)=>v.grams!==Number(draft.items[i].grams));
        if(changed || !confirm) { const response=await api('/api/drafts/update',{draft_id:draft.id,portions:values,conversation_id:sessionId}); draft=response.draft; if(!draft) throw new Error('Updated meal was not returned.'); if(confirm && changed) { paint(draft); card.querySelector('.draft-notice').textContent='Totals updated. Review them, then confirm to save.'; return; } }
        if(confirm) { const response=await api('/api/meals/confirm',{draft_id:draft.id,conversation_id:sessionId}); if(!response.ok) throw new Error('Meal could not be confirmed.'); card.innerHTML=`<h3>Saved to your journal ✓</h3><p class="draft-subtitle">${esc(draft.label)} · ${fmt(draft.calories)} kcal · ${fmt(draft.protein)} g protein</p>`; await refresh(); }
        else paint(draft);
      } catch(error) { let notice=card.querySelector('.draft-notice'); if(notice) { notice.textContent=error.message;notice.classList.add('error-text'); }buttons.forEach(b=>b.disabled=false); }
      scrollChat();
    }
    card.querySelector('.update-button').addEventListener('click',()=>action(false));
    card.querySelector('.confirm-button').addEventListener('click',()=>action(true));
    card.querySelectorAll('input').forEach(input=>input.addEventListener('input',()=>{ card.querySelector('.draft-notice').textContent='Portions changed. Totals will recalculate when you update or confirm.'; }));
  }
  paint(draft);return card;
}
async function send(message) {
  message=message.trim();if(!message || chatBusy)return;
  chatBusy=true;$('#send').disabled=true;$('#chat-input').disabled=true;
  $('.welcome')?.remove();addMessage('user',message);$('#chat-input').value='';
  const pending=addMessage('assistant','Looking at your data…');pending.classList.add('loading-message');scrollChat();
  try {
    const result=await api('/api/chat',{message,conversation_id:conversationId});pending.remove();
    const answer=addMessage('assistant',result.answer || 'The request completed.');
    if(result.tools?.length) { localTools.unshift(...result.tools); const details=document.createElement('details');details.className='tool-details';details.innerHTML=`<summary>${result.tools.length} tool ${result.tools.length===1?'call':'calls'} · Details</summary>${toolsMarkup(result.tools)}`;answer.appendChild(details); }
    if(result.removals?.length){for(const meal of result.removals){const card=document.createElement('div');card.className='draft-card';card.innerHTML=`<h3>${esc(meal.label)}</h3><p>${esc(meal.logged_at)} · ${fmt(meal.calories)} kcal</p><button type="button">Remove</button>`;card.querySelector('button').addEventListener('click',()=>mealAction(meal.id,false,card.querySelector('button')));$('#conversation').appendChild(card);}}
    if(result.draft)$('#conversation').appendChild(draftCard(result.draft));
  }catch(error){pending.remove();const errorMessage=addMessage('assistant',`I couldn’t complete that request. ${error.message}`);errorMessage.classList.add('error-text');}
  finally{chatBusy=false;$('#send').disabled=false;$('#chat-input').disabled=false;$('#chat-input').focus();scrollChat();}
}
async function loadEngineering() {
  try {
    const result=await api('/api/engineering');
    const counts=Array.isArray(result.counts)?result.counts:[];
    $('#event-counts').innerHTML=counts.length?counts.map(c=>`<div class="count-row"><span>${esc(c.kind)}</span><strong>${fmt(c.count)}</strong></div>`).join(''):'<div class="empty-state">No events recorded yet.</div>';
    const tools=result.tools?.length?result.tools:localTools;
    $('#engineering-tools').innerHTML=tools.length?toolsMarkup(tools):'<div class="empty-state">Ask the assistant a question to see the work behind its answer.</div>';
    $('#event-list').innerHTML=(result.events||[]).slice(0,30).map(event=>`<div class="event-row"><time>${esc(formatTime(event.recorded_at || event.created_at || event.timestamp || event.event_time || ''))}</time><strong>${esc(event.kind || event.event_type || event.type || 'Event')}</strong><pre>${esc(JSON.stringify(event,null,2))}</pre></div>`).join('') || '<div class="empty-state">No recent events.</div>';
    if(result.pipeline && typeof result.pipeline==='object') {
      const lines=Object.entries(result.pipeline).map(([key,value])=>`<div class="capability-row"><span>${esc(key.replace(/_/g,' '))}</span><span>${esc(display(value))}</span></div>`).join('');
      if(lines)$('#capabilities').innerHTML+=`<div class="pipeline-details">${lines}</div>`;
    }
  }catch(error){$('#engineering-tools').textContent=error.message;throw error;}
}
for(const button of document.querySelectorAll('[data-view]'))button.addEventListener('click',async()=>{currentView=button.dataset.view;document.querySelectorAll('.view').forEach(view=>view.hidden=view.id!==currentView);document.querySelectorAll('[data-view]').forEach(nav=>nav.classList.toggle('active',nav===button));$('#page-title').textContent=currentView==='overview'?'My day':'Under the hood';if(currentView==='engineering'){await refresh();}});
for(const button of document.querySelectorAll('[data-prompt]'))button.addEventListener('click',()=>send(button.dataset.prompt));
$('#chat-form').addEventListener('submit',event=>{event.preventDefault();send($('#chat-input').value);});
$('#chat-input').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();send(event.target.value);}});
let activityTimer = null;
let activityTickBusy = false;
function stopActivityStream() {
  if(activityTimer !== null) clearInterval(activityTimer);
  activityTimer = null;
  $('#activity-stream').textContent = 'Stream synthetic activity · Off';
  $('#activity-stream').setAttribute('aria-pressed','false');
}
async function activityTick() {
  if(activityTickBusy || activityTimer === null) return;
  activityTickBusy = true;
  try { await api('/api/activity/tick',{}); await refresh(true); }
  catch(error) {
    stopActivityStream();
    $('#activity-stream-error').textContent = `Activity stream stopped: ${error.message}`;
    $('#activity-stream-error').hidden = false;
  } finally { activityTickBusy = false; }
}
$('#activity-stream').addEventListener('click',()=>{
  if(activityTimer !== null) { stopActivityStream(); return; }
  $('#activity-stream-error').hidden = true;
  $('#activity-stream').textContent = 'Stream synthetic activity · On';
  $('#activity-stream').setAttribute('aria-pressed','true');
  activityTimer = setInterval(activityTick,3000);
  activityTick();
});
window.addEventListener('pagehide',stopActivityStream);
window.addEventListener('beforeunload',stopActivityStream);
$('#refresh').addEventListener('click',()=>refresh());
$('#date-label').textContent=new Date().toLocaleDateString(undefined,{weekday:'long',month:'long',day:'numeric',timeZone:'UTC'}).toUpperCase()+' · UTC';
refresh();

$('#food-search-form').addEventListener('submit',async event=>{
  event.preventDefault();
  const button=$('#food-search-button');button.disabled=true;
  $('#food-results').replaceChildren();$('#food-search-details').hidden=true;
  $('#food-search-status').textContent='Searching…';
  try {
    const result=await api('/api/foods/search',{query:$('#food-query').value.trim(),mode:'hybrid'});
    $('#food-search-status').textContent=result.foods.length ? `${result.foods.length} matches · nutrition per 100 g` : 'No matches. Try a different name.';
    $('#food-search-tools').innerHTML=toolsMarkup(result.tools);$('#food-search-details').hidden=false;
    for(const food of result.foods){
      const row=document.createElement('div');row.className='food-result';
      row.innerHTML=`<div><strong>${esc(food.name)}</strong><small>${fmt(food.calories)} kcal · ${fmt(food.protein)} g protein per 100 g</small></div><button type="button">Choose</button>`;
      row.querySelector('button').addEventListener('click',async()=>{
        const choose=row.querySelector('button');choose.disabled=true;
        try {
          const response=await api('/api/drafts/manual',{label:food.name,items:[{food_id:food.id,grams:Math.min(2000,Math.max(1,Math.round(Number(food.serving_grams)||100)))}]});
          $('#manual-draft').replaceChildren(draftCard(response.draft,null));
          $('#manual-draft').scrollIntoView({behavior:'smooth',block:'nearest'});
        }catch(error){$('#food-search-status').textContent=error.message;}
        finally{choose.disabled=false;}
      });
      $('#food-results').appendChild(row);
    }
  }catch(error){$('#food-search-status').textContent=error.message;}
  finally{button.disabled=false;}
});

async function mealAction(id,restore,button){
  button.disabled=true;
  try{
    const result=await api(restore?'/api/meals/restore':'/api/meals/remove',{meal_id:id});
    button.textContent=restore?'Restored':'Removed';
    const status=$('#meal-action-status');status.replaceChildren(document.createTextNode(`${result.label} ${restore?'restored':'removed'}.${result.replication_pending?' Totals are still syncing.':''} `));
    if(!restore){const undo=document.createElement('button');undo.textContent='Undo';undo.addEventListener('click',()=>mealAction(id,true,undo));status.appendChild(undo);}
    await refresh();
  }catch(error){$('#meal-action-status').textContent=error.message;button.disabled=false;await refresh();}
}
