"""Observed actions through Browser Harness; one CDP session, no per-step subprocess."""

import functools
import hashlib
import json
import os
import sys
import time
from pathlib import Path

from browser_harness.admin import daemon_browser_ready, ensure_daemon
from browser_harness.helpers import cdp

from . import tracing

# Atomically read visible content and controls, preserving actual DOM node identity.
READ_STATE = Path(__file__).with_name("snapshot.js").read_text(encoding="utf-8")
MARKER = f"(() => {{ const state={READ_STATE}; return state?.marker ?? null; }})()"
CDP_RESPONSE_TIMEOUT = 30  # A deadline, not a delay: fast responses still return immediately.
FRAME_LIMIT = 4
DEFAULT_VIEWPORT = (1470, 1470)


def viewport():
    """The emulated page size: JEV_VIEWPORT as WIDTHxHEIGHT, e.g. 1470x830."""
    raw = os.environ.get("JEV_VIEWPORT", "").strip().lower()
    if not raw:
        return DEFAULT_VIEWPORT
    try:
        width, height = (int(part) for part in raw.split("x"))
    except ValueError:
        raise ValueError(f"JEV_VIEWPORT must be WIDTHxHEIGHT, not {raw!r}") from None
    if not (320 <= width <= 4096 and 320 <= height <= 4096):
        raise ValueError(f"JEV_VIEWPORT {raw!r} is outside 320-4096 pixels per side")
    return width, height

# Resolves once focus has not moved for 50 ms, or after 250 ms at most.
FOCUS_SETTLED = """new Promise(resolve => {
  const start=performance.now(); let last=start;
  const moved=()=>{ last=performance.now(); };
  document.addEventListener('focusin', moved, true);
  const check=()=>{
    const now=performance.now();
    if (now-last<50 && now-start<250) return requestAnimationFrame(check);
    document.removeEventListener('focusin', moved, true);
    resolve();
  };
  requestAnimationFrame(check);
})"""
# Map a point inside a frame to this viewport, only if the frame element itself is hit there.
PLACE = """(([node,x,y]) => {
  const f=window.__jevFast?.nodes.get(node);
  if (!f?.isConnected) return null;
  const r=f.getBoundingClientRect(), X=r.x+f.clientLeft+x, Y=r.y+f.clientTop+y;
  return X>=0 && Y>=0 && X<innerWidth && Y<innerHeight && document.elementFromPoint(X,Y)===f ? {x:X,y:Y} : null;
})"""

class StalePage(ValueError):
    """A decision no longer refers to the observed page."""


def stage(name):
    """Trace a whole browser method as one `browser.stage` span."""

    def decorate(method):
        @functools.wraps(method)
        def traced(*args, **kwargs):
            with tracing.span("browser.stage", stage=name):
                return method(*args, **kwargs)

        return traced

    return decorate


