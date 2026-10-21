import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = fs.readFileSync(new URL("../jev_ultrafast/static/app.js", import.meta.url), "utf8");
const ids = [
  "action-count", "auto", "choices", "choice-title", "choose", "completion", "confidence", "download",
  "empty", "error", "execute", "goal", "helper", "history", "latency", "model-state",
  "operation-choices", "overlays", "pace", "page-title", "plan", "ranking-note", "retry",
  "scenario", "screenshot", "start", "status", "step-count", "stop", "targets", "task-form", "url",
];

class FakeElement {
  constructor() {
    this.checked = false;
    this.disabled = false;
    this.hidden = false;
    this.innerHTML = "";
    this.style = {};
    this.listeners = new Map();
    this.textContent = "";
    this.value = "";
  }

  addEventListener(name, listener) {
    this.listeners.set(name, listener);
  }

  querySelector(selector) {
    this.children ??= new Map();
    if (!this.children.has(selector)) this.children.set(selector, new FakeElement());
    return this.children.get(selector);
  }
}

const serverState = ({ history = [], status = "ready", decision = null } = {}) => ({
  decision,
  decisions: [],
  elapsed_ms: 25,
  elements: [],
  goal: "Inspect the fixture",
  history,
  max_steps: 8,
  page: {
    actions: [],
    fingerprint: "fresh",
    h: 100,
    screenshot: "",
    text: "Fixture",
    title: "Fixture",
    url: "https://example.test/",
    w: 100,
  },
  plan: [],
  plan_index: 0,
  status,
  text_model: "test-model",
});

const jsonResponse = (data, { ok = true, status = 200 } = {}) => ({
  json: async () => structuredClone(data),
  ok,
  status,
});

const malformedResponse = () => ({
  json: async () => {
    throw new SyntaxError("bad json");
  },
  ok: true,
  status: 200,
});

async function settle() {
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
}

async function load(responses) {
  const elements = Object.fromEntries(ids.map((id) => [id, new FakeElement()]));
  elements.goal.value = "Inspect the fixture";
  elements.overlays.checked = true;
  elements.scenario.value = "research";
  const calls = [];
  const queue = [...responses];
  const context = vm.createContext({
    Blob,
    Error,
    JSON,
    Math,
    Object,
    Promise,
    String,
    URL: { createObjectURL: () => "blob:test", revokeObjectURL: () => {} },
    document: {
      createElement: () => ({ click() {} }),
      getElementById: (id) => (elements[id] ??= new FakeElement()),
      querySelector: () => ({ content: "test-token" }),
      querySelectorAll: () => [],
    },
    fetch: async (...args) => {
      calls.push(args);
      const response = queue.shift();
      if (response instanceof Error) throw response;
      if (!response) throw new Error("Unexpected fetch");
      return response;
    },
    setTimeout,
  });
  vm.runInContext(source, context);
  await settle();
  return {
    calls,
    click: async (id) => {
      await elements[id].listeners.get("click")({ preventDefault() {} });
      await settle();
    },
    elements,
    queue,
  };
}

test("initial offline state disables browser actions and offers retry", async () => {
  const app = await load([new TypeError("offline")]);
  assert.equal(app.elements.status.textContent, "Cannot reach local demo server");
  assert.equal(app.elements.retry.hidden, false);
  for (const id of ["start", "choose", "execute", "auto"]) assert.equal(app.elements[id].disabled, true);
});

test("disconnect preserves an exportable trace but disables stale browser actions", async () => {
  const history = [{ action: "Open", latency_ms: 4, page_changed: true, probability: 1, step: 1 }];
  const app = await load([jsonResponse(serverState({ history })), new TypeError("offline"), new TypeError("offline")]);
  await app.click("choose");
  assert.equal(app.elements.download.disabled, false);
  assert.match(app.elements.history.innerHTML, /Open/);
  assert.equal(app.elements.retry.hidden, false);
  for (const id of ["start", "choose", "execute", "auto"]) assert.equal(app.elements[id].disabled, true);
  assert.deepEqual(app.calls.map(([, options]) => options?.method || "GET"), ["GET", "POST", "GET"]);
});

test("retry rejects non-2xx and malformed state responses", async () => {
  const app = await load([new TypeError("offline")]);
  app.queue.push(jsonResponse({ error: "still offline" }, { ok: false, status: 503 }));
  await app.click("retry");
  assert.equal(app.elements.status.textContent, "Cannot reach local demo server");
  assert.match(app.elements.error.textContent, /still offline/);
  app.queue.push(malformedResponse());
  await app.click("retry");
  assert.match(app.elements.error.textContent, /Invalid response/);
  assert.equal(app.elements.start.disabled, true);
});

