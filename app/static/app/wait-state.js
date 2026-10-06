// Live elapsed timer and stage copy for a running recommendation. The server
// renders the first state and the stage thresholds (#wait-stages), and htmx
// polls a status endpoint that returns 204 until the run finishes, so this
// keeps the timer moving between polls.
(() => {
  let timer = null;

  function stages() {
    const el = document.getElementById('wait-stages');
    return el ? JSON.parse(el.textContent) : [];
  }

  function stageFor(seconds, stages) {
    let text = '';
    for (const [at, label] of stages) {
      if (seconds >= at) text = label;
    }
    return text;
  }

  function stop() {
    if (timer !== null) clearInterval(timer);
    timer = null;
  }

  function start() {
    stop();
    const wait = document.querySelector('.wait');
    if (!wait) return;
    const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
    wait.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
    const secondsEl = wait.querySelector('.wait-seconds');
    const stageEl = wait.querySelector('.wait-stage');
    const stageList = stages();
    const rendered = parseInt(secondsEl?.textContent ?? '0', 10) || 0;
    const startedAt = Date.now() - rendered * 1000;
    timer = setInterval(() => {
      const seconds = Math.floor((Date.now() - startedAt) / 1000);
      if (secondsEl) secondsEl.textContent = String(seconds);
      if (stageEl) stageEl.textContent = stageFor(seconds, stageList);
    }, 1000);
  }

  start();
  document.body.addEventListener('htmx:afterSwap', start);
})();