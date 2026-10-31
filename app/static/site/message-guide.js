(() => {
 const init = () => {
 const form = document.querySelector('#message-ask');
 if (!form || form.dataset.ready) return;
 form.dataset.ready = '1';
 const question = form.querySelector('textarea'), result = document.querySelector('#guide-result'), submit = form.querySelector('button');
 document.querySelectorAll('.guide-prompts button').forEach(button => button.addEventListener('click', () => { question.value = button.textContent; question.focus(); }));
 form.addEventListener('submit', async event => {
 event.preventDefault(); if (submit.disabled) return;
 submit.disabled = true; result.hidden = false; result.textContent = 'Exploring the message transcripts…'; result.setAttribute('aria-busy', 'true');
 try {
 const response = await fetch(form.action, {method: 'POST', body: new FormData(form), signal: AbortSignal.timeout(65000)});
 const data = await response.json();
 result.replaceChildren();
 const text = document.createElement('p'); text.textContent = data.answer || data.error || 'Unable to answer. Please try again.'; result.append(text);
 (data.messages || []).forEach(message => { const a = document.createElement('a'); a.href = message.url; a.className = 'btn btn-ghost'; a.textContent = 'Listen · ' + message.title; result.append(a); });
 } catch { result.textContent = 'ScentAI could not connect. Please try again or browse the messages below.'; }
 finally { submit.disabled = false; result.setAttribute('aria-busy', 'false'); }
 });
 };
 init(); document.addEventListener('turbo:load', init);
})();
