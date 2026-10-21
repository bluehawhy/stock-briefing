'use strict';
const form = document.getElementById('login-form');
form.addEventListener('submit', async event => {
  event.preventDefault();
  const button = form.querySelector('button');
  const message = document.getElementById('login-message');
  button.disabled = true;
  message.textContent = '로그인 중…';
  try {
    const response = await fetch('/stock-briefing/api/login', {
      method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: {'Content-Type': 'application/json', 'X-Dashboard-Request': '1'},
      body: JSON.stringify({username: form.username.value, password: form.password.value}),
      signal: AbortSignal.timeout(15000)
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(typeof data.detail === 'string' ? data.detail : '로그인하지 못했습니다. 입력 내용을 확인하세요.');
    }
    window.location.replace('/stock-briefing/');
  } catch (error) {
    message.textContent = error.name === 'TimeoutError' ? '응답이 지연됩니다. 다시 시도하세요.' : error.message;
    form.password.value = '';
    form.password.focus();
  } finally { button.disabled = false; }
});
