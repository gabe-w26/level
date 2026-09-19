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
