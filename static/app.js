// Countdowns. The server sends seconds remaining (so the admin demo clock is
// respected); we count down from page load.
(function () {
  const els = Array.from(document.querySelectorAll('[data-countdown]'));
  if (!els.length) return;
  const start = Date.now();
  const base = els.map((el) => Number(el.dataset.seconds) || 0);
  const fmt = (s) => {
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    if (s >= 172800) return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`;
    return h ? `${h}h ${String(m).padStart(2, '0')}m` : `${m}m ${String(sec).padStart(2, '0')}s`;
  };
  const tick = () => {
    const gone = Math.floor((Date.now() - start) / 1000);
    els.forEach((el, i) => {
      const s = Math.max(0, base[i] - gone);
      const suffix = el.dataset.suffix !== undefined ? el.dataset.suffix : ' left';
      el.textContent = s > 0 ? (el.dataset.prefix || '') + fmt(s) + suffix : 'Time’s up';
      el.classList.toggle('urgent', s > 0 && s < 4 * 3600);
      el.classList.toggle('done', s === 0);
    });
  };
  tick();
  setInterval(tick, 1000);
})();

// Quote form: show the price fields that match the chosen pricing type.
document.querySelectorAll('[data-price-form]').forEach((form) => {
  const sync = () => {
    const picked = form.querySelector('input[name=price_type]:checked');
    const v = picked ? picked.value : '';
    form.querySelectorAll('[data-show-for]').forEach((el) => {
      el.hidden = !el.dataset.showFor.split(' ').includes(v);
    });
  };
  form.addEventListener('change', sync);
  sync();
});

// Ask before irreversible actions.
document.addEventListener('submit', (e) => {
  const msg = e.target.dataset && e.target.dataset.confirm;
  if (msg && !window.confirm(msg)) e.preventDefault();
});

// Installable app: service worker, offline banner, add-to-home-screen.
(function () {
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => navigator.serviceWorker.register('/sw.js').catch(() => {}));
  }

  const paintOffline = () => document.body.classList.toggle('is-offline', !navigator.onLine);
  window.addEventListener('online', paintOffline);
  window.addEventListener('offline', paintOffline);
  paintOffline();

  const buttons = () => Array.from(document.querySelectorAll('.install-btn'));
  let installPrompt = null;
  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    installPrompt = e;
    buttons().forEach((b) => { b.hidden = false; });
  });
  document.addEventListener('click', async (e) => {
    const btn = e.target.closest && e.target.closest('.install-btn');
    if (!btn || !installPrompt) return;
    installPrompt.prompt();
    await installPrompt.userChoice;
    installPrompt = null;
    buttons().forEach((b) => { b.hidden = true; });
  });

  // iPhones don't offer a prompt, so show Safari's steps instead.
  const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent);
  const installed = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone;
  if (isIOS && !installed) {
    document.querySelectorAll('.ios-install').forEach((el) => { el.hidden = false; });
  }
})();

// Landing page board: one job moving through the rules.
(function () {
  const board = document.querySelector('[data-board]');
  if (!board) return;
  const seats = Array.from(board.querySelectorAll('.seat'));
  const meter = Array.from(board.querySelectorAll('[data-board-meter] i'));
  const count = board.querySelector('[data-board-count]');
  const clock = board.querySelector('[data-board-clock]');
  const status = board.querySelector('[data-board-status]');
  const stamp = board.querySelector('.stamp');

  const set = (seat, cls, text) => {
    seat.className = 'seat' + (cls ? ' ' + cls : '');
    seat.querySelector('b').textContent = text;
  };
  const quotes = (n) => {
    meter.forEach((m, i) => m.classList.toggle('on', i < n));
    count.textContent = `${n}/6`;
  };
  const finalState = () => {
    const quoted = [1, 6, 10, 3, 0, 12];
    seats.forEach((s, i) => set(s, quoted.includes(i) ? 'quoted' : 'closed', quoted.includes(i) ? 'Quoted' : 'Closed'));
    quotes(6);
    clock.textContent = 'Hour 31';
    status.textContent = 'Full — closed to everyone else';
    stamp.classList.add('is-full');
  };

  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    finalState();
    return;
  }

  const firstQuotes = [1, 6, 10, 3];
  const lateQuotes = [0, 12];
  let timers = [];
  const at = (ms, fn) => timers.push(setTimeout(fn, ms));

  const run = () => {
    timers.forEach(clearTimeout);
    timers = [];
    stamp.classList.remove('is-full');
    seats.forEach((s) => set(s, '', 'Deciding'));
    quotes(0);
    clock.textContent = 'Hour 0';
    status.textContent = '15 trades deciding';

    firstQuotes.forEach((i, k) => at(1100 + k * 900, () => {
      set(seats[i], 'quoted', 'Quoted');
      quotes(k + 1);
      clock.textContent = `Hour ${[2, 5, 9, 16][k]}`;
      status.textContent = `${k + 1} quoted · ${14 - k} deciding`;
    }));

    const t24 = 1100 + firstQuotes.length * 900 + 900;
    at(t24, () => {
      clock.textContent = 'Hour 24';
      status.textContent = '11 didn’t quote — slots handed on';
      seats.forEach((s, i) => { if (!firstQuotes.includes(i)) set(s, 'gone', 'Out'); });
    });
    at(t24 + 1000, () => {
      seats.forEach((s, i) => { if (!firstQuotes.includes(i)) set(s, 'fresh', 'New trade'); });
      status.textContent = '11 new trades deciding';
    });

    lateQuotes.forEach((i, k) => at(t24 + 2200 + k * 1000, () => {
      set(seats[i], 'quoted', 'Quoted');
      quotes(5 + k);
      clock.textContent = `Hour ${[27, 31][k]}`;
      status.textContent = k ? '6 quotes in' : '5 quoted';
    }));

    at(t24 + 4300, () => {
      seats.forEach((s, i) => { if (![...firstQuotes, ...lateQuotes].includes(i)) set(s, 'closed', 'Closed'); });
      status.textContent = 'Full — closed to everyone else';
      stamp.classList.add('is-full');
    });
    at(t24 + 8800, run);
  };

  // Start when the board is on screen; pause the loop when it isn't.
  const io = new IntersectionObserver((entries) => {
    entries.forEach((en) => {
      if (en.isIntersecting) run();
      else { timers.forEach(clearTimeout); timers = []; }
    });
  }, { threshold: 0.35 });
  io.observe(board);
})();

// Quote templates: fill the quote form from the chosen template. Prices stay
// blank on purpose — every job is priced fresh.
document.querySelectorAll('[data-template-select]').forEach((select) => {
  select.addEventListener('change', () => {
    const option = select.selectedOptions[0];
    if (!option || !option.dataset.fields) return;
    const fields = JSON.parse(option.dataset.fields);
    const form = select.closest('form');
    Object.entries(fields).forEach(([name, value]) => {
      if (!value) return;
      if (name === 'price_type') {
        const radio = form.querySelector(`input[name=price_type][value="${value}"]`);
        if (radio) { radio.checked = true; radio.dispatchEvent(new Event('change', { bubbles: true })); }
        return;
      }
      const field = form.querySelector(`[name="${name}"]`);
      if (field) field.value = value;
    });
  });
});

// Copy an invite link.
document.addEventListener('click', async (e) => {
  const button = e.target.closest && e.target.closest('[data-copy]');
  if (!button) return;
  const source = button.parentElement.querySelector('[data-copy-source]');
  if (!source) return;
  try {
    await navigator.clipboard.writeText(source.value);
  } catch (err) {
    source.select();
    document.execCommand('copy');
  }
  const label = button.textContent;
  button.textContent = 'Copied';
  setTimeout(() => { button.textContent = label; }, 1500);
});

// "Help me describe it": send the rough notes, get back a clear brief to edit.
document.querySelectorAll('[data-ai-help]').forEach((box) => {
  const form = box.closest('form');
  const go = box.querySelector('[data-ai-go]');
  const status = box.querySelector('[data-ai-status]');
  const questions = box.querySelector('[data-ai-questions]');
  go.addEventListener('click', async () => {
    const body = new FormData();
    body.append('_csrf', box.dataset.csrf);
    ['category', 'title', 'description'].forEach((name) => {
      const field = form.querySelector(`[name="${name}"]`);
      body.append(name, field ? field.value : '');
    });
    go.disabled = true;
    status.textContent = 'Working on it…';
    try {
      const resp = await fetch(box.dataset.url, { method: 'POST', body, credentials: 'same-origin' });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || 'Something went wrong. Try again.');
      if (data.title) form.querySelector('[name=title]').value = data.title;
      if (data.description) form.querySelector('[name=description]').value = data.description;
      if (data.value_band) {
        const radio = form.querySelector(`input[name=value_band][value="${data.value_band}"]`);
        if (radio) radio.checked = true;
      }
      const list = questions.querySelector('ul');
      list.innerHTML = '';
      (data.questions || []).forEach((q) => {
        const li = document.createElement('li');
        li.textContent = q;
        list.appendChild(li);
      });
      questions.hidden = !(data.questions || []).length;
      status.textContent = 'Done — have a read and change anything that isn’t quite right.';
    } catch (err) {
      status.textContent = err.message;
    } finally {
      go.disabled = false;
    }
  });
});
