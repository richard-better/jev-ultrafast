const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function inspector() {
  const nodes = new Map();
  const document = {
    querySelector: () => ({ content: 'offline-token' }),
    getElementById: id => {
      if (!nodes.has(id)) {
        const children = new Map();
        nodes.set(id, {
          style: {},
          addEventListener() {},
          querySelector: selector => children.get(selector) ?? children.set(selector, {}).get(selector),
        });
      }
      return nodes.get(id);
    },
  };
  const stopped = {
    status: 'blocked', decision: null, elements: [], elapsed_ms: 123,
    page: { actions: [], url: 'about:blank', title: 'Offline', w: 1120, h: 780 },
    history: [{ step: 1, action: 'Submit', execution: 'unknown', executed_ms: null,
      page_changed: null, latency_ms: 10, probability: 1 }],
  };
  return { nodes, document, stopped };
}

// Load the inspector the way the page does: its first GET /api/state answers with `initial`.
async function load({ nodes, document, stopped }, initial) {
  const context = vm.createContext({
    document,
    fetch: async () => ({ ok: true, json: async () => structuredClone(initial) }),
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../jev_ultrafast/static/app.js'), 'utf8'), context);
  await new Promise(resolve => setImmediate(resolve));
  return { nodes, context, stopped };
}

test('an interrupted action remains stopped after the inspector refreshes its state', async () => {
  const fixture = inspector();
  const live = { ...fixture.stopped, status: 'ready', history: [] };
  const { nodes, context, stopped } = await load(fixture, live);
  assert.equal(nodes.get('choose').disabled, false);
  context.fetch = async url => ({
    ok: url === '/api/state',
    json: async () => url === '/api/state' ? stopped : { error: 'Lost reply' },
  });
  await vm.runInContext('perform(() => call("tick"), "Executing")', context);
  for (const id of ['choose', 'execute', 'auto']) assert.equal(nodes.get(id).disabled, true);
  assert.equal(nodes.get('start').disabled, false);
  assert.match(nodes.get('status').textContent, /Stopped.*interrupted/);
  assert.match(nodes.get('history').innerHTML, /Execution unconfirmed/);
  assert.doesNotMatch(nodes.get('history').innerHTML, /No change observed/);
  assert.equal(nodes.get('empty').querySelector('h2').textContent, 'Live preview unavailable.');
  assert.equal(nodes.get('viewport').style.aspectRatio, '1120/780');
});

test('a failed reset reports its own error without attributing the old interruption to it', async () => {
  const fixture = inspector();
  const { nodes, context, stopped } = await load(fixture, fixture.stopped);
  const requests = [];
  context.fetch = async url => {
    requests.push(url);
    return {
      ok: url === '/api/state',
      json: async () => url === '/api/state' ? structuredClone(stopped) : { error: 'Enter 1–2,000 characters' },
    };
  };
  await vm.runInContext('perform(() => call("reset", { goal: "" }), "Opening a fresh browser")', context);
  assert.deepEqual(requests, ['/api/reset', '/api/state']);
  assert.equal(nodes.get('error').textContent, 'Enter 1–2,000 characters');
  assert.equal(nodes.get('status').textContent, 'Paused · needs attention');
  for (const id of ['choose', 'execute', 'auto']) assert.equal(nodes.get(id).disabled, true);
  assert.equal(nodes.get('start').disabled, false);
  assert.match(nodes.get('history').innerHTML, /Execution unconfirmed/);
});
