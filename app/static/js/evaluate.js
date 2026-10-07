/* The Evaluate tab — a thin surface over evaluate_document_pii.py.
   Everything here is presentation; the request itself is built server-side by
   the same function the CLI calls. */

const evalState = {
  config: null,
  files: [],
  timer: null,
  since: 0,
  started: 0,
  result: null,
};

async function loadEvaluateConfig() {
  const config = await api('/api/evaluate/config');
  evalState.config = config;

  $('evalApiPill').textContent = config.default_api_version || '—';

  const versions = clear($('evalApiVersion'));
  config.api_versions.forEach((version) => {
    const option = el('option', null,
      version === config.default_api_version ? `${version} (default)` : version);
    option.value = version;
    if (version === config.default_api_version) option.selected = true;
    versions.appendChild(option);
  });
  versions.addEventListener('change', () => {
    markUnsupportedCategories();
    markPreviewOnlyFeatures();
  });

  const policies = clear($('evalPolicy'));
  config.policies.forEach((policy) => {
    const option = el('option', null, policy);
    option.value = policy;
    policies.appendChild(option);
  });
  policies.addEventListener('change', updatePolicyControls);

  const modes = clear($('evalModes'));
  config.modes.forEach((mode, index) => {
    const row = el('div', 'row');
    row.style.marginTop = index ? '8px' : '0';
    const label = el('label', 'check');
    const input = el('input');
    input.type = 'radio';
    input.name = 'evalMode';
    input.value = mode.value;
    input.checked = index === 0;
    label.appendChild(input);
    label.appendChild(el('span', null, mode.label));
    row.appendChild(label);
    row.appendChild(el('span', 'small muted', mode.hint));
    modes.appendChild(row);
  });

  const readiness = clear($('evalReadiness'));
  if (!config.ready) {
    readiness.appendChild(banner('warn', 'Document PII is not configured', [
      `Missing: ${config.missing.join(', ')}`,
      'This path needs a Language endpoint and a storage account — the API reads its '
      + 'source from a blob and writes its output to a container you own.',
    ]));
  }
  updateEvaluateButton();
}

