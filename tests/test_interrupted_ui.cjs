const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

test('an interrupted action remains stopped after the inspector refreshes its state', async () => {
  const nodes = new Map();
  const document = {
    querySelector: () => ({ content: 'offline-token' }),
    getElementById: id => {
      if (!nodes.has(id)) nodes.set(id, { addEventListener() {} });
      return nodes.get(id);
    },
  };
  const stopped = {
    status: 'blocked', decision: null, elements: [], elapsed_ms: 123,
    page: { actions: [], url: 'about:blank', title: 'Offline', w: 1120, h: 780 },
    history: [{ step: 1, action: 'Submit', execution: 'unknown', executed_ms: null,
      page_changed: null, latency_ms: 10, probability: 1 }],
  };
  const context = vm.createContext({ document, fetch: () => new Promise(() => {}) });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../jev_ultrafast/static/app.js'), 'utf8'), context);
  context.fetch = async () => ({ json: async () => stopped });
  await vm.runInContext('perform(async () => { throw Error("Lost reply"); }, "Executing")', context);
  for (const id of ['choose', 'execute', 'auto']) assert.equal(nodes.get(id).disabled, true);
  assert.equal(nodes.get('start').disabled, false);
  assert.match(nodes.get('status').textContent, /Stopped.*interrupted/);
  assert.match(nodes.get('history').innerHTML, /Execution unconfirmed/);
  assert.doesNotMatch(nodes.get('history').innerHTML, /No change observed/);
});