class Browser:
    @stage("start")
    def __init__(self, url):
        if os.environ.get("BH_REQUIRE_EXISTING_DAEMON") == "1":
            if not daemon_browser_ready():
                raise RuntimeError("The required Browser Harness daemon is unavailable")
        else:
            ensure_daemon()
        self.frames = {}
        width, height = viewport()
        self.target = cdp("Target.createTarget", url="about:blank", background=True)["targetId"]
        # The tab exists from here on, and no caller holds this object yet. Whatever fails
        # below -- a refused attach, a malformed URL, Ctrl-C during the readiness wait --
        # this is the only place that can still close it instead of orphaning it in Chrome.
        try:
            self.session = cdp("Target.attachToTarget", targetId=self.target, flatten=True)["sessionId"]
            self.call(
                "Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1, mobile=False
            )
            # Keep rAF/menus rendering in an owned background tab, without activating the user's Chrome tab.
            self.call("Emulation.setFocusEmulationEnabled", enabled=True)
            self.call("Page.navigate", url=url)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                try:
                    if self.evaluate("document.readyState") == "complete":
                        break
                except StalePage:
                    # Page.navigate destroys the about:blank context. Keep polling.
                    pass
                time.sleep(0.02)
        except BaseException as error:
            # Nothing that goes wrong while closing may replace the error the caller needs
            # to see, including a second interrupt landing during the close itself. A tab
            # that could not be closed is still worth knowing about, so it rides along.
            try:
                self.close()
            except BaseException as cleanup:
                error.add_note(f"The browser tab this run opened may still be open: {cleanup!r}")
            raise

    def call(self, method, **params):
        timeout = 5 if method == "Page.captureScreenshot" else CDP_RESPONSE_TIMEOUT
        return cdp(method, session_id=self.session, _response_timeout=timeout, **params)

    def evaluate(self, expression):
        response = self.call("Runtime.evaluate", expression=expression, returnByValue=True)
        if response.get("exceptionDetails"):
            raise StalePage("Document changed during evaluation")
        return response.get("result", {}).get("value")

    def _frame(self, frame_id):
        """A script context in a child frame: its own target when cross-site, else an isolated world."""
        handle = self.frames.get(frame_id)
        if handle is None:
            # Listing targets is also what makes Chrome accept an attach to an out-of-process frame.
            targets = cdp("Target.getTargets")["targetInfos"]
            if any(t["targetId"] == frame_id and t["type"] == "iframe" for t in targets):
                session = cdp("Target.attachToTarget", targetId=frame_id, flatten=True)["sessionId"]
                handle = {"session": session, "context": None}
            else:
                world = self.call("Page.createIsolatedWorld", frameId=frame_id, worldName="jev")
                handle = {"session": self.session, "context": world["executionContextId"]}
            self.frames[frame_id] = handle
        return handle

    def _attach(self, frame_id):
        """A visible cross-site frame gets its own target shortly after its element renders. Wait for
        it once, briefly; a frame that never attaches is skipped without waiting again."""
        unavailable = self.__dict__.setdefault("unavailable_frames", set())
        deadline = time.monotonic() + (0 if frame_id in unavailable else 1.0)
        while True:
            try:
                return self._frame(frame_id)
            except RuntimeError:
                if time.monotonic() >= deadline:
                    unavailable.add(frame_id)
                    raise
                tracing.wait("frame", 0.05)

    def evaluate_in(self, frame_id, expression):
        """Read-only evaluation inside a frame. A navigated frame loses its context: stale, not failed."""
        handle = self.frames.get(frame_id)
        if handle is None:
            raise StalePage("Frame is gone")
        context = {} if handle["context"] is None else {"contextId": handle["context"]}
        try:
            response = cdp(
                "Runtime.evaluate", session_id=handle["session"], _response_timeout=CDP_RESPONSE_TIMEOUT,
                expression=expression, returnByValue=True, **context,
            )
        except RuntimeError as error:
            self.frames.pop(frame_id, None)
            raise StalePage("Frame changed") from error
        if response.get("exceptionDetails"):
            raise StalePage("Frame changed during evaluation")
        return response.get("result", {}).get("value")

    def _frame_id(self, node):
        found = self.call(
            "Runtime.evaluate", expression=f"window.__jevFast?.nodes.get({int(node)}) ?? null", objectGroup="jev-frames"
        )["result"]
        try:
            if found.get("subtype") != "node":
                return None
            return self.call("DOM.describeNode", objectId=found["objectId"])["node"].get("frameId")
        finally:
            self.call("Runtime.releaseObjectGroup", objectGroup="jev-frames")

    def _with_frames(self, info):
        """Merge controls from visible frames, mapped into this viewport and hit-tested through the frame."""
        frames, merged = [], []
        for box in info.pop("frames", [])[:FRAME_LIMIT]:
            try:
                frame_id = self._frame_id(box["node"])
                if not frame_id:
                    continue
                self._attach(frame_id)
                state = self.evaluate_in(frame_id, READ_STATE)
            except (StalePage, RuntimeError):
                continue
            if not state:
                continue
            for action in state["actions"]:
                if action["kind"] in {"scroll", "wait"}:
                    continue
                r = action["rect"]
                merged.append({
                    **action,
                    "frame": frame_id,
                    "frame_node": box["node"],
                    "rect": {**r, "x": r["x"] + box["x"], "y": r["y"] + box["y"]},
                    "local": [r["x"] + r["w"] / 2, r["y"] + r["h"] / 2],
                })
            frames.append({
                "id": frame_id, "node": box["node"], "marker": state["marker"],
                "page_key": state["page_key"], "guards": state["guards"], "text": state["text"],
            })
        if merged:
            points = [[a["frame_node"], *a["local"]] for a in merged]
            hits = self.evaluate(f"(points => points.map(p => !!({PLACE})(p)))({json.dumps(points)})")
            merged = [a for a, hit in zip(merged, hits or []) if hit]
        for action in merged:
            del action["local"]
        controls = [a for a in info["actions"] if a["kind"] in {"scroll", "wait"}]
        elements = [a for a in info["actions"] if a["kind"] not in {"scroll", "wait"}] + merged
        retained = elements[: max(0, 250 - len(controls))]
        info["omitted_actions"] = info.get("omitted_actions", 0) + len(elements) - len(retained)
        for index, action in enumerate(retained, 1):
            action["id"] = f"e{index}"
        info["actions"] = retained + controls
        info["frames"] = frames
        if frames:
            info["text"] = "\n".join([info["text"], *(f["text"][:3000] for f in frames)])
        for gone in set(self.frames) - {f["id"] for f in frames}:
            self.frames.pop(gone)
        info["fingerprint"] = fingerprint(info)
        return info

    @stage("observe")
    def observe(self, screenshot=True):
        if getattr(self, "after_input", None):
            action, self.after_input = self.after_input, None
            # This is read-only and happens after execution was logged, even if navigation interrupts it.
            # A frame action settles in its own frame; its field and navigation live there.
            handle = getattr(self, "frames", {}).get(action.get("frame"))
            run = self.call
            if handle is not None:
                context = {} if handle["context"] is None else {"contextId": handle["context"]}
                run = functools.partial(
                    cdp, session_id=handle["session"], _response_timeout=CDP_RESPONSE_TIMEOUT, **context
                )
            try:
                with tracing.span("browser.stage", stage="settle"):
                    run(
                        "Runtime.evaluate",
                        expression="""(action => new Promise(resolve => {
                          const field=window.__jevFast?.nodes.get(action.node);
                          const autocomplete=action.kind==='fill' && field?.getAttribute('role')==='combobox';
                          const navigation=action.kind==='click' && action.navigation &&
                            ['_self','_top','_parent'].includes(action.navigation.target.toLowerCase()) &&
                            action.navigation.url!==action.source_url;
                          let frames=0, stopped=false;
                          const finish=()=>{stopped=true;resolve()};
                          setTimeout(finish,navigation ? 1500 : autocomplete ? 200 : 50);
                          const ready=()=>{
                            if (stopped) return;
                            let suggestionsReady=true;
                            if (autocomplete) {
                              const ids=(field?.getAttribute('aria-controls')||field?.getAttribute('aria-owns')||'')
                                .split(/\\s+/).filter(Boolean);
                              const roots=ids.length ?
                                ids.map(id=>document.getElementById(id)).filter(Boolean) : [document];
                              const options=roots.flatMap(root=>[...root.querySelectorAll('[role="option"]')]);
                              suggestionsReady=options.some(e=>{
                                const r=e.getBoundingClientRect();
                                return r.width && r.height && r.bottom>0 && r.top<innerHeight &&
                                  e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
                              });
                            }
                            const navigated=location.href!==action.source_url ||
                              performance.timeOrigin!==action.source_document;
                            if (++frames>=2 && (!navigation || navigated) && suggestionsReady) finish();
                            else requestAnimationFrame(ready);
                          };
                          requestAnimationFrame(ready);
                        }))(""" + json.dumps(action) + ")",
                        awaitPromise=True,
                        returnByValue=True,
                    )
            except RuntimeError:
                pass
        for attempt in range(10):
            try:
                info = browser_operation({"operation": "observe", "session": self.session, "screenshot": screenshot})
            except StalePage:
                if attempt == 9:
                    raise
                # Navigation can outlive the fast action loop. Retry observations only, never the mutation.
                tracing.wait("navigation", min(0.05 * (attempt + 1), 0.25))
                continue
            if not info.get("frames"):
                info["frames"] = []
                return info
            with tracing.span("browser.stage", stage="frames"):
                return self._with_frames(info)
        raise StalePage("Page did not settle")

    @stage("fresh")
    def fresh(self, page, action=None):
        if action is not None and action["kind"] == "scroll" and type(action.get("node")) is int:
            current = self.evaluate(
                """(action => {
                  const c=window.__jevFast, e=c?.nodes.get(action.node);
                  if (!e?.isConnected || !e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}) ||
                      e.closest('[aria-hidden="true"],[inert]') ||
                      !/(auto|scroll)/.test(getComputedStyle(e).overflowY)) return null;
                  return [c.pageKey(),[action.node,e.scrollTop,e.scrollHeight,e.clientHeight]];
                })(""" + json.dumps(action) + ")"
            )
            expected = [
                page["page_key"],
                [action["node"], action["scroll_top"], action["scroll_height"], action["client_height"]],
            ]
            return current == expected
        if action is not None and action["kind"] in {"click", "select"}:
            node = action["node"]
            if type(node) is not int:
                return False
            guard = (
                "(() => { const c=window.__jevFast; "
                f"return c ? [c.pageKey(),c.guard(c.nodes.get({node}))] : null; }})()"
            )
            if action.get("frame"):
                frame = next((f for f in page.get("frames", []) if f["id"] == action["frame"]), None)
                if frame is None:
                    return False
                try:
                    current = self.evaluate_in(action["frame"], guard)
                except StalePage:
                    return False
                top = self.evaluate("(() => window.__jevFast?.pageKey() ?? null)()")
                return current == [frame["page_key"], frame["guards"].get(str(node))] and top == page["page_key"]
            current = self.evaluate(guard)
            return current == [page["page_key"], page["guards"].get(str(node))]
        if self.evaluate(MARKER) != page["marker"]:
            return False
        for frame in page.get("frames", []):
            try:
                if self.evaluate_in(frame["id"], MARKER) != frame["marker"]:
                    return False
            except StalePage:
                return False
        return True

    @stage("act")
    def act(self, action, page, text=None):
        if not self.fresh(page, action):
            raise StalePage("Page changed since this decision. Observe again.")
        if action["kind"] == "wait":
            tracing.wait("wait_action", 0.1)
        request = {"operation": "act", "session": self.session, "action": action, "text": text}
        if action.get("frame"):
            handle = getattr(self, "frames", {}).get(action["frame"])
            if handle is None:
                raise StalePage("Frame changed since this decision. Observe again.")
            request["frame"] = {**handle, "node": action["frame_node"]}
        result = browser_operation(request)
        self.after_input = action if action["kind"] != "wait" else None
        if self.after_input and action.get("navigation"):
            self.after_input = {**action, "source_url": page["url"], "source_document": page["marker"][0]}
        return result

    @stage("close")
    def close(self):
        for handle in getattr(self, "frames", {}).values():
            if handle["context"] is None:
                try:
                    cdp("Target.detachFromTarget", sessionId=handle["session"])
                except RuntimeError:
                    pass
        self.frames = {}
        if self.target:
            cdp("Target.closeTarget", targetId=self.target)
            self.target = None


