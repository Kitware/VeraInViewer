// Measure actual browser VTK renders, including interaction-driven renders.
// StartEvent -> EndEvent is wall time, not a GPU timestamp measurement. No
// per-frame trame state changes, network messages, or forced renders are needed.
(function () {
  const WINDOW_SIZE = 120;
  const REPORT_MS = 500;
  let enabled = false;
  let target = null;
  let observers = [];
  let timer = null;
  let started = null;
  let samples = [];
  let total = 0;
  let reported = -1;

  function readout(text) {
    const element = document.getElementById("vera-frame-times");
    if (element) element.textContent = text;
  }

  function snapshot() {
    const sorted = [...samples].sort((a, b) => a - b);
    return {
      requestedBackend: target?.backend ?? null,
      renderWindow: target?.className ?? null,
      frames: total,
      samples: samples.length,
      lastMs: samples.at(-1) ?? null,
      meanMs: samples.length ? samples.reduce((a, b) => a + b, 0) / samples.length : null,
      p95Ms: sorted.length ? sorted[Math.ceil(sorted.length * 0.95) - 1] : null,
    };
  }

  function report() {
    if (!target || reported === total) return;
    reported = total;
    const stats = snapshot();
    const heading = `${target.backend} requested · ${target.className}`;
    readout(
      stats.samples
        ? `${heading}\nVTK render wall time (not GPU time)\n` +
          `Last ${stats.lastMs.toFixed(2)} ms · Mean ${stats.meanMs.toFixed(2)} ms\n` +
          `p95 ${stats.p95Ms.toFixed(2)} ms · Last ${stats.samples} renders`
        : `${heading}\nWaiting for renders — rotate, pan, or zoom`,
    );
  }

  function reset() {
    started = null;
    samples = [];
    total = 0;
    reported = -1;
    report();
  }

  function stop() {
    clearInterval(timer);
    timer = null;
    for (const tag of observers) target.native.unObserve(target.id, tag);
    observers = [];
    started = null;
  }

  function start() {
    if (!enabled || !target || observers.length) return;
    try {
      observers.push(target.native.observe(target.id, "StartEvent", () => {
        started = document.hidden ? null : performance.now();
      }));
      observers.push(target.native.observe(target.id, "EndEvent", () => {
        if (started === null) return;
        const ms = performance.now() - started;
        started = null;
        if (document.hidden || !Number.isFinite(ms) || ms < 0) return;
        samples.push(ms);
        if (samples.length > WINDOW_SIZE) samples.shift();
        total += 1;
        // Opt in explicitly: logging each frame can affect the measurement.
        if (window.veraFrameTiming.logFrames) {
          console.log(`[Vera frame] ${target.backend} requested / ${target.className}: ${ms.toFixed(3)} ms`);
        }
      }));
      timer = setInterval(report, REPORT_MS);
      report();
    } catch (error) {
      stop();
      readout("Frame timing unavailable — see browser console");
      console.error("Unable to observe VTK render events", error);
    }
  }

  function detach() {
    stop();
    target = null;
    reset();
    readout("Waiting for view…");
  }

  function attach(view, id, backend) {
    detach();
    try {
      const native = view.getRemoteSession().native;
      // Report the actual window class as well as the requested backend. Older
      // WASM builds may use OpenGL even when WebGPU was requested.
      target = { native, id, backend, className: native.get(id).ClassName };
      start();
    } catch (error) {
      readout("Frame timing unavailable — see browser console");
      console.error("Unable to attach VTK frame timing", error);
    }
  }

  function setEnabled(value) {
    enabled = Boolean(value);
    stop();
    reset();
    start();
  }

  // Do not include a render suspended while the tab was in the background.
  document.addEventListener("visibilitychange", () => { started = null; });
  window.veraFrameTiming = { attach, detach, setEnabled, reset, snapshot, logFrames: false };
})();
