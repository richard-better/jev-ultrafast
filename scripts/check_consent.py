"""Consent banners and embedded frames, served from two local origins.

Default: real-browser checks without model calls. For every banner shape, the agent must be
offered the banner's "Reject all", clicking it must dismiss the banner, and the page underneath
must then be usable. Shapes that do not work yet are listed in KNOWN_GAPS; a gap that starts
passing fails the run too, so the list stays honest.

--live: also runs the real agent (paid TypeSafe and text-model calls) on every shape with a small
task behind the banner. It passes when the task is done without accepting tracking: rejecting a
blocking banner, or ignoring one that blocks nothing.
"""

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from jev_ultrafast import Agent
from jev_ultrafast.browser import Browser
from jev_ultrafast.demo import load_environment

# Bottom bars leave the page usable, so its controls stay offered beside them.
NON_BLOCKING = {"inline", "shadow-open", "shadow-closed"}
KNOWN_GAPS = {
    "shadow-closed": "closed shadow roots are unreachable from page scripts; needs the accessibility tree",
    "shadow-closed-overlay": "closed shadow roots are unreachable from page scripts; needs the accessibility tree",
}

SHOP = """
<main id="shop"><h1>Lamp shop</h1>
<form id="search" onsubmit="event.preventDefault(); window.searched=document.getElementById('q').value;
  document.getElementById('result').textContent='Results for '+window.searched;">
<input id="q" type="search" aria-label="Search products"><button type="submit">Search</button></form>
<p id="result">No search yet</p></main>
"""
STYLE = """<style>body{margin:0;font:16px/1.5 system-ui}main{padding:40px}
.banner{position:fixed;left:0;right:0;bottom:0;background:#222;color:#fff;padding:20px;z-index:10}
.overlay{position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:10;display:flex;align-items:center;
  justify-content:center}.dialog{background:#fff;padding:24px;width:420px}
iframe.consent{position:fixed;inset:0;width:100%;height:100%;border:0;z-index:10}</style>"""
BUTTONS = """<p>We use cookies to improve your experience.</p>
<button onclick="decide('rejected')">Reject all</button> <button onclick="decide('accepted')">Accept all</button>"""
DECIDE = """<script>function decide(value){window.consent=value;
  document.querySelectorAll('.banner,.overlay,#consent-host').forEach(e=>e.remove());}</script>"""
FRAME = """<!doctype html><style>body{margin:0;background:rgba(0,0,0,.6);display:flex;align-items:center;
justify-content:center;height:100vh;font:16px system-ui}.dialog{background:#fff;padding:24px;width:420px}</style>
<div class="dialog"><p>We use cookies to improve your experience.</p>
<button onclick="parent.postMessage('consent:rejected','*')">Reject all</button>
<button onclick="parent.postMessage('consent:accepted','*')">Accept all</button></div>"""
LISTEN = """<script>addEventListener('message',e=>{if(typeof e.data==='string'&&e.data.startsWith('consent:')){
  window.consent=e.data.slice(8);document.querySelector('iframe.consent')?.remove();}});</script>"""


def shadow(mode, blocking=False):
    place = "inset:0;background:rgba(0,0,0,.6)" if blocking else "left:0;right:0;bottom:0"
    return f"""<div id="consent-host" style="position:fixed;{place};z-index:10"></div>
<script>const root=document.getElementById('consent-host').attachShadow({{mode:'{mode}'}});
root.innerHTML=`<style>div{{background:#222;color:#fff;padding:20px}}</style><div>{BUTTONS}</div>`;
root.querySelectorAll('button').forEach(b=>b.onclick=()=>decide(b.textContent.startsWith('Reject')?'rejected':'accepted'));
</script>"""


def pages(frame_origin):
    below_fold = (
        '<main style="padding:40px"><h1>Before you continue</h1>'
        + "".join(f"<p>Section {i}: how we use cookies and data.</p>" for i in range(1, 25))
        + BUTTONS.replace("decide(", "decideAndShow(")
        + "</main><script>function decideAndShow(v){window.consent=v;document.body.innerHTML="
        + json.dumps(SHOP)
        + ";}</script>"
    )
    return {
        "inline": SHOP + f'<div class="banner">{BUTTONS}</div>',
        "overlay": SHOP + f'<div class="overlay"><div class="dialog">{BUTTONS}</div></div>',
        "below-fold": below_fold,
        "iframe-same-origin": SHOP + '<iframe class="consent" src="/frame"></iframe>' + LISTEN,
        # Another port is another origin in the same process; localhost is another site, so Chrome runs
        # that frame as its own target, like a third-party consent manager.
        "iframe-cross-origin": SHOP + f'<iframe class="consent" src="{frame_origin}/frame"></iframe>' + LISTEN,
        "iframe-cross-site": SHOP
        + f'<iframe class="consent" src="{frame_origin.replace("127.0.0.1", "localhost")}/frame"></iframe>'
        + LISTEN,
        "shadow-open": SHOP + shadow("open"),
        "shadow-closed": SHOP + shadow("closed"),
        "shadow-open-overlay": SHOP + shadow("open", blocking=True),
        "shadow-closed-overlay": SHOP + shadow("closed", blocking=True),
    }


