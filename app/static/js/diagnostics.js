/* The Diagnostics tab: is this configured, and which categories will the
   selected api-version actually accept? */

const ENV_REFERENCE = [
  ['AZURE_LANGUAGE_ENDPOINT', 'Language resource endpoint', 'yes'],
  ['AZURE_LANGUAGE_KEY', 'Language key — omit to use az login instead', 'key auth'],
  ['AZURE_DOCUMENT_PII_API_VERSION', 'Default api-version; overridable per run', 'no'],
  ['AZURE_STORAGE_CONNECTION_STRING', 'Storage the API reads from and writes to', 'yes'],
  ['AZURE_STORAGE_ACCOUNT_URL', 'Storage account URL, for Entra ID auth', 'entra auth'],
  ['AZURE_STORAGE_SOURCE_CONTAINER', 'Container for staged sources (pii-source)', 'no'],
  ['AZURE_STORAGE_TARGET_CONTAINER', 'Container the service writes to (pii-target)', 'no'],
  ['AZURE_USE_ENTRA_ID', 'true to use DefaultAzureCredential instead of keys', 'no'],
];

async function loadDiagnostics() {
  const config = evalState.config;
  const host = clear($('diagReadiness'));

  const box = el('div', `coverageBox ${config && config.ready ? 'ok' : 'warn'}`);
  const body = el('div');
  const head = el('div');
  head.appendChild(el('strong', null, 'Document PII'));
  head.appendChild(el('span', `pill ${config && config.ready ? 'ok' : 'wait'}`,
    config && config.ready ? 'ready' : 'not configured'));
  body.appendChild(head);
  body.appendChild(el('div', 'small muted',
    config && config.ready
      ? `${config.endpoint} · default api-version ${config.default_api_version}`
      : (config && config.missing.join('; ')) || 'configuration could not be read'));
  box.appendChild(body);
  host.appendChild(box);

  const table = clear($('envTable'));
  ENV_REFERENCE.forEach(([name, purpose, required]) => {
    const row = el('tr');
    const cell = el('td');
    cell.appendChild(el('code', null, name));
    row.appendChild(cell);
    row.appendChild(el('td', null, purpose));
    row.appendChild(el('td', 'small muted', required));
    table.appendChild(row);
  });

  renderCategorySupport();
}

async function renderCategorySupport() {
  const host = clear($('diagCategories'));
  let categories;
  try {
    categories = await api('/api/evaluate/categories');
  } catch (error) {
    host.appendChild(banner('err', 'Could not load the category taxonomy', error.message));
    return;
  }

  const versions = categories.api_versions || [];
  const rows = categories.categories || [];
  host.appendChild(el('div', 'small muted',
    `${rows.length} categories in the taxonomy.`));

  const strip = el('div', 'chunkStrip');
  versions.forEach((version) => {
    const accepted = rows.filter((c) => (c.document_pii_api_versions || []).includes(version));
    const cell = el('div', 'chunkCell');
    cell.appendChild(el('span', 'chunkPages', version));
    cell.appendChild(el('span', 'chunkMeta', `${accepted.length} of ${rows.length} accepted`));
    strip.appendChild(cell);
  });
  host.appendChild(strip);

  versions.forEach((version) => {
    const rejected = rows
      .filter((c) => !(c.document_pii_api_versions || []).includes(version))
      .map((c) => c.tag);
    if (!rejected.length) return;
    const details = el('details', 'catGroup');
    const summary = el('summary');
    summary.appendChild(el('span', null, `${version} rejects ${rejected.length}`));
    details.appendChild(summary);
    const list = el('div', 'catGrid');
    rejected.forEach((tag) => list.appendChild(el('span', 'small muted', tag)));
    details.appendChild(list);
    host.appendChild(details);
  });
}