def fingerprint(state):
    # Geometry is re-read immediately before input. Animation must not look like a new page,
    # so neither element rects nor the document height count; the scroll position does.
    actions = [{k: v for k, v in action.items() if k != "rect"} for action in state["actions"]]
    content = {"url": state["url"], "text": state["text"], "actions": actions, "scroll": state["scroll"]["y"]}
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def browser_operation(request):
    operation = request["operation"]
    session = request["session"]

    def call(method, **params):
        timeout = 5 if method == "Page.captureScreenshot" else CDP_RESPONSE_TIMEOUT
        return cdp(method, session_id=session, _response_timeout=timeout, **params)

    frame = request.get("frame")

    def top_evaluate(expression):
        result = call("Runtime.evaluate", expression=expression, returnByValue=True)
        if result.get("exceptionDetails"):
            raise StalePage("Document changed during evaluation")
        return result.get("result", {}).get("value")

    def evaluate(expression, wait=False):
        # Element-side work runs in the action's own frame; input always goes through the page.
        promise = {"awaitPromise": True} if wait else {}
        if frame:
            context = {} if frame["context"] is None else {"contextId": frame["context"]}
            result = cdp(
                "Runtime.evaluate", session_id=frame["session"], _response_timeout=CDP_RESPONSE_TIMEOUT,
                expression=expression, returnByValue=True, **context, **promise,
            )
        else:
            result = call("Runtime.evaluate", expression=expression, returnByValue=True, **promise)
        if result.get("exceptionDetails"):
            if operation == "act" and request["action"]["kind"] == "select":
                raise RuntimeError("Dropdown execution was interrupted; inspect before retrying.")
            raise StalePage("Document changed during evaluation")
        return result.get("result", {}).get("value")

    if operation == "act":
        action = request["action"]
        kind = action["kind"]
        if kind == "scroll":
            if type(action.get("node")) is int:
                target = evaluate("""(action => {
                  const e=window.__jevFast?.nodes.get(action.node);
                  if (!e?.isConnected || !e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}) ||
                      e.closest('[aria-hidden="true"],[inert]') ||
                      !/(auto|scroll)/.test(getComputedStyle(e).overflowY) ||
                      e.scrollTop!==action.scroll_top || e.scrollHeight!==action.scroll_height ||
                      e.clientHeight!==action.client_height) return null;
                  const r=e.getBoundingClientRect();
                  if (r.width<=0 || r.height<=0) return null;
                  const nearestScrollable=hit=>{
                    for (let node=hit; node && node!==document.body && node!==document.documentElement;
                         node=node.parentElement) {
                      const style=getComputedStyle(node);
                      if (/(auto|scroll)/.test(style.overflowY) &&
                          node.scrollHeight>node.clientHeight+2) return node;
                    }
                    return null;
                  };
                  // Look inside the region's own visible box, so any viewport size finds a point on it.
                  const left=Math.max(0,r.left), top=Math.max(0,r.top);
                  const width=Math.min(innerWidth,r.right)-left, height=Math.min(innerHeight,r.bottom)-top;
                  if (width<=0 || height<=0) return null;
                  for (const fx of [0.5,0.1,0.3,0.7,0.9]) for (const fy of [0.5,0.2,0.8]) {
                    const x=left+width*fx, y=top+height*fy;
                    const hit=document.elementFromPoint(x,y);
                    if (hit && !hit.closest('input,select,textarea') && nearestScrollable(hit)===e)
                      return {x,y};
                  }
                  return null;
                })(""" + json.dumps(action) + ")")
                if target is None:
                    raise StalePage("Scrollable region changed or is covered. Observe again.")
                x, y = target["x"], target["y"]
            else:
                viewport = evaluate("({width:innerWidth,height:innerHeight})")
                if not viewport or viewport["width"] <= 0 or viewport["height"] <= 0:
                    raise StalePage("No scrollable viewport")
                x, y = viewport["width"] / 2, viewport["height"] / 2
            call("Input.dispatchMouseEvent", type="mouseWheel", x=x, y=y, deltaX=0, deltaY=action["delta"])
        elif kind != "wait":
            if type(action["node"]) is not int:
                raise ValueError("Invalid observed node")
            if kind == "select" and type(action.get("option_node")) is not int:
                raise ValueError("Invalid observed option node")
            # Code-owned node IDs refer to actual observed elements, never model-generated selectors.
            with tracing.span("browser.stage", stage="target"):
                target = evaluate("""(action => {
                  const cache=window.__jevFast, e=cache?.nodes.get(action.node);
                  if (!e?.isConnected || e.matches(':disabled') || e.closest('[aria-disabled="true"],[inert]') ||
                      !e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true})) return null;
                  if (action.kind==='fill' && !cache.editable(e)) return null;
                  const r=e.getBoundingClientRect();
                  let x=r.x+r.width/2, y=r.y+r.height/2;
                  if (!r.width || !r.height || x<0 || y<0 || x>=innerWidth || y>=innerHeight) return null;
                  if (!cache.within(e,cache.hit(x,y))) {
                    // A wrapped link's box centre sits on the text between its fragments; click the
                    // centre of a visible fragment instead, the same fallback the snapshot uses.
                    const f=[...e.getClientRects()].map(q=>[q.x+q.width/2,q.y+q.height/2,q.width,q.height])
                      .find(([fx,fy,w,h])=>w>0 && h>0 && fx>=0 && fy>=0 && fx<innerWidth && fy<innerHeight &&
                        cache.within(e,cache.hit(fx,fy)));
                    if (!f) return null;
                    x=f[0]; y=f[1];
                  }
                  if (action.kind==='select') {
                    const o=window.__jevFast.nodes.get(action.option_node);
                    if (e.tagName!=='SELECT' || o?.tagName!=='OPTION' || !o.isConnected || o.closest('select')!==e ||
                        o.value!==action.value || o.selected || o.disabled ||
                        o.closest('optgroup[disabled]')) return null;
                    // Values need not be unique. Select the exact option that was observed.
                    if (e.multiple) o.selected=true;
                    else e.selectedIndex=o.index;
                    e.dispatchEvent(new Event('input',{bubbles:true}));
                    e.dispatchEvent(new Event('change',{bubbles:true}));
                  }
                  return {x,y};
                })(""" + json.dumps(action) + ")")
            if target is None:
                if kind == "select":
                    raise RuntimeError("Dropdown execution was not confirmed; inspect before retrying.")
                raise StalePage("Target changed or is covered. Observe again.")
            local_x, local_y = target["x"], target["y"]
            if frame and kind != "select":
                # The frame may have moved or been covered since observation. Resolve it last.
                target = top_evaluate(f"({PLACE})({json.dumps([frame['node'], local_x, local_y])})")
                if target is None:
                    raise StalePage("Frame moved or is covered. Observe again.")
            with tracing.span("browser.stage", stage="input"):
                if kind != "select":
                    x, y = target["x"], target["y"]
                    for event in ("mousePressed", "mouseReleased"):
                        call("Input.dispatchMouseEvent", type=event, x=x, y=y, button="left", clickCount=1)
                    if kind == "fill":
                        def text_target(claim):
                            # Claiming picks the element that will receive keys; later checks require it unchanged.
                            try:
                                claimed = bool(evaluate("""(([node,x,y,claim]) => {
                                  const cache=window.__jevFast, e=cache?.nodes.get(node), a=cache?.active();
                                  const usable=f=>!!(f?.isConnected && !f.matches(':disabled') &&
                                    !f.closest('[aria-disabled="true"],[inert]') &&
                                    f.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}) &&
                                    cache.editable(f));
                                  if (!claim) return !!cache?.typing && a===cache.typing && usable(a);
                                  cache.typing=null;
                                  if (a===e) return usable(e) && !!(cache.typing=e);
                                  // The click opened an editor in the field's place, e.g. a combobox's search
                                  // popup. It holds focus and sits under the clicked point: the same field.
                                  const hit=cache.hit(x,y);
                                  return !!(hit && cache.within(a,hit) && usable(a) && (cache.typing=a));
                                })(""" + json.dumps([action["node"], local_x, local_y, claim]) + ")"))
                                if claimed and frame:
                                    # Keys go to the page's focused frame, so the page must focus this one.
                                    claimed = top_evaluate(
                                        f"window.__jevFast?.nodes.get({frame['node']})===document.activeElement"
                                    )
                                return bool(claimed)
                            except StalePage:
                                return False

                        # A click can move focus a few frames later, e.g. when a field opens its search
                        # popup. Let focus settle before choosing where keys go, so none land mid-move.
                        try:
                            evaluate(FOCUS_SETTLED, wait=True)
                        except StalePage:
                            pass
                        if not text_target(claim=True):
                            # Focus moved somewhere other than the clicked field. No key was sent, so the
                            # outcome is known: a click. The next observation shows where focus went.
                            return {"executed": action["id"], "typed": False}
                        call(
                            "Input.dispatchKeyEvent",
                            type="keyDown",
                            key="a",
                            code="KeyA",
                            modifiers=4 if sys.platform == "darwin" else 2,
                            commands=["selectAll"],
                        )
                        call(
                            "Input.dispatchKeyEvent",
                            type="keyUp",
                            key="a",
                            code="KeyA",
                            modifiers=4 if sys.platform == "darwin" else 2,
                        )
                        # Page key handlers can redirect focus during Select All as well. Select All already
                        # reached whatever held focus, so this outcome is unknown. Never retry it.
                        if not text_target(claim=False):
                            raise RuntimeError("Text target changed or lost focus; inspect before retrying.")
                        call("Input.insertText", text=request["text"])
        return {"executed": action["id"]}

    with tracing.span("browser.stage", stage="snapshot"):
        info = evaluate(READ_STATE)
    if info is None:
        raise StalePage("Document is navigating")
    info["fingerprint"] = fingerprint(info)
    if request.get("screenshot", True):
        try:
            with tracing.span("browser.stage", stage="screenshot"):
                info["screenshot"] = call("Page.captureScreenshot", format="jpeg", quality=72)["data"]
        except TimeoutError:
            # Screenshots do not drive the agent. A throttled background tab must
            # not discard an otherwise complete structured observation.
            info["screenshot"] = None
    return info
