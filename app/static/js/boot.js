/* Wiring, in dependency order. Loaded last. */

async function boot() {
  wireTabs();
  wireOverlay();
  wireEvaluate();

  try {
    const health = await api('/api/health');
    $('healthDot').className = 'statusDot ok';
    $('healthText').textContent = health.app || 'ready';
  } catch (error) {
    $('healthDot').className = 'statusDot bad';
    $('healthText').textContent = 'backend unreachable';
    return;
  }

  try {
    await loadEvaluateConfig();
    loadCategoryPicker();
    markPreviewOnlyFeatures();
    $('evalSynAdd').addEventListener('click', () => addSynonymRow('Person', ''));
    loadEvaluationHistory();
    loadDiagnostics();
  } catch (error) {
    clear($('evalReadiness')).appendChild(
      banner('err', 'Could not load the configuration', error.message));
  }
}

document.addEventListener('DOMContentLoaded', boot);
