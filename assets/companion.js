(() => {
  'use strict';
  const base = document.body.dataset.base || './';
  const key = 'mqc-companion-final-2026-11-01';
  const mobileNav = document.querySelector('.mobile-nav');
  const smallScreen = matchMedia('(max-width:760px)');
  if (mobileNav) {
    mobileNav.open = !smallScreen.matches;
    smallScreen.addEventListener('change', event => { mobileNav.open = !event.matches; });
  }
  const state = {read: [], last: null};
  let storageOK = true;
  try {
    const saved = JSON.parse(localStorage.getItem(key) || '{}');
    state.read = Array.isArray(saved.read) ? [...new Set(saved.read.filter(n => Number.isInteger(n) && n >= 1 && n <= 5))] : [];
    state.last = Number.isInteger(saved.last) && saved.last >= 1 && saved.last <= 5 ? saved.last : null;
    localStorage.setItem(key, JSON.stringify(state));
  } catch { storageOK = false; }
  const updateProgress = () => {
    document.querySelectorAll('[data-read]').forEach(el => el.hidden = !state.read.includes(Number(el.dataset.read)));
    const progress = document.querySelector('.reading-progress');
    if (progress) {
      progress.hidden = false;
      progress.querySelector('progress').value = state.read.length;
      progress.querySelector('[data-progress-label]').textContent = `${state.read.length} of 5 chapters marked as read`;
      progress.querySelector('small').textContent = storageOK ? 'Saved in this browser.' : 'Progress is temporary; browser storage is unavailable.';
    }
    document.querySelectorAll('.mark-read').forEach(b => {
      b.hidden = false;
      const yes = state.read.includes(Number(b.dataset.chapter));
      b.setAttribute('aria-pressed', String(yes));
      b.textContent = yes ? '✓ Chapter marked as read · Undo' : 'Mark chapter as read';
    });
  };
  const save = () => { try { localStorage.setItem(key, JSON.stringify(state)); } catch {storageOK = false;} updateProgress(); };
  const resume = document.querySelector('[data-resume]');
  if (resume && state.last) { resume.textContent = `Continue Chapter ${state.last} →`; resume.href = `${base}chapters/chapter-${state.last}.html`; }
  const chapter = document.querySelector('.mark-read');
  if (chapter) {
    state.last = Number(chapter.dataset.chapter); save();
    chapter.addEventListener('click', () => {
      const n = Number(chapter.dataset.chapter);
      state.read = state.read.includes(n) ? state.read.filter(v => v !== n) : [...state.read,n]; save();
    });
  }
  updateProgress();
  // Deep links to glossary entries open their explanation automatically.
  const revealHash = () => {
    let id; try {id = decodeURIComponent(location.hash.slice(1));} catch {return;}
    const target = document.getElementById(id);
    if (target?.tagName === 'DETAILS') target.open = true;
  };
  revealHash(); window.addEventListener('hashchange', revealHash);
  const searchOpen = document.querySelector('.search-open');
  if (!searchOpen || !Array.isArray(window.COMPANION_SEARCH)) return;
  const dialog = document.createElement('dialog'); dialog.className = 'search-dialog'; dialog.setAttribute('aria-labelledby','guide-search-label');
  dialog.innerHTML = '<div class="search-dialog-top"><button class="search-dialog-close" type="button" aria-label="Close search">×</button><label id="guide-search-label" for="guide-search">Find a section or a concept</label><input id="guide-search" type="search" placeholder="Try: 4.5, point recall, humidity…" autocomplete="off" aria-controls="guide-search-results"><p class="search-status" role="status" aria-live="polite"></p></div><ul class="search-results" id="guide-search-results"></ul>';
  document.body.append(dialog);
  const input = dialog.querySelector('input'), results = dialog.querySelector('.search-results'), status = dialog.querySelector('.search-status');
  const normalize = str => str.toLowerCase().normalize('NFKD').replace(/[\u0300-\u036f]/g,'');
  const render = () => {
    const words = normalize(input.value.trim()).split(/\s+/).filter(Boolean);
    const matches = window.COMPANION_SEARCH.filter(item => words.every(word => normalize(`${item.title} ${item.description} ${item.category}`).includes(word)));
    const shown = words.length ? matches : window.COMPANION_SEARCH.filter(x => x.category.startsWith('Chapter')).slice(0,7);
    results.replaceChildren();
    status.textContent = words.length ? `${matches.length} ${matches.length === 1 ? 'match' : 'matches'}` : 'Search chapter numbers, headings, methods and key terms.';
    for (const item of shown) {
      const li = document.createElement('li'), a = document.createElement('a'), tag = document.createElement('small'), title = document.createElement('strong'), p = document.createElement('p');
      a.href = base + item.url; tag.textContent = item.category; title.textContent = item.title; p.textContent = item.description;
      a.append(tag,title,p); li.append(a); results.append(li);
      a.addEventListener('click',() => dialog.close());
    }
    if (!shown.length) { const li = document.createElement('li'); li.className = 'search-empty'; li.textContent = 'No matching section. Try a shorter phrase, a section number or a term such as “coverage”.'; results.append(li); }
  };
  searchOpen.hidden = false;
  searchOpen.addEventListener('click', () => {dialog.showModal(); render(); input.focus();});
  dialog.querySelector('.search-dialog-close').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => searchOpen.focus());
  dialog.addEventListener('click', e => { if(e.target === dialog) {const box = dialog.getBoundingClientRect(); if(e.clientX < box.left || e.clientX > box.right || e.clientY < box.top || e.clientY > box.bottom) dialog.close();} });
  input.addEventListener('input',render);
})();