test("successful retry refreshes state with one GET and never replays the failed action", async () => {
  const app = await load([jsonResponse(serverState()), new TypeError("offline"), new TypeError("offline")]);
  await app.click("choose");
  const beforeRetry = app.calls.length;
  app.queue.push(jsonResponse(serverState()));
  await app.click("retry");
  assert.deepEqual(app.calls.slice(beforeRetry).map(([, options]) => options?.method || "GET"), ["GET"]);
  assert.equal(app.elements.retry.hidden, true);
  assert.equal(app.elements.start.disabled, false);
  assert.equal(app.elements.choose.disabled, false);
  assert.equal(app.elements.auto.disabled, false);
  assert.equal(app.elements.execute.disabled, true);
});

const methods = (calls) => calls.map(([, options]) => options?.method || "GET");
const predicted = () => serverState({ status: "predicted", decision: { choice: "e1", operation: "CLICK" } });

test("a lost decision request while the server is down is not flagged as a possible execution", async () => {
  const app = await load([jsonResponse(serverState()), new TypeError("offline"), new TypeError("offline")]);
  await app.click("choose");
  assert.equal(app.elements.status.textContent, "Cannot reach local demo server");
  assert.doesNotMatch(app.elements.error.textContent, /may already have changed the page/);
});

test("an execute request lost with the server is flagged as a possible execution and not replayed", async () => {
  const app = await load([jsonResponse(predicted()), new TypeError("Failed to fetch"), new TypeError("offline")]);
  await app.click("execute");
  assert.equal(app.elements.status.textContent, "Disconnected · the action may have executed");
  assert.match(app.elements.error.textContent, /Failed to fetch\. .*may already have changed the page/);
  assert.equal(app.elements.retry.hidden, false);
  for (const id of ["start", "choose", "execute", "auto"]) assert.equal(app.elements[id].disabled, true);
  assert.deepEqual(methods(app.calls), ["GET", "POST", "GET"]);
});

test("a malformed reply to an automatic step counts as unconfirmed", async () => {
  const app = await load([jsonResponse(serverState()), malformedResponse(), new TypeError("offline")]);
  await app.click("auto");
  assert.equal(app.elements.status.textContent, "Disconnected · the action may have executed");
  assert.deepEqual(methods(app.calls), ["GET", "POST", "GET"]);
});

test("an explicit server rejection of an execute request defers to the server's record", async () => {
  const app = await load([
    jsonResponse(predicted()),
    jsonResponse({ error: "Page changed; observe again" }, { ok: false, status: 400 }),
    jsonResponse(predicted()),
  ]);
  await app.click("execute");
  assert.equal(app.elements.status.textContent, "Paused · needs attention");
  assert.equal(app.elements.error.textContent, "Page changed; observe again");
});

test("after reconnecting without a server record, the possible execution stays flagged", async () => {
  const app = await load([jsonResponse(predicted()), new TypeError("offline"), new TypeError("offline")]);
  await app.click("execute");
  app.queue.push(new TypeError("offline"));
  await app.click("retry");
  assert.equal(app.elements.status.textContent, "Disconnected · the action may have executed");
  const beforeRetry = app.calls.length;
  app.queue.push(jsonResponse(serverState({ status: "idle" })));
  await app.click("retry");
  assert.deepEqual(methods(app.calls.slice(beforeRetry)), ["GET"]);
  assert.equal(app.elements.status.textContent, "Reconnected · inspect the browser first");
  assert.match(app.elements.error.textContent, /may already have changed the page/);
  assert.equal(app.elements.error.hidden, false);
  assert.equal(app.elements.start.disabled, false);
});

test("after reconnecting, the server's record of the interrupted action replaces the warning", async () => {
  const app = await load([jsonResponse(predicted()), new TypeError("offline"), new TypeError("offline")]);
  await app.click("execute");
  const history = [{ action: "Submit", execution: "unknown", latency_ms: 4, page_changed: null, probability: 1, step: 1 }];
  app.queue.push(jsonResponse(serverState({ history, status: "blocked" })));
  await app.click("retry");
  assert.equal(app.elements.status.textContent, "Stopped · inspect the interrupted action");
  assert.equal(app.elements.error.hidden, true);
  for (const id of ["choose", "execute", "auto"]) assert.equal(app.elements[id].disabled, true);
});
