// Run with: node --test tests/frame_timing.test.cjs
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { test } = require("node:test");
const { runInNewContext } = require("node:vm");

const source = readFileSync(join(__dirname, "../src/vera_in_viewer/module/serve/frame_timing.js"), "utf8");

function setup() {
  let now = 0;
  let nextTag = 0;
  const observers = new Map();
  const timers = new Set();
  const logs = [];
  const element = { textContent: "" };
  const visibility = {};
  const document = {
    hidden: false,
    getElementById: () => element,
    addEventListener: (event, callback) => { visibility[event] = callback; },
  };
  const native = {
    get: () => ({ ClassName: "vtkWebAssemblyOpenGLRenderWindow" }),
    observe: (id, event, callback) => {
      const tag = ++nextTag;
      observers.set(tag, { id, event, callback });
      return tag;
    },
    unObserve: (id, tag) => {
      assert.equal(observers.get(tag).id, id);
      observers.delete(tag);
    },
  };
  const view = { getRemoteSession: () => ({ native }) };
  const window = {};
  runInNewContext(source, {
    window, document, performance: { now: () => now },
    console: { log: (line) => logs.push(line), error: (message, error) => { throw error; } },
    setInterval: (callback) => { timers.add(callback); return callback; },
    clearInterval: (callback) => timers.delete(callback),
  });
  const emit = (event) => {
    for (const observer of observers.values()) {
      if (observer.event === event) observer.callback();
    }
  };
  return {
    api: window.veraFrameTiming, view, native, observers, timers, logs, element, document, visibility, emit,
    advance: (ms) => { now += ms; },
    frame: (ms) => { emit("StartEvent"); now += ms; emit("EndEvent"); },
    report: () => { for (const callback of timers) callback(); },
  };
}

test("measures renders rather than idle time, and reports rolling mean/p95", () => {
  const h = setup();
  h.api.attach(h.view, 1, "webgl");
  assert.equal(h.observers.size, 0);
  h.api.setEnabled(true);
  for (let ms = 1; ms <= 125; ms++) {
    h.advance(10000);
    h.frame(ms);
  }
  const stats = h.api.snapshot();
  assert.equal(stats.frames, 125);
  assert.equal(stats.samples, 120);
  assert.equal(stats.lastMs, 125);
  assert.equal(stats.meanMs, 65.5); // Last 120 durations: 6 through 125 ms.
  assert.equal(stats.p95Ms, 119);
  h.report();
  assert.match(h.element.textContent, /Mean 65.50 ms/);
  assert.equal(h.logs.length, 0);
});

test("toggles and backend remounts remove observers and reset samples", () => {
  const h = setup();
  h.api.setEnabled(true);
  h.api.attach(h.view, 1, "webgl");
  h.frame(4);
  h.api.setEnabled(false);
  assert.equal(h.observers.size, 0);
  assert.equal(h.timers.size, 0);
  h.frame(9);
  assert.equal(h.api.snapshot().frames, 0);
  h.api.setEnabled(true);
  h.api.setEnabled(true);
  assert.equal(h.observers.size, 2);
  assert.equal(h.timers.size, 1);
  h.frame(5);
  h.api.attach(h.view, 2, "webgpu");
  assert.equal(h.observers.size, 2);
  assert.equal(h.timers.size, 1);
  assert.equal(h.api.snapshot().frames, 0);
  assert.equal(h.api.snapshot().requestedBackend, "webgpu");
  assert.equal(h.api.snapshot().renderWindow, "vtkWebAssemblyOpenGLRenderWindow");
  h.frame(7);
  assert.equal(h.api.snapshot().frames, 1);
  h.api.detach();
  assert.equal(h.observers.size, 0);
  assert.equal(h.timers.size, 0);
  assert.equal(h.api.snapshot().renderWindow, null);
});

test("ignores unmatched ends and renders spanning a hidden tab", () => {
  const h = setup();
  h.api.attach(h.view, 1, "webgl");
  h.api.setEnabled(true);
  h.emit("EndEvent");
  h.emit("StartEvent");
  h.document.hidden = true;
  h.visibility.visibilitychange();
  h.advance(60000);
  h.document.hidden = false;
  h.visibility.visibilitychange();
  h.emit("EndEvent");
  assert.equal(h.api.snapshot().frames, 0);
  h.frame(2);
  assert.equal(h.api.snapshot().lastMs, 2);
});

test("console logging is opt in; reset clears a partial render", () => {
  const h = setup();
  h.api.attach(h.view, 1, "webgl");
  h.api.setEnabled(true);
  h.frame(3);
  assert.equal(h.logs.length, 0);
  h.api.logFrames = true;
  h.frame(4);
  assert.match(h.logs[0], /webgl.*4.000 ms/);
  h.emit("StartEvent");
  h.api.reset();
  h.emit("EndEvent");
  assert.equal(h.api.snapshot().samples, 0);
  h.report();
  assert.match(h.element.textContent, /Waiting for renders/);
});
