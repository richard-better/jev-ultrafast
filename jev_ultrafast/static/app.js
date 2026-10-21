const $ = (id) => document.getElementById(id);
const token = document.querySelector('meta[name="demo-token"]').content;
let state = null,
  busy = false,
  automatic = false,
  connected = false;
const goals = {
  flights: '__FLIGHTS_GOAL__',
  travel: 'Find a Design stay in Lisbon with Free cancellation and open Casa Flora.',
  research:
    "Open the article about using finite choices to control browser agents.",
};
const escape = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const percent = (value) => `${(value * 100).toFixed(value < 0.01 ? 1 : 0)}%`;
const isServerState = (value) =>
  value !== null &&
  typeof value === "object" &&
  !Array.isArray(value) &&
  typeof value.status === "string" &&
  Array.isArray(value.history) &&
  Object.hasOwn(value, "page") &&
  Object.hasOwn(value, "decision");
// Browser mutations: if one of these gets no valid reply, it may still have run.
const mutations = new Set(["act", "tick"]);
const unconfirmedNotice =
  "The server stopped answering before confirming the browser action. It may already have changed the page; inspect the browser before continuing. Nothing was retried.";
// History length when a browser mutation lost its reply while the server was
// unreachable, or null. Only the server's own record of the action settles it.
let unconfirmedFrom = null;
async function responseState(response) {
  let data;
  try {
    data = await response.json();
  } catch {
    throw Error("Invalid response from local demo server");
  }
  if (!response.ok) {
    throw Object.assign(Error(data?.error || `Request failed (${response.status})`), { answered: true });
  }
  if (!isServerState(data)) throw Error("Invalid response from local demo server");
  return data;
}
async function readState() {
  return responseState(await fetch("/api/state"));
}
async function call(name, body = {}) {
  let data;
  try {
    const response = await fetch(`/api/${name}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Demo-Token": token },
      body: JSON.stringify(body),
    });
    data = await responseState(response);
  } catch (error) {
    if (mutations.has(name) && !error.answered) error.unconfirmed = true;
    throw error;
  }
  state = data;
  connected = true;
  render();
  return data;
}
function controls() {
  const live = connected && state?.page && !["done", "blocked"].includes(state.status);
  $("start").disabled = busy || !connected;
  $("scenario").disabled = busy;
  $("goal").disabled = busy;
  $("choose").disabled = busy || !live;
  $("execute").disabled = busy || !state?.decision || !live;
  $("auto").disabled = busy || !live;
  $("auto").hidden = automatic;
  $("stop").hidden = !automatic;
  $("download").disabled = !(state?.history?.length || state?.decisions?.length);
  $("retry").disabled = busy;
  $("retry").hidden = connected;
}
function showError(message) {
  $("error").textContent = message;
  $("error").hidden = false;
}
// Read the server's state once; never replay the request that failed. Returns
// the read error, or null once validated state is live.
async function refresh() {
  let fresh;
  try {
    fresh = await readState();
  } catch (error) {
    connected = false;
    return error;
  }
  state = fresh;
  connected = true;
  render();
  return null;
}
async function perform(fn, label) {
  if (busy) return;
  const historyLength = state?.history?.length ?? 0;
  busy = true;
  unconfirmedFrom = null;
  $("error").hidden = true;
  controls();
  $("status").textContent = label;
  try {
    await fn();
  } catch (error) {
    automatic = false;
    await refresh();
    if (!connected && error.unconfirmed) {
      unconfirmedFrom = state?.history?.length ?? 0;
      showError(`${error.message}. ${unconfirmedNotice}`);
      $("status").textContent = "Disconnected · the action may have executed";
    } else {
      showError(error.message);
      const interrupted = (state?.history?.length ?? 0) > historyLength &&
        state.history.at(-1).execution === "unknown";
      $("status").textContent = !connected
        ? "Cannot reach local demo server"
        : interrupted
          ? "Stopped · inspect the interrupted action"
          : "Paused · needs attention";
    }
  } finally {
    busy = false;
    controls();
  }
}
async function retryConnection() {
  if (busy) return;
  busy = true;
  $("error").hidden = true;
  $("status").textContent = "Connecting to local demo server…";
  controls();
  try {
    const error = await refresh();
    if (error) {
      showError(unconfirmedFrom === null ? error.message : `${error.message}. ${unconfirmedNotice}`);
      $("status").textContent = unconfirmedFrom === null
        ? "Cannot reach local demo server"
        : "Disconnected · the action may have executed";
    } else if (unconfirmedFrom !== null) {
      // A longer history is the server's own record of the action. Without one,
      // the server may have restarted and lost track of the request.
      if (state.history.length <= unconfirmedFrom) {
        showError(unconfirmedNotice);
        $("status").textContent = "Reconnected · inspect the browser first";
      }
      unconfirmedFrom = null;
    }
  } finally {
    busy = false;
    controls();
  }
}
function render() {
  if (!state) return;
  $("helper").textContent = `Text helper · ${state.text_model}`;
  $("plan").innerHTML = (state.plan || [])
    .map(
      (goal, i) =>
        `<div class="plan-step ${i === state.plan_index ? "current" : ""}"><span>${i < state.plan_index ? "✓" : i + 1}</span>${escape(goal)}</div>`,
    )
    .join("");
  const page = state.page,
    d =
      state.decision ||
      (state.status === "done" ? state.decisions?.at(-1) : null);
  const labels = {
    idle: "Ready to explore",
    ready: "Page observed · ready for a decision",
    predicted: "Choice ready · inspect or execute",
    done: "Jev reports complete · inspect the page",
    blocked: state.history?.at(-1)?.execution === "unknown"
      ? "Stopped · inspect the interrupted action"
      : "Stopped · no supported next action",
  };
  $("status").textContent = labels[state.status] || state.status;
  if (!page) {
    controls();
    return;
  }
  // Overlay targets are placed in percent of the page, so the frame must keep the page's shape.
  if (page.w && page.h) $("viewport").style.aspectRatio = `${page.w}/${page.h}`;
  const hasScreenshot = Boolean(page.screenshot);
  $("empty").hidden = hasScreenshot;
  $("screenshot").hidden = !hasScreenshot;
  if (hasScreenshot) {
    $("screenshot").src = `data:image/jpeg;base64,${page.screenshot}`;
  } else {
    $("empty").querySelector("h2").textContent = "Live preview unavailable.";
    $("empty").querySelector("p:last-child").textContent =
      "Screenshot capture timed out. The indexed-element loop is still running.";
  }
  $("url").textContent = page.url;
  $("page-title").textContent = page.title;
  $("action-count").textContent = `${state.elements.length} element${state.elements.length === 1 ? "" : "s"}`;
  const chosen = page.actions.find((a) => a.id === d?.choice);
  $("choice-title").textContent = d
    ? chosen?.label || d.choice
    : "Choose an action";
  $("latency").textContent = d ? `${d.latency_ms} ms` : "—";
  $("confidence").textContent = d?.target_confidence != null ? percent(d.target_confidence) : "—";
  $("completion").textContent = d ? d.operation : "—";
  $("ranking-note").textContent = d ? "Ranked by Jev" : "Unranked";
  const op = Object.entries(d?.operation_probabilities || {}).sort((a,b)=>b[1]-a[1]);
  $("operation-choices").innerHTML = op.map(([name,p]) =>
    `<span class="operation-choice ${name === d.operation ? 'best' : ''}">${escape(name)} <b>${percent(p)}</b></span>`).join('');
  const probability = e => d?.target_probabilities[e.index] ??
    Math.max(-1, ...(e.options || []).map(o=>d?.target_probabilities[o.index] ?? -1));
  const selectedIndex = d?.target?.split(':')[0];
  const elements = [...state.elements];
  if (d) elements.sort((a,b)=>probability(b)-probability(a));
  $("choices").innerHTML = elements.map(e => {
    const p = probability(e);
    return `<div class="choice ${selectedIndex === e.index ? 'best' : ''}" data-action="${escape(e.index)}"><span class="choice-id">[${escape(e.index)}]</span><div class="choice-label">${escape(e.label)}<small>${escape(e.role)} · ${escape(e.operations.join(' / '))}${e.value ? ' · '+escape(e.value) : ''}${e.checked !== undefined ? ' · checked '+escape(e.checked) : ''}</small>${p >= 0 ? `<div class="bar" style="--probability:${p*100}%"></div>` : ''}</div><span class="probability">${p >= 0 ? percent(p) : '—'}</span></div>`;
  }).join('');
  const targets = new Map();
  for (const a of page.actions) if (a.rect && !targets.has(a.node)) targets.set(a.node, a);
  $("targets").innerHTML = [...targets.values()].map((a,i) => {
    const index=String(i+1);
    return `<div class="target ${index === selectedIndex ? 'selected' : ''}" data-action="${index}" style="left:${100*a.rect.x/page.w}%;top:${100*a.rect.y/page.h}%;width:${100*a.rect.w/page.w}%;height:${100*a.rect.h/page.h}%"><span>${index}</span></div>`;
  }).join('');
  $("targets").hidden = !hasScreenshot || !$("overlays").checked;
  $("history").innerHTML = state.history.length
    ? state.history
        .map(
          (h) =>
            `<div class="trace-row"><span class="number">${String(h.step).padStart(2, "0")}</span><div>${escape(h.action)}${h.text ? ` <b>“${escape(h.text)}”</b><small>${escape(h.text_helper)}</small>` : ""}</div><span class="time">${h.latency_ms} ms · ${percent(h.probability)}</span><span class="effect">${h.execution === "unknown" ? "Execution unconfirmed · inspect before restarting" : h.page_changed ? "Page changed" : "No change observed"}</span></div>`,
        )
        .join("")
    : '<p class="muted">Each executed action leaves an observed result.</p>';
  $("step-count").textContent = `${state.history.length} action${state.history.length === 1 ? "" : "s"} · ${(state.elapsed_ms / 1000).toFixed(2)} s`;
  $("model-state").textContent = JSON.stringify(
    d?.request || {
      goal: state.goal,
      url: page.url,
      text: page.text,
      actions: page.actions.map(({ rect, node, ...rest }) => rest),
    },
    null,
    2,
  );
  controls();
}
$("task-form").addEventListener("submit", (event) => {
  event.preventDefault();
  automatic = false;
  perform(
    () =>
      call("reset", { scenario: $("scenario").value, goal: $("goal").value }),
    "Opening a fresh browser…",
  );
});
$("scenario").addEventListener("change", () => {
  $("goal").value = goals[$("scenario").value];
});
$("choose").addEventListener("click", () =>
  perform(() => call("predict"), "Jev is comparing the actions…"),
);
$("execute").addEventListener("click", () =>
  perform(
    () => call("act", { fingerprint: state.page.fingerprint }),
    "Executing the choice…",
  ),
);
$("auto").addEventListener("click", () =>
  perform(async () => {
    automatic = true;
    controls();
    for (let i = 0; i < state.max_steps * 2 && automatic; i++) {
      $("status").textContent = "Running…";
      if ($("pace").checked) {
        await call("predict");
        await new Promise(resolve => setTimeout(resolve, 450));
        if (!automatic) break;
        await call("act", {fingerprint: state.page.fingerprint});
      } else {
        await call("tick");
      }
      if (["done", "blocked"].includes(state.status)) break;
    }
    automatic = false;
  }, "Running the browser…"),
);
$("stop").addEventListener("click", () => {
  automatic = false;
  $("status").textContent = "Pausing after the current request…";
  controls();
});
$("retry").addEventListener("click", retryConnection);
$("overlays").addEventListener("change", () => {
  $("targets").hidden = !state?.page?.screenshot || !$("overlays").checked;
});
$("choices").addEventListener("pointerover", (event) => {
  const id = event.target.closest("[data-action]")?.dataset.action;
  document
    .querySelectorAll(".target")
    .forEach((t) =>
      t.classList.toggle(
        "selected",
        t.dataset.action === id || t.dataset.action === state?.decision?.target?.split(':')[0],
      ),
    );
});
$("choices").addEventListener("pointerleave", () =>
  document
    .querySelectorAll(".target")
    .forEach((t) =>
      t.classList.toggle(
        "selected",
        t.dataset.action === state?.decision?.target?.split(':')[0],
      ),
    ),
);
$("download").addEventListener("click", () => {
  const { page, ...rest } = state;
  const blob = new Blob(
    [
      JSON.stringify(
        { ...rest, page: { ...page, screenshot: undefined } },
        null,
        2,
      ),
    ],
    { type: "application/json" },
  );
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "typesafe-browser-trace.json";
  a.click();
  URL.revokeObjectURL(url);
});
retryConnection();