/* ------------------------------------------------------------- uploads */
function wireEvaluate() {
  const drop = $('evalDrop');
  const input = $('evalFiles');

  drop.addEventListener('click', () => input.click());
  drop.addEventListener('dragover', (event) => { event.preventDefault(); drop.classList.add('over'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', (event) => {
    event.preventDefault();
    drop.classList.remove('over');
    addEvaluateFiles(event.dataTransfer.files);
  });
  input.addEventListener('change', () => addEvaluateFiles(input.files));

  $('evalRun').addEventListener('click', startEvaluation);
  $('evalRefresh').addEventListener('click', loadEvaluationHistory);
  wireDocumentOverlay();
  $('evalShowJson').addEventListener('click', () => {
    if (evalState.result) showJson(`${evalState.result.run_id} — summary.json`, evalState.result);
  });
}

function addEvaluateFiles(fileList) {
  Array.from(fileList).forEach((file) => {
    if (!/\.(pdf|docx|txt)$/i.test(file.name)) return;
    if (evalState.files.some((f) => f.name === file.name && f.size === file.size)) return;
    evalState.files.push(file);
  });
  renderEvaluateFiles();
}

function renderEvaluateFiles() {
  const host = clear($('evalFileList'));
  if (!evalState.files.length) { updateEvaluateButton(); return; }

  evalState.files.forEach((file, index) => {
    const card = el('div', 'fileCard');
    card.appendChild(el('span', 'pill other', file.name.split('.').pop().toUpperCase()));
    const body = el('div');
    body.appendChild(el('div', 'fileName', file.name));
    body.appendChild(el('div', 'small muted', fmtBytes(file.size)));
    card.appendChild(body);
    card.appendChild(el('div', 'spacer'));
    const remove = el('button', 'ghost', 'remove');
    remove.addEventListener('click', () => {
      evalState.files.splice(index, 1);
      renderEvaluateFiles();
    });
    card.appendChild(remove);
    host.appendChild(card);
  });
  updateEvaluateButton();
}

function updateEvaluateButton() {
  const ready = evalState.config && evalState.config.ready;
  const count = evalState.files.length;
  $('evalRun').disabled = !ready || !count || evalState.timer !== null;

  if (!ready) {
    $('evalHint').textContent = 'Document PII is not configured — see Diagnostics.';
  } else if (!count) {
    $('evalHint').textContent = 'Add at least one document.';
  } else {
    const mode = document.querySelector('input[name="evalMode"]:checked');
    const jobs = mode && mode.value === 'batch' ? 1 : count;
    $('evalHint').textContent =
      `${count} document(s) · ${jobs} job${jobs === 1 ? '' : 's'} to the service.`;
  }
}

/* ----------------------------------------------------------------- run */
/* The category picker. Document PII rejects the whole request over a single
   unsupported category, so anything the chosen api-version does not accept is
   marked and excluded here rather than sent and bounced. */
function selectedCategories() {
  return Array.from(
    document.querySelectorAll('#evalCatList input[type=checkbox]:checked'))
    .filter((box) => !box.disabled)
    .map((box) => box.value);
}

async function loadCategoryPicker() {
  const host = $('evalCatList');
  if (!host) return;
  let payload;
  try {
    payload = await api('/api/evaluate/categories');
  } catch (error) {
    host.appendChild(el('div', 'small muted',
      `Could not load the taxonomy: ${error.message}`));
    return;
  }
  evalState.categories = payload.categories || [];

  const groups = new Map();
  evalState.categories.forEach((row) => {
    const name = row.group || 'Other';
    if (!groups.has(name)) groups.set(name, []);
    groups.get(name).push(row);
  });

  clear(host);
  for (const [name, rows] of groups) {
    const block = el('div', 'catGroup');
    const head = el('div', 'catGroupHead');
    head.appendChild(el('span', 'catGroupName', name));
    const all = el('button', 'ghost small', 'all');
    all.type = 'button';
    all.addEventListener('click', () => {
      block.querySelectorAll('input:not(:disabled)').forEach((b) => { b.checked = true; });
      updateCategorySummary();
    });
    head.appendChild(all);
    block.appendChild(head);

    rows.forEach((row) => {
      const label = el('label', 'check catItem');
      label.title = row.description || row.name;
      const box = el('input');
      box.type = 'checkbox';
      box.value = row.tag || row.name;
      box.dataset.versions = (row.document_pii_api_versions || []).join(' ');
      box.addEventListener('change', updateCategorySummary);
      label.appendChild(box);
      label.appendChild(el('span', null, row.tag || row.name));
      block.appendChild(label);
    });
    host.appendChild(block);
  }

  markUnsupportedCategories();
  updateCategorySummary();

  $('evalCatNone').addEventListener('click', () => {
    host.querySelectorAll('input').forEach((b) => { b.checked = false; });
    updateCategorySummary();
  });

  $('evalCatFilter').addEventListener('input', (event) => {
    const needle = event.target.value.trim().toLowerCase();
    host.querySelectorAll('.catItem').forEach((item) => {
      item.style.display =
        !needle || item.textContent.toLowerCase().includes(needle) ? '' : 'none';
    });
    host.querySelectorAll('.catGroup').forEach((group) => {
      const visible = Array.from(group.querySelectorAll('.catItem'))
        .some((item) => item.style.display !== 'none');
      group.style.display = visible ? '' : 'none';
    });
  });
}

function markUnsupportedCategories() {
  const version = $('evalApiVersion')?.value;
  if (!version) return;
  document.querySelectorAll('#evalCatList .catItem').forEach((item) => {
    const box = item.querySelector('input');
    const versions = (box.dataset.versions || '').split(' ').filter(Boolean);
    // An empty list means no api-version accepts this category, which is the
    // opposite of the engine's "unprobed version, pass it through" rule.
    const supported = versions.includes(version);
    box.disabled = !supported;
    if (!supported) box.checked = false;
    item.classList.toggle('catUnsupported', !supported);
    item.title = supported
      ? box.value
      : `${box.value} — not accepted by ${version}`;
  });
  updateCategorySummary();
}

function updateCategorySummary() {
  const chosen = selectedCategories();
  const label = $('evalCatSummary');
  if (!label) return;
  label.textContent = chosen.length
    ? `${chosen.length} selected`
    : 'all categories';
  label.classList.toggle('accent', chosen.length > 0);
}

/* Preview-only features. The server refuses these on the GA version, so the UI
   disables them rather than letting a run fail on submit. */
function isPreviewVersion() {
  const previews = evalState.config?.preview_api_versions || [];
  return previews.includes($('evalApiVersion')?.value);
}

function markPreviewOnlyFeatures() {
  const preview = isPreviewVersion();
  const previewPolicies = evalState.config?.preview_only_policies || [];
  const select = $('evalPolicy');

  Array.from(select.options).forEach((option) => {
    const previewOnly = previewPolicies.includes(option.value);
    option.disabled = previewOnly && !preview;
    option.textContent = previewOnly ? `${option.value} (preview)` : option.value;
  });
  if (select.selectedOptions[0]?.disabled) select.value = 'characterMask';

  const synBox = $('evalSynBox');
  synBox.classList.toggle('disabledBox', !preview);
  synBox.querySelectorAll('input, select, button').forEach((control) => {
    control.disabled = !preview;
  });
  if (!preview) synBox.open = false;
  $('evalSynHelp').classList.toggle('warn', !preview);

  updatePolicyControls();
  updateSynonymSummary();
}

function updatePolicyControls() {
  $('evalSyntheticRow').style.display =
    $('evalPolicy').value === 'syntheticReplacement' ? '' : 'none';
}

/* ---- entity synonyms ---- */
function addSynonymRow(entityType, words) {
  const host = $('evalSynRows');
  const row = el('div', 'row synRow');
  row.style.marginTop = '6px';

  const select = el('select');
  select.style.minWidth = '150px';
  (evalState.config?.synonym_entity_types || []).forEach((type) => {
    const option = el('option', null, type);
    option.value = type;
    if (type === entityType) option.selected = true;
    select.appendChild(option);
  });

  const input = el('input');
  input.type = 'text';
  input.placeholder = 'Insured, Applicant, Policyholder';
  input.value = words || '';
  input.style.flex = '1';
  input.style.minWidth = '160px';
  input.addEventListener('input', updateSynonymSummary);

  const remove = el('button', 'ghost small', 'remove');
  remove.type = 'button';
  remove.addEventListener('click', () => { row.remove(); updateSynonymSummary(); });

  row.appendChild(select);
  row.appendChild(input);
  row.appendChild(remove);
  host.appendChild(row);

  if (!isPreviewVersion()) {
    row.querySelectorAll('input, select, button').forEach((c) => { c.disabled = true; });
  }
  updateSynonymSummary();
}

function collectSynonyms() {
  if (!isPreviewVersion()) return [];
  return Array.from(document.querySelectorAll('#evalSynRows .synRow'))
    .map((row) => ({
      entityType: row.querySelector('select').value,
      synonyms: row.querySelector('input').value
        .split(',').map((w) => w.trim()).filter(Boolean),
    }))
    .filter((group) => group.synonyms.length);
}

function updateSynonymSummary() {
  const groups = collectSynonyms();
  const total = groups.reduce((n, g) => n + g.synonyms.length, 0);
  const label = $('evalSynSummary');
  if (!label) return;
  label.textContent = !isPreviewVersion()
    ? 'preview only'
    : total ? `${total} word(s) across ${groups.length} type(s)` : 'none';
  label.classList.toggle('accent', total > 0);
}

async function startEvaluation() {
  const form = new FormData();
  evalState.files.forEach((file) => form.append('files', file));
  const mode = document.querySelector('input[name="evalMode"]:checked');
  form.append('mode', mode ? mode.value : 'per-document');
  form.append('api_version', $('evalApiVersion').value);
  form.append('policy', $('evalPolicy').value);
  form.append('language', $('evalLanguage').value || 'en-US');
  form.append('domain', $('evalDomain').value);
  form.append('keep_uploads', $('evalKeepUploads').checked ? 'true' : 'false');
  form.append('categories', selectedCategories().join(','));
  form.append('preserve_data_format', $('evalPreserveFormat').checked ? 'true' : 'false');
  const synonyms = collectSynonyms();
  if (synonyms.length) form.append('entity_synonyms', JSON.stringify(synonyms));

  $('evalProgress').style.display = '';
  $('evalSpin').style.display = '';
  $('evalResultCard').style.display = 'none';
  clear($('evalLog'));
  evalState.since = 0;
  evalState.started = Date.now();
  setEvalProgress(4, 'uploading…');

  try {
    const started = await api('/api/evaluate', { method: 'POST', body: form });
    evalState.timer = setInterval(() => pollEvaluation(started.run_id), 1500);
    updateEvaluateButton();
    pollEvaluation(started.run_id);
  } catch (error) {
    appendEvalEvent({ stage: 'error', message: error.message, at: new Date().toISOString() });
    setEvalProgress(100, 'failed');
    $('evalSpin').style.display = 'none';
  }
}

async function pollEvaluation(runId) {
  let payload;
  try {
    payload = await api(`/api/evaluate/jobs/${encodeURIComponent(runId)}?since=${evalState.since}`);
  } catch (error) {
    stopEvaluationPolling();
    appendEvalEvent({ stage: 'error', message: error.message, at: new Date().toISOString() });
    return;
  }

  (payload.events || []).forEach(appendEvalEvent);
  evalState.since = payload.event_count || evalState.since;
  $('evalElapsed').textContent = fmtDuration(Date.now() - evalState.started);

  if (payload.status === 'running') {
    setEvalProgress(Math.min(92, 8 + evalState.since * 5), 'working…');
    return;
  }

  stopEvaluationPolling();
  setEvalProgress(100, payload.status);
  $('evalSpin').style.display = 'none';
  if (payload.error) {
    appendEvalEvent({ stage: 'error', message: payload.error, at: new Date().toISOString() });
  }
  if (payload.result) {
    renderEvaluationResult(payload.result);
    loadEvaluationHistory();
  }
}

function stopEvaluationPolling() {
  if (evalState.timer) clearInterval(evalState.timer);
  evalState.timer = null;
  updateEvaluateButton();
}

function setEvalProgress(percent, stage) {
  $('evalFill').style.width = `${percent}%`;
  $('evalStage').textContent = stage;
}

function appendEvalEvent(event) {
  const row = el('tr');
  const time = el('td', 'small muted');
  time.style.width = '74px';
  time.textContent = new Date(event.at).toLocaleTimeString();
  row.appendChild(time);
  row.appendChild(el('td', null, event.message));
  $('evalLog').appendChild(row);
  const wrap = $('evalLog').closest('.tableWrap');
  if (wrap) wrap.scrollTop = 1e6;
}

/* -------------------------------------------------------------- result */
function renderEvaluationResult(summary) {
  evalState.result = summary;
  $('evalResultCard').style.display = '';
  $('evalResultMeta').textContent =
    `${summary.api_version} · ${summary.mode} · ${summary.policy} · lang ${summary.language}`;

  const documents = summary.documents || [];
  const body = clear($('evalResults'));

  documents.forEach((document_) => {
    const row = el('tr');
    row.appendChild(el('td', null, document_.filename));

    const kind = el('td');
    kind.appendChild(el('span', `pill ${document_.kind === 'scanned' ? 'warn' : 'other'}`, document_.kind));
    row.appendChild(kind);

    const result = el('td');
    const ok = document_.status === 'succeeded';
    const pill = el('span', `pill ${ok ? 'ok' : 'bad'}`, ok ? 'OK' : document_.status);
    if (document_.error) pill.title = document_.error;
    result.appendChild(pill);
    row.appendChild(result);

    row.appendChild(el('td', 'num', `${document_.duration_seconds}s`));
    row.appendChild(el('td', 'num', document_.entity_count));

    const categories = Object.entries(document_.categories || {});
    row.appendChild(el('td', 'small',
      categories.slice(0, 4).map(([k, v]) => `${k}:${v}`).join(', ') || '—'));

    const artifacts = el('td');
    const links = el('div', 'pillRow');
    const stem = document_.filename.replace(/\.[^.]+$/, '');

    if (document_.redacted_artifact) {
      const view = el('button', 'pill ok', 'view');
      view.title = 'Open the redacted document beside the original';
      view.addEventListener('click', () => openDocumentCompare(summary.run_id, document_));
      links.appendChild(view);
    }
    if (document_.result_artifact) {
      links.appendChild(artifactLink(summary.run_id,
        `${stem}/${document_.result_artifact}`, 'result.json', false));
    }
    if (document_.redacted_artifact) {
      links.appendChild(artifactLink(summary.run_id,
        `${stem}/${document_.redacted_artifact}`, 'download', true));
    }
    artifacts.appendChild(links);
    row.appendChild(artifacts);

    body.appendChild(row);
  });

  renderEvaluationVerdict(documents);
}

function artifactUrl(runId, path, download) {
  return `/api/evaluate/runs/${encodeURIComponent(runId)}/artifact`
    + `?path=${encodeURIComponent(path)}${download ? '&download=true' : ''}`;
}

function artifactLink(runId, path, label, download) {
  const url = artifactUrl(runId, path, download);
  const button = el('button', 'pill', label);
  button.addEventListener('click', async () => {
    if (download) { window.location.href = url; return; }
    try {
      const payload = await api(url);
      showJson(path, payload);
    } catch (error) {
      showJson(path, { error: error.message });
    }
  });
  return button;
}

/* --------------------------------------------------- document compare */
const docCompareState = { runId: null, document: null };

function openDocumentCompare(runId, document_) {
  docCompareState.runId = runId;
  docCompareState.document = document_;

  $('docTitle').textContent = document_.filename;
  const pill = $('docEntityPill');
  pill.textContent = `${document_.entity_count} PII · ${document_.kind}`;
  pill.className = `pill ${document_.kind === 'scanned' ? 'warn' : 'other'}`;

  document.querySelectorAll('#docView .segBtn').forEach((b) =>
    b.classList.toggle('active', b.dataset.value === 'side'));

  renderDocumentCompare('side');
  $('docOverlay').classList.add('open');
}

function renderDocumentCompare(view) {
  const { runId, document: doc } = docCompareState;
  if (!doc) return;
  const stem = doc.filename.replace(/\.[^.]+$/, '');
  const host = clear($('docCompare'));
  host.className = `docCompare ${view === 'side' ? 'side' : 'single'}`;

  const panes = [];
  if (view === 'side' || view === 'source') {
    panes.push({
      label: 'Original (as submitted)',
      artifact: doc.source_artifact,
      missing: 'The source was not kept for this run. Runs created before the '
        + 'viewer existed did not copy it; re-run to get a side-by-side.',
    });
  }
  if (view === 'side' || view === 'redacted') {
    panes.push({
      label: 'Redacted (returned by Document PII)',
      artifact: doc.redacted_artifact,
      missing: 'The service produced no redacted file for this document.',
    });
  }

  panes.forEach((pane) => {
    const wrapper = el('div', 'docPane');
    const head = el('div', 'docPaneHead');
    head.appendChild(el('strong', null, pane.label));
    wrapper.appendChild(head);

    if (pane.artifact) {
      const frame = el('iframe');
      frame.src = artifactUrl(runId, `${stem}/${pane.artifact}`, false) + '#view=FitH';
      frame.title = pane.label;
      wrapper.appendChild(frame);
    } else {
      wrapper.appendChild(el('div', 'docMissing', pane.missing));
    }
    host.appendChild(wrapper);
  });
}

function wireDocumentOverlay() {
  $('docClose').addEventListener('click', () => $('docOverlay').classList.remove('open'));
  $('docOverlay').addEventListener('click', (event) => {
    if (event.target === $('docOverlay')) $('docOverlay').classList.remove('open');
  });
  wireSegmented($('docView'), renderDocumentCompare);
  $('docDownload').addEventListener('click', () => {
    const { runId, document: doc } = docCompareState;
    if (!doc || !doc.redacted_artifact) return;
    const stem = doc.filename.replace(/\.[^.]+$/, '');
    window.location.href = artifactUrl(runId, `${stem}/${doc.redacted_artifact}`, true);
  });
}

function renderEvaluationVerdict(documents) {
  const host = clear($('evalVerdict'));
  const byKind = new Map();
  documents.forEach((d) => {
    if (!byKind.has(d.kind)) byKind.set(d.kind, []);
    byKind.get(d.kind).push(d);
  });

  const lines = [];
  byKind.forEach((group, kind) => {
    const ok = group.filter((d) => d.status === 'succeeded').length;
    lines.push(`${kind}: ${ok}/${group.length} succeeded`);
  });

  const allOk = documents.every((d) => d.status === 'succeeded');
  const box = el('div', `coverageBox ${allOk ? 'ok' : 'warn'}`);
  const text = el('div');
  text.appendChild(el('strong', null, allOk
    ? 'Every document was processed'
    : 'Some documents did not produce output'));
  text.appendChild(el('div', 'small muted', lines.join(' · ')));
  box.appendChild(text);
  host.appendChild(box);
}

/* ------------------------------------------------------------- history */
async function loadEvaluationHistory() {
  const host = $('evalHistory');
  host.className = 'empty';
  host.textContent = 'Loading…';

  let payload;
  try {
    payload = await api('/api/evaluate/runs');
  } catch (error) {
    clear(host).appendChild(banner('err', 'Could not list evaluations', error.message));
    return;
  }

  $('evalDir').textContent = payload.evaluations_dir || '';
  const runs = payload.runs || [];
  if (!runs.length) {
    host.className = 'empty';
    host.textContent = 'No evaluations yet.';
    return;
  }

  host.className = 'tableWrap';
  clear(host);
  const table = el('table');
  const head = el('thead');
  const headRow = el('tr');
  ['Run', 'When', 'api-version', 'Mode', 'Docs', 'PII', ''].forEach((title) => {
    headRow.appendChild(el('th', null, title));
  });
  head.appendChild(headRow);
  table.appendChild(head);

  const body = el('tbody');
  runs.forEach((run) => {
    const row = el('tr');
    row.appendChild(el('td', 'small', run.run_id));
    row.appendChild(el('td', 'small', fmtWhen(run.created_at)));
    row.appendChild(el('td', 'small', run.api_version));
    row.appendChild(el('td', 'small', run.mode));
    row.appendChild(el('td', 'num', `${run.succeeded}/${run.document_count}`));
    row.appendChild(el('td', 'num', run.total_entities));

    const actions = el('td');
    const group = el('div', 'pillRow');
    const open = el('button', 'ghost small evalOpen', 'open');
    open.addEventListener('click', async () => {
      try {
        const summary = await api(`/api/evaluate/runs/${encodeURIComponent(run.run_id)}`);
        renderEvaluationResult(summary);
        $('evalResultCard').scrollIntoView({ behavior: 'smooth', block: 'start' });
      } catch (error) {
        clear($('evalVerdict')).appendChild(banner('err', 'Could not open', error.message));
      }
    });
    group.appendChild(open);

    // Deliberately not a `.ghost` button: these folders hold PII evidence, and
    // a generic "click the first button in the row" — from a script, a stray
    // Enter, or a mis-aimed click — must not be able to reach this one.
    const remove = el('button', 'evalArchive', 'archive');
    remove.title = 'Moves this run to evaluations/.trash/ — recoverable by hand.';
    remove.addEventListener('click', async () => {
      if (!window.confirm(
        `Archive ${run.run_id}?\n\n`
        + 'It moves to evaluations/.trash/ and stays on disk, but leaves this list.'
      )) return;
      try {
        await api(`/api/evaluate/runs/${encodeURIComponent(run.run_id)}`, { method: 'DELETE' });
        loadEvaluationHistory();
      } catch (error) {
        clear($('evalVerdict')).appendChild(banner('err', 'Could not archive', error.message));
      }
    });
    group.appendChild(remove);
    actions.appendChild(group);
    row.appendChild(actions);
    body.appendChild(row);
  });
  table.appendChild(body);
  host.appendChild(table);
}
