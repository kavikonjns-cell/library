/* Book details + loan request on /books (dialog / bottom sheet) and on /books/<code> (plain page). */
(function () {
  var tg = window.Telegram && window.Telegram.WebApp;
  var inApp = !!(tg && tg.initData);
  var keyboardApp = !inApp && !!(tg && tg.sendData && tg.platform && tg.platform !== 'unknown');
  var signedIn = !!window.BOOKS_SIGNED_IN;
  var dialog = document.getElementById('bookDialog');
  var body = document.getElementById('dlgBody');
  var trigger = null, dirty = false, openCode = null;

  function post(url, data) {
    if (inApp) data.initData = tg.initData;
    return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', body: JSON.stringify(data) })
      .then(function (r) { return r.json().then(function (j) { return j; }); });
  }

  // --------------------------------------------------------- one book view (works in dialog and on the page)
  function wire(root) {
    var det = root.querySelector('[data-step=details]'), bor = root.querySelector('[data-step=borrow]');
    if (!det || !bor) return;
    var code = root.querySelector('.book-detail').dataset.code;
    var form = bor.querySelector('[data-borrow-form]'), notice = bor.querySelector('[data-notice]');
    var submit = bor.querySelector('[data-submit]'), retry = bor.querySelector('[data-retry]');
    var phase = 'idle', requestKey = null;

    function step(name) {
      det.hidden = name !== 'details'; bor.hidden = name !== 'borrow';
      var h = (name === 'details' ? det : bor).querySelector('h2'); if (h) { h.id = 'dlgTitle'; h.tabIndex = -1; h.focus(); }
    }
    function say(kind, text) { notice.hidden = !text; notice.dataset.kind = kind || ''; notice.textContent = text || ''; }
    function setPhase(p, text) {
      phase = p;
      form.setAttribute('aria-busy', p === 'submitting');
      submit.disabled = p === 'submitting' || p === 'success';
      submit.textContent = p === 'submitting' ? 'در حال ثبت…' : 'ثبت درخواست';
      retry.hidden = p !== 'error'; submit.hidden = p === 'error';
      form.querySelectorAll('input[name=days]').forEach(function (r) { r.disabled = p === 'submitting' || p === 'success'; });
      if (p === 'success') say('success', text); else if (p === 'error') say('error', text); else say('', '');
    }
    root.querySelectorAll('[data-go-borrow]').forEach(function (b) { b.addEventListener('click', function () { setPhase('idle'); step('borrow'); }); });
    bor.querySelector('[data-back]').addEventListener('click', function () { step('details'); });
    root.querySelectorAll('[data-tg-only]').forEach(function (b) { if (keyboardApp) { b.hidden = false; var n = root.querySelector('[data-guest-note]'); if (n) n.hidden = true; } });
    root.querySelectorAll('[data-close]').forEach(function (b) { b.addEventListener('click', function () { closeBook(); }); });

    function send() {
      if (phase === 'submitting' || phase === 'success') return;
      var days = +(form.querySelector('input[name=days]:checked') || { value: 14 }).value;
      if (keyboardApp && !signedIn) {   // the bot answers in the chat; the web app closes itself
        tg.sendData(JSON.stringify({ t: 'request', code: code, days: days })); return;
      }
      setPhase('submitting'); requestKey = requestKey || String(Date.now());
      post('/api/request', { code: code, days: days }).then(function (r) {
        if (r.ok) { dirty = true; setPhase('success', r.message || 'درخواست ثبت شد.'); if (inApp && tg.HapticFeedback) tg.HapticFeedback.notificationOccurred('success'); }
        else setPhase('error', r.message || r.detail || 'ثبت درخواست ممکن نشد.');
      }).catch(function () { setPhase('error', 'ارتباط برقرار نشد. دوباره امتحان کنید.'); });
    }
    form.addEventListener('submit', function (e) { e.preventDefault(); send(); });
    retry.addEventListener('click', send);
  }

  if (window.BOOK_PAGE) {
    var page = document.getElementById('standalone'); if (page) wire(page);
    if (inApp && !signedIn) post('/api/me', {}).then(function (d) {   // Telegram web app: sign in to the site, then reload once
      try { if (d.member && !sessionStorage.getItem('tgsync')) { sessionStorage.setItem('tgsync', '1'); location.reload(); } } catch (e) {}
    }).catch(function () {});
    return;
  }
  if (!dialog) return;
  if (inApp && !signedIn) post('/api/me', {}).then(function (d) {
    try { if (d.member && !sessionStorage.getItem('tgsync')) { sessionStorage.setItem('tgsync', '1'); location.reload(); } } catch (e) {}
  }).catch(function () {});

  // --------------------------------------------------------- dialog
  function show(html) { body.innerHTML = html; wire(body); }
  function loadBook(code) {
    body.innerHTML = '<p class="result-state" role="status">در حال دریافت…</p>';
    return fetch('/books/' + encodeURIComponent(code) + '?fragment=1', { credentials: 'same-origin' })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
      .then(show)
      .catch(function () {
        body.innerHTML = '<div class="result-state"><h3 id="dlgTitle">دریافت اطلاعات کتاب ممکن نشد</h3><button class="button" type="button" id="dlgRetry">تلاش دوباره</button> <button class="button" type="button" data-close>بستن</button></div>';
        body.querySelector('#dlgRetry').onclick = function () { loadBook(code); };
        body.querySelector('[data-close]').onclick = function () { closeBook(); };
      });
  }
  function openBook(code, link, push) {
    trigger = link || document.activeElement; openCode = code; dirty = false;
    if (!dialog.open) { dialog.showModal(); document.documentElement.style.overflow = 'hidden'; }
    if (push) history.pushState({ book: code }, '', '/books/' + code);
    loadBook(code);
  }
  function finish() {
    if (dialog.open) dialog.close();
    document.documentElement.style.overflow = ''; openCode = null; body.innerHTML = '';
    if (dirty) { dirty = false; location.reload(); return; }
    if (trigger && document.contains(trigger)) trigger.focus();
  }
  function closeBook() { if (history.state && history.state.book) history.back(); else finish(); }

  document.addEventListener('click', function (e) {
    var a = e.target.closest('a[data-book], a.cover-link'); if (!a || e.metaKey || e.ctrlKey || e.shiftKey || e.button) return;
    var code = a.dataset.book || (a.getAttribute('href') || '').split('/').pop(); if (!code) return;
    e.preventDefault(); openBook(code, a.closest('li').querySelector('a[data-book]'), true);
  });
  dialog.addEventListener('cancel', function (e) { e.preventDefault(); closeBook(); });
  dialog.addEventListener('click', function (e) {
    if (e.target === dialog && !dialog.querySelector('[data-step=borrow]:not([hidden]) [aria-busy=true]')) closeBook();
  });
  window.addEventListener('popstate', function (e) {
    if (e.state && e.state.book) { if (!dialog.open || openCode !== e.state.book) openBook(e.state.book, trigger, false); }
    else if (dialog.open) finish();
  });
  // a page opened directly at /books/<code> is its own page, so nothing to do here
})();