def serve(routes):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = routes().get(self.path.split("?")[0])
            self.send_response(200 if body else 404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write((body or "Not found").encode())

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


def page_state(browser):
    return browser.evaluate("({consent: window.consent ?? null, searched: window.searched ?? null})")


def labels(page):
    return {a["label"]: a for a in page["actions"]}


def check(browser, name):
    """Reject the banner through the real observe/act path, then prove the page underneath works."""
    page = browser.observe(screenshot=False)
    offered = labels(page)
    if name == "below-fold":
        if "Reject all" in offered:
            return False, "consent buttons below the fold were offered before scrolling"
        if "Scroll down" not in offered:
            return False, "no Scroll down to reach the consent buttons"
        for _ in range(6):
            browser.act(labels(page)["Scroll down"], page)
            page = browser.observe(screenshot=False)
            if "Reject all" in labels(page):
                break
        offered = labels(page)
    if "Reject all" not in offered:
        return False, f"Reject all not offered; offered: {sorted(offered)[:8]}"
    if "Search" in offered and name not in NON_BLOCKING:
        return False, "controls under a blocking banner were offered"
    browser.act(offered["Reject all"], page)
    time.sleep(0.2)
    page = browser.observe(screenshot=False)
    state, offered = page_state(browser), labels(page)
    if state["consent"] != "rejected":
        return False, f"consent is {state['consent']!r} after clicking Reject all"
    if "Reject all" in offered or "Search" not in offered:
        return False, f"banner still in the way; offered: {sorted(offered)[:8]}"
    return True, "offered, rejected, page usable"


def live(url, name):
    goal = 'Search the lamp shop for "desk lamp".'
    with Agent(url, goal, max_steps=12) as agent:
        error = None
        try:
            for _ in agent.run():
                pass
        except Exception as caught:
            error = type(caught).__name__
        state = page_state(agent.browser)
        history = [f"{h['kind']} {h['action']}" + (f" = {h['text']!r}" if h["text"] else "") for h in
                   agent.state["history"]]
        return {
            "done": state["searched"] == "desk lamp",
            "consent": state["consent"],
            "status": agent.state["status"],
            "error": error,
            "steps": history,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--live", action="store_true", help="also run the real agent (paid model calls)")
    parser.add_argument("--only", nargs="*", help="limit to these shapes")
    args = parser.parse_args()
    frame_server, frame_origin = serve(lambda: {"/frame": FRAME})
    content = pages(frame_origin)
    main_server, origin = serve(lambda: {"/frame": FRAME, **{f"/{k}": STYLE + DECIDE + v for k, v in content.items()}})
    names = args.only or list(content)
    failures = []
    try:
        for name in names:
            browser = Browser(f"{origin}/{name}")
            try:
                ok, detail = check(browser, name)
            except Exception as error:
                ok, detail = False, f"{type(error).__name__}: {error}"
            finally:
                browser.close()
            gap = KNOWN_GAPS.get(name)
            if ok and gap:
                failures.append(name)
                print(f"FAIL: {name}: now passes; remove it from KNOWN_GAPS")
            elif ok:
                print(f"PASS: {name}: {detail}")
            elif gap:
                print(f"GAP:  {name}: {detail} ({gap})")
            else:
                failures.append(name)
                print(f"FAIL: {name}: {detail}")
        if args.live:
            load_environment()
            print("\nLive agent (paid model calls):")
            for name in names:
                result = live(f"{origin}/{name}", name)
                # Ignoring a banner that blocks nothing is fine; agreeing to tracking never is.
                verdict = "PASS" if result["done"] and result["consent"] != "accepted" else "FAIL"
                error = f", {result['error']}" if result["error"] else ""
                print(f"{verdict}: {name}: task {'done' if result['done'] else 'not done'}, consent "
                      f"{result['consent']}, status {result['status']}{error}")
                print("       " + " -> ".join(result["steps"]) if result["steps"] else "       no actions")
    finally:
        main_server.shutdown()
        frame_server.shutdown()
    gaps = sum(1 for n in names if n in KNOWN_GAPS and n not in failures)
    print(f"\n{len(names) - len(failures) - gaps} passed, {gaps} known gaps, {len(failures)} failed; no model calls"
          if not args.live else "")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
