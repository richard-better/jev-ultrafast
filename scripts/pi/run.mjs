// One goal through Browser Use Pi (ultrafast mode) in a tab another process already opened and
// positioned. Prints one JSON line with the run's own measurements; the caller verifies the outcome.
import { parseArgs } from 'node:util';
import { mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { performance } from 'node:perf_hooks';
import { randomUUID } from 'node:crypto';
import { Browser, BrowserUse, builtinModels } from '@browser_use/pi';

const { values } = parseArgs({
  options: {
    target: { type: 'string' },
    goal: { type: 'string' },
    'start-url': { type: 'string' },
    model: { type: 'string', default: 'opencode-go/glm-5.3-flash' },
    reasoning: { type: 'string', default: 'off' },
    effort: { type: 'string', default: 'low' },
    'max-steps': { type: 'string', default: '40' },
    'timeout-ms': { type: 'string', default: '180000' },
  },
});
if (!values.target || !values.goal) throw new Error('--target and --goal are required');
// Pi reads the OpenCode Go key from its own variable; reuse the key the Jev text helper uses.
process.env.OPENCODE_API_KEY ??= process.env.TEXT_MODEL_API_KEY;

// OpenCode Go rejects requests without one stable session id per run, as the Jev text helper sends.
const models = builtinModels();
const [provider, ...rest] = values.model.split('/');
const catalogModel = models.getModel(provider, rest.join('/'));
if (!catalogModel) throw new Error(`Unknown model ${values.model}`);
// OpenCode Go takes reasoning as this top-level field; Pi's 'off' sends nothing, which leaves GLM's
// default thinking on. 'none' is refused intermittently ("cannot be disabled; use low, high, or max").
if (provider === 'opencode-go') {
  catalogModel.headers = { ...catalogModel.headers, 'x-opencode-session': randomUUID() };
  catalogModel.samplingParams = { reasoning_effort: values.effort };
}

const turns = [];
let turnStart = performance.now();
const agent = await BrowserUse.create({
  model: values.model,
  models,
  reasoning: values.reasoning,
  mode: 'ultrafast',
  browser: Browser.chrome({
    targetId: values.target,
    ...(process.env.BU_CDP_URL ? { cdpUrl: process.env.BU_CDP_URL } : {}),
  }),
  workspace: await mkdtemp(join(tmpdir(), 'jev-pi-')),
  telemetry: false,
  // The Jev arms are handed the start URL separately from the goal; Pi gets the same fact.
  instructions: values['start-url']
    ? `The current tab is already open on ${values['start-url']}. Work in this tab; do not open new tabs.`
    : undefined,
});
try {
  const started = performance.now();
  turnStart = started;
  const result = await agent.run(values.goal, {
    maxSteps: Number(values['max-steps']),
    timeoutMs: Number(values['timeout-ms']),
    onEvent: (event) => {
      if (event.type === 'turn_end') {
        turns.push(Math.round(performance.now() - turnStart));
        turnStart = performance.now();
      }
    },
  });
  console.log(
    JSON.stringify({
      status: result.status,
      text: result.text,
      error: result.error,
      wall_ms: Math.round(performance.now() - started),
      duration_ms: result.durationMs,
      steps: result.steps,
      turn_ms: turns,
      usage: result.usage,
      model: result.model,
      warnings: result.warnings ?? [],
    }),
  );
} finally {
  await agent.close();
}
