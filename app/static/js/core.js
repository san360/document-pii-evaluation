/* Shared state, DOM helpers and chrome.
   No bundler: every file is a classic script sharing one global scope, loaded
   in the order index.html lists them. */

/* Build a node rather than interpolating HTML: filenames, entity values and
   service messages are derived from an uploaded document and must never be
   parsed. */
function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

function $(id) { return document.getElementById(id); }

function fmtBytes(bytes) {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
  return `${value.toFixed(unit === 0 ? 0 : value >= 100 ? 0 : 1)} ${units[unit]}`;
}

function fmtDuration(ms) {
  if (ms === undefined || ms === null) return '—';
  if (ms < 1000) return `${ms} ms`;
  const seconds = ms / 1000;
  if (seconds < 90) return `${seconds.toFixed(1)}s`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

function fmtWhen(iso) {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString();
}

function banner(kind, title, lines) {
  const node = el('div', `banner ${kind}`);
  node.appendChild(el('strong', null, title));
  if (Array.isArray(lines)) {
    const list = el('ul');
    lines.forEach((line) => list.appendChild(el('li', null, line)));
    node.appendChild(list);
  } else if (lines) {
    node.appendChild(el('div', null, lines));
  }
  return node;
}

/* ------------------------------------------------------------------ tabs */
function showTab(name) {
  document.querySelectorAll('nav.tabs .tab').forEach((tab) => {
    tab.classList.toggle('active', tab.dataset.tab === name);
  });
  document.querySelectorAll('main .panel').forEach((panel) => {
    panel.classList.toggle('active', panel.id === `tab-${name}`);
  });
}

function wireTabs() {
  $('tabs').addEventListener('click', (event) => {
    const tab = event.target.closest('.tab');
    if (tab) showTab(tab.dataset.tab);
  });
  // Changing the submission mode changes the job count in the hint.
  document.addEventListener('change', (event) => {
    if (event.target.name === 'evalMode') updateEvaluateButton();
  });
}

/* --------------------------------------------------------- segmented set */
function wireSegmented(container, onChange) {
  container.addEventListener('click', (event) => {
    const button = event.target.closest('.segBtn');
    if (!button) return;
    container.querySelectorAll('.segBtn').forEach((b) => b.classList.remove('active'));
    button.classList.add('active');
    onChange(button.dataset.value);
  });
}

/* -------------------------------------------------------------- overlays */
function showJson(title, payload) {
  $('jsonTitle').textContent = title;
  $('jsonBody').textContent = JSON.stringify(payload, null, 2);
  $('jsonOverlay').classList.add('open');
}

function wireOverlay() {
  $('jsonClose').addEventListener('click', () => $('jsonOverlay').classList.remove('open'));
  $('jsonOverlay').addEventListener('click', (event) => {
    if (event.target === $('jsonOverlay')) $('jsonOverlay').classList.remove('open');
  });
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    $('jsonOverlay').classList.remove('open');
    const docOverlay = $('docOverlay');
    if (docOverlay) docOverlay.classList.remove('open');
  });
}
