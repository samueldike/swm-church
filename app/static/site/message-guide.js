(() => {
 const init = () => {
 const request = async (endpoint, body) => {
  const response = await fetch(endpoint, {method:'POST',body,signal:AbortSignal.timeout(65000)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'ScentAI is unavailable. Please try again.');
  return data;
 };
 const sources = (container, messages, events = false) => (messages || []).forEach(message => {
  const link = document.createElement('a'); link.href = message.url; link.textContent = (events ? 'Event · ' : 'Listen / download · ') + message.title; link.className = 'ai-source'; container.append(link);
 });
 const telegram = (container, url) => {if(!url || !/^https?:\/\//i.test(url)) return;const a=document.createElement('a');a.href=url;a.target='_blank';a.rel='noopener';a.className='ai-source';a.textContent='Join our Telegram channel ↗';container.append(a);};
 const overview = document.querySelector('[data-overview]');
 if (overview && !overview.dataset.ready) {
  overview.dataset.ready = '1'; const body = new FormData(); body.set('question',overview.dataset.question); body.set('csrf_token',overview.dataset.token);
  request(overview.dataset.endpoint,body).then(data => {overview.querySelector('[data-overview-text]').textContent = data.answer; sources(overview.querySelector('[data-overview-sources]'),data.messages); sources(overview.querySelector('[data-overview-sources]'),data.events,true);telegram(overview.querySelector('[data-overview-sources]'),data.telegram_url);}).catch(error => {overview.querySelector('[data-overview-text]').textContent = error.message;});
 }
 const form = document.querySelector('#scentai-form, #message-discuss-form'); if (!form || form.dataset.ready) return;
 form.dataset.ready = '1'; let history = []; let busy = false;
 const field = form.querySelector('textarea'), button = form.querySelector('button'), log = document.querySelector('#ai-conversation'), welcome = document.querySelector('#ai-welcome');
 const scroll = () => {const pane = document.querySelector('#ai-scroll'); if(pane) pane.scrollTop = pane.scrollHeight;};
 const turn = (role,text) => {const article = document.createElement('article'); article.className = 'ai-turn ai-turn-' + role; const label = document.createElement('strong');label.textContent = role === 'user' ? 'You' : '✦ ScentAI'; const p = document.createElement('p');p.textContent = text;article.append(label,p);log.append(article);scroll();return article;};
 form.addEventListener('submit', async event => {
  event.preventDefault(); const question = field.value.trim(); if (busy || !question) return;
  busy = true; button.disabled = true; if(welcome) welcome.hidden = true;
  turn('user',question); const reply = turn('assistant','Exploring the teachings…'); reply.setAttribute('aria-busy','true');
  const body = new FormData(form);body.set('question',question);body.set('history',JSON.stringify(history));field.value = '';
  try {const data = await request(form.action,body);reply.querySelector('p').textContent = data.answer;sources(reply,data.messages);sources(reply,data.events,true);telegram(reply,data.telegram_url);history.push({role:'user',text:question},{role:'assistant',text:data.answer});history=history.slice(-8);}
  catch(error){reply.querySelector('p').textContent = error.message || 'Connection failed. Please try again.';field.value = question;}
  finally {busy=false;button.disabled=false;reply.setAttribute('aria-busy','false');scroll();field.focus();}
 });
 field.addEventListener('keydown',event => {if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();form.requestSubmit();}});
 document.querySelectorAll('[data-prompt]').forEach(el => el.addEventListener('click',() => {field.value=el.dataset.prompt;form.requestSubmit();}));
 document.querySelector('[data-new-chat]')?.addEventListener('click',() => {if(busy)return;history=[];log.replaceChildren();welcome.hidden=false;field.value='';window.history.replaceState({},'',location.pathname);field.focus();});
 const initial = new URLSearchParams(location.search).get('q');if(initial){field.value=initial.slice(0,1200);form.requestSubmit();}
 };
 init();document.addEventListener('turbo:load',init);
})();
