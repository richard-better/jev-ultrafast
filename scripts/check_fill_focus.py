"""Local-browser text targeting regressions. No model calls or external websites."""

import json

from jev_ultrafast.browser import Browser

INPUT = '<input id="target" aria-label="Target" value="original">'
EDITOR = '<div id="target" aria-label="Target" contenteditable="true"><span>original</span></div>'


def check(browser, field, setup, rejected):
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
    error = None
    try:
        browser.act(action, page, text="replacement")
    except RuntimeError as caught:
        error = caught
    actual = browser.evaluate("""(() => {
      const target=document.getElementById('target');
      return {value:target?.value ?? target?.textContent, other:document.getElementById('other').value,
        clicks:window.clicks, inputs:window.inputs};
    })()""")
    assert actual["clicks"] == 1, actual
    assert actual["other"] == "keep this", actual
    if rejected:
        assert error is not None, ("Expected a non-retryable error", actual)
        assert actual["inputs"] == 0, actual
        assert actual.get("value") in (None, "original"), actual
    else:
        assert error is None, error
        assert actual["value"] == "replacement", actual
        assert actual["inputs"] == 1, actual


def main():
    cases = [
        ("input replacement", INPUT, "", False),
        ("textarea replacement", '<textarea id="target" aria-label="Target">original</textarea>', "", False),
        ("contenteditable replacement", EDITOR, "", False),
        ("click redirects focus", INPUT, "target.onclick=()=>other.focus();", True),
        ("select-all redirects focus", INPUT, "target.onkeydown=()=>other.focus();", True),
        ("click makes target readonly", INPUT, "target.onclick=()=>{target.readOnly=true};", True),
        ("click disables target", INPUT, "target.onclick=()=>{target.disabled=true};", True),
        ("click removes target", INPUT, "target.onclick=()=>target.remove();", True),
        ("click replaces target", INPUT, "target.onclick=()=>{target.outerHTML=target.outerHTML};", True),
        ("click changes target to password", INPUT, "target.onclick=()=>{target.type='password'};", True),
        ("click stops editing", EDITOR, "target.onclick=()=>{target.contentEditable='false'};", True),
    ]
    browser = Browser("about:blank")
    failures = []
    try:
        for label, field, setup, rejected in cases:
            try:
                check(browser, field, setup, rejected)
                print("PASS:", label)
            except (AssertionError, ValueError, RuntimeError) as error:
                failures.append(label)
                print("FAIL:", label, str(error))
    finally:
        browser.close()
    assert not failures, failures
    print(f"PASS: {len(cases)} text targeting checks; no model calls")


if __name__ == "__main__":
    main()
