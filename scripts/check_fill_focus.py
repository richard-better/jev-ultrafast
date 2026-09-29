"""Local-browser text targeting regressions. No model calls or external websites."""

import json

from jev_ultrafast.browser import Browser

INPUT = '<input id="target" aria-label="Target" value="original">'
EDITOR = '<div id="target" aria-label="Target" contenteditable="true"><span>original</span></div>'


def popup(top):
    """A focused editor the click opens, like a combobox's search popup, at the given top edge."""
    return (
        "const p=document.createElement('input'), r=target.getBoundingClientRect();"
        "p.id='popup'; p.setAttribute('aria-label','Where else?');"
        "p.style.cssText='position:absolute;z-index:9;width:300px;height:60px';"
        f"p.style.left=(r.x+scrollX)+'px'; p.style.top=({top}+scrollY)+'px';"
        "document.body.append(p); p.focus();"
    )


def check(browser, field, setup, expected):
    html = (
        '<style>input,textarea,[contenteditable]{display:block;width:240px;height:60px;margin:20px}</style>'
        + field + '<input id="other" value="keep this">'
    )
    browser.evaluate("document.body.innerHTML=" + json.dumps(html))
    browser.evaluate("""(() => {
      window.clicks=0; window.inputs=0;
      const target=document.getElementById('target'), other=document.getElementById('other');
      target.addEventListener('click',()=>window.clicks++);
      document.body.oninput=()=>window.inputs++;
    """ + setup + "})()")
    page = browser.observe(screenshot=False)
    action = next(a for a in page["actions"] if a["kind"] == "fill" and a["label"] == "Target")
    error, result = None, None
    try:
        result = browser.act(action, page, text="replacement")
    except RuntimeError as caught:
        error = caught
    actual = browser.evaluate("""(() => {
      const target=document.getElementById('target'), popup=document.getElementById('popup');
      return {value:target?.value ?? target?.textContent, other:document.getElementById('other').value,
        popup:popup?.value, clicks:window.clicks, inputs:window.inputs};
    })()""")
    assert actual["clicks"] == 1, actual
    assert actual["other"] == "keep this", actual
    if expected == "popup":
        # The click opened an editor in the field's place; it receives the text instead.
        assert error is None and result == {"executed": action["id"]}, (error, result)
        assert actual["popup"] == "replacement", actual
        assert actual["value"] == "original", actual
        assert actual["inputs"] == 1, actual
        return
    if expected == "typed":
        assert error is None and result == {"executed": action["id"]}, (error, result)
        assert actual["value"] == "replacement", actual
        assert actual["inputs"] == 1, actual
        return
    # Nothing may be typed anywhere once focus leaves the observed field.
    assert actual["inputs"] == 0, actual
    assert actual.get("value") in (None, "original"), actual
    if expected == "click":
        # Focus moved before any key: a known click, reported without an error.
        assert error is None and result == {"executed": action["id"], "typed": False}, (error, result)
    else:
        # Select All already reached another element: unknown, never retried.
        assert error is not None, ("Expected a non-retryable error", actual)


def check_shadow(browser):
    """Controls in an open shadow root are offered, typed into and clicked like any other."""
    browser.evaluate("""document.body.innerHTML='<div id="host"></div>';
      document.getElementById('host').attachShadow({mode:'open'}).innerHTML=
        '<label for="q">Shadow search</label><input id="q" style="display:block;width:200px;height:30px">'+
        '<button onclick="this.dataset.clicks=+(this.dataset.clicks||0)+1">Shadow go</button>';""")
    page = browser.observe(screenshot=False)
    field = next(a for a in page["actions"] if a["label"] == "Shadow search" and a["kind"] == "fill")
    assert browser.act(field, page, text="replacement") == {"executed": field["id"]}
    page = browser.observe(screenshot=False)
    browser.act(next(a for a in page["actions"] if a["label"] == "Shadow go"), page)
    actual = browser.evaluate("""(root => [root.getElementById('q').value, root.querySelector('button').dataset.clicks])
      (document.getElementById('host').shadowRoot)""")
    assert actual == ["replacement", "1"], actual


def main():
    cases = [
        ("input replacement", INPUT, "", "typed"),
        ("textarea replacement", '<textarea id="target" aria-label="Target">original</textarea>', "", "typed"),
        ("contenteditable replacement", EDITOR, "", "typed"),
        ("click redirects focus", INPUT, "target.onclick=()=>other.focus();", "click"),
        ("click opens an editor in place", INPUT, "target.onclick=()=>{" + popup("r.y") + "};", "popup"),
        ("click opens an editor elsewhere", INPUT, "target.onclick=()=>{" + popup("r.bottom+200") + "};", "click"),
        ("select-all redirects focus", INPUT, "target.onkeydown=()=>other.focus();", "stopped"),
        ("click makes target readonly", INPUT, "target.onclick=()=>{target.readOnly=true};", "click"),
        ("click disables target", INPUT, "target.onclick=()=>{target.disabled=true};", "click"),
        ("click removes target", INPUT, "target.onclick=()=>target.remove();", "click"),
        ("click replaces target", INPUT, "target.onclick=()=>{target.outerHTML=target.outerHTML};", "click"),
        ("click changes target to password", INPUT, "target.onclick=()=>{target.type='password'};", "click"),
        ("click stops editing", EDITOR, "target.onclick=()=>{target.contentEditable='false'};", "click"),
    ]
    browser = Browser("about:blank")
    failures = []
    passed = 0
    click_only_fields = (
        '<div role="textbox" tabindex="0" aria-label="Target">original</div>',
        '<input type="checkbox" role="textbox" aria-label="Target">',
        '<input type="text" role="button" aria-label="Target">',
        '<div contenteditable="true" role="button" aria-label="Target">original</div>',
        '<div contenteditable="true" role="checkbox" aria-label="Target">original</div>',
        '<div contenteditable="true" role="option" aria-label="Target">original</div>',
    )
    try:
        for field in click_only_fields:
            label = f"click-only control: {field}"
            try:
                browser.evaluate("document.body.innerHTML=" + json.dumps(field))
                page = browser.observe(screenshot=False)
                kinds = {a["kind"] for a in page["actions"] if a["label"] == "Target"}
                assert kinds == {"click"}, kinds
                passed += 1
                print("PASS:", label)
            except (AssertionError, ValueError, RuntimeError) as error:
                failures.append(label)
                print("FAIL:", label, str(error))
        for label, field, setup, expected in cases:
            try:
                check(browser, field, setup, expected)
                passed += 1
                print("PASS:", label)
            except (AssertionError, ValueError, RuntimeError) as error:
                failures.append(label)
                print("FAIL:", label, str(error))
        try:
            check_shadow(browser)
            passed += 1
            print("PASS: open shadow root field and button")
        except (AssertionError, ValueError, RuntimeError, StopIteration) as error:
            failures.append("open shadow root")
            print("FAIL: open shadow root", str(error))
        total = len(cases) + len(click_only_fields) + 1
        print(f"{passed}/{total} text targeting checks passed; no model calls")
    finally:
        try:
            browser.close()
        except Exception as error:
            failures.append("browser cleanup")
            print("FAIL: browser cleanup", str(error))
    assert not failures, failures


if __name__ == "__main__":
    main()
