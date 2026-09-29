"""Controls inside iframes, same-process and cross-site. Local browser only; no model calls.

Frame controls are read in their own frame and mapped into the page. Input must reach the right
element, a covered frame is never clicked through, and a frame that changed or navigated after
observation is refused as stale before any input.
"""

import sys
import time

from check_consent import serve

from jev_ultrafast.browser import Browser, StalePage

FRAME = """<!doctype html><body style="margin:0;font:16px system-ui">
<input id="q" aria-label="Frame search" style="width:200px;height:36px">
<button id="go" onclick="document.body.dataset.clicks=+(document.body.dataset.clicks||0)+1">Frame go</button>
<a id="next" href="/next">Next page</a></body>"""
CLICKS = "+(document.body.dataset.clicks||0)"
NEXT = "<!doctype html><body><p>Another page</p><button>Other</button></body>"
TOP = """<!doctype html><body style="margin:0;font:16px system-ui"><h1>Host page</h1>
<iframe id="f" src="{src}"
  style="position:absolute;left:120px;top:160px;width:480px;height:140px;border:2px solid #333"></iframe>
<div id="cover" style="position:fixed;left:0;top:150px;width:700px;height:200px;background:#fff;display:none"></div>
</body>"""


# Same-process frames are read in an isolated world: it shares the DOM, not the page's globals.
def frame_value(browser, page, expression):
    (frame,) = page["frames"]
    return browser.evaluate_in(frame["id"], expression)


def fresh_page(url):
    browser = Browser(url)
    for _ in range(40):
        page = browser.observe(screenshot=False)
        if any(a.get("frame") for a in page["actions"]):
            return browser, page
        time.sleep(0.05)
    browser.close()
    raise AssertionError("frame controls never offered")


def action(page, label, kind="click"):
    return next(a for a in page["actions"] if a["label"] == label and a["kind"] == kind)


def check_input(url):
    browser, page = fresh_page(url)
    try:
        browser.act(action(page, "Frame search", "fill"), page, text="hello")
        page = browser.observe(screenshot=False)
        assert frame_value(browser, page, "document.getElementById('q').value") == "hello"
        browser.act(action(page, "Frame go"), page)
        page = browser.observe(screenshot=False)
        assert frame_value(browser, page, CLICKS) == 1
    finally:
        browser.close()


def check_covered(url):
    browser, page = fresh_page(url)
    try:
        go = action(page, "Frame go")
        browser.evaluate("document.getElementById('cover').style.display='block'")
        try:
            browser.act(go, page)
        except StalePage:
            pass
        else:
            raise AssertionError("clicked a frame control through a covering element")
        page = browser.observe(screenshot=False)
        assert not any(a.get("frame") for a in page["actions"]), "covered frame controls were offered"
        browser.evaluate("document.getElementById('cover').style.display='none'")
        page = browser.observe(screenshot=False)
        assert frame_value(browser, page, CLICKS) == 0
    finally:
        browser.close()


def check_changed(url, change):
    browser, page = fresh_page(url)
    try:
        go = action(page, "Frame go")
        (frame,) = page["frames"]
        if change == "navigated":
            browser.evaluate_in(frame["id"], "location.href='/next'")
            time.sleep(0.5)
        else:
            browser.evaluate_in(frame["id"], "document.getElementById('go').textContent='Delete account'")
        try:
            browser.act(go, page)
        except StalePage:
            pass
        else:
            raise AssertionError(f"acted on a frame that {change} after observation")
        if change == "relabelled":
            assert frame_value(browser, browser.observe(screenshot=False), CLICKS) == 0
    finally:
        browser.close()


def main():
    _, frame_origin = serve(lambda: {"/frame": FRAME, "/next": NEXT})
    # Another port is another origin in the same process; localhost is another site, its own target.
    shapes = {"same-origin": "/frame", "cross-site": f"{frame_origin.replace('127.0.0.1', 'localhost')}/frame"}
    pages = {"/frame": FRAME, "/next": NEXT, **{f"/top-{name}": TOP.format(src=src) for name, src in shapes.items()}}
    server, origin = serve(lambda: pages)
    checks = [
        ("input reaches frame controls", check_input),
        ("covered frame is never clicked through", check_covered),
        ("relabelled frame control is refused", lambda url: check_changed(url, "relabelled")),
        ("navigated frame is refused", lambda url: check_changed(url, "navigated")),
    ]
    failures = []
    try:
        for name in shapes:
            for label, run in checks:
                try:
                    run(f"{origin}/top-{name}")
                    print(f"PASS: {name}: {label}")
                except Exception as error:
                    failures.append(f"{name}: {label}")
                    print(f"FAIL: {name}: {label}: {type(error).__name__}: {error}")
    finally:
        server.shutdown()
    total = len(shapes) * len(checks)
    print(f"{total - len(failures)}/{total} frame checks passed; no model calls")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
