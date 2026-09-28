"""Local-browser freshness/execution regressions. No live model calls or external websites."""

import time


import json
from unittest.mock import patch
from urllib.parse import quote

from jev_ultrafast import Agent, model
from jev_ultrafast.browser import Browser, StalePage, browser_operation
from jev_ultrafast.model import action_space

HTML = """<!doctype html><title>Guard checks</title>
<style>
body{margin:30px}button{width:180px;height:50px}#outside{position:absolute;top:3000px}
#feed{position:absolute;left:500px;top:140px;width:320px;height:180px;overflow-y:auto}
#feed div{height:800px}
#wheel-control{position:sticky;top:0;width:100px;height:40px}
#outer-feed{position:absolute;left:40px;top:520px;width:1000px;height:200px;overflow-y:auto}
#inner-feed{height:200px;overflow-y:auto}
#inner-feed div,#outer-tail{height:600px}
</style>
<p id="context">Cart total: $10</p>
<button id="target" onclick="window.clicks=(window.clicks||0)+1">Continue</button>
<label>City<input id="field" value="Zurich"></label>
<label><input id="toggle" type="checkbox">Refundable</label>
<select aria-label="Category"><option>All</option><option>Design</option></select>
<section id="feed" aria-label="Search results">
  <input id="wheel-control" type="number" value="5"><div>First result<br>More results below</div>
</section>
<section id="outer-feed" aria-label="Outer results">
  <section id="inner-feed" aria-label="Inner results"><div>Nested results</div></section>
  <div id="outer-tail">Outer tail</div>
</section>
<p id="outside">Unrelated offscreen text</p>"""


def main():
    browser = Browser("data:text/html," + quote(HTML))
    passed = []
    try:
        page = browser.observe(screenshot=False)
        action = next(a for a in page["actions"] if a["label"] == "Continue")
        browser.evaluate("document.querySelector('#target').style.transform='translateX(200px)'")
        assert browser.fresh(page, action), "Movement should preserve the target-specific guard"
        browser.act(action, page)
        assert browser.evaluate("window.clicks") == 1
        passed.append("moving target clicked at its current location")
        page = browser.observe(screenshot=False)

        browser.evaluate("document.querySelector('#outside').textContent='Updated outside the viewport'")
        assert browser.fresh(page)
        passed.append("unrelated offscreen text does not invalidate")

        page = browser.observe(screenshot=False)
        scroll = next(a for a in page["actions"] if a["label"] == "Scroll down Search results")
        browser.evaluate(
            "(()=>{const e=document.querySelector('#wheel-control'); e.focus(); "
            "e.addEventListener('wheel',()=>window.controlWheels=(window.controlWheels||0)+1)})()"
        )
        page_y = browser.evaluate("scrollY")
        browser.act(scroll, page)
        browser.observe(screenshot=False)
        assert browser.evaluate("document.querySelector('#feed').scrollTop") > 0
        assert browser.evaluate("scrollY") == page_y
        assert browser.evaluate("window.controlWheels||0") == 0
        assert browser.evaluate("document.querySelector('#wheel-control').value") == "5"
        assert not browser.fresh(page, scroll)
        passed.append("nested scroll avoids form controls and moves only its observed region")

        for attribute, value in (("inert", ""), ("aria-hidden", "true")):
            browser.evaluate("document.querySelector('#feed').scrollTop=0")
            page = browser.observe(screenshot=False)
            scroll = next(a for a in page["actions"] if a["label"] == "Scroll down Search results")
            browser.evaluate(
                f"document.querySelector('#feed').setAttribute({attribute!r},{value!r})"
            )
            assert not browser.fresh(page, scroll)
            try:
                browser_operation({
                    "operation": "act", "session": browser.session, "action": scroll,
                })
            except StalePage:
                pass
            else:
                raise AssertionError(f"{attribute} scroll region received input")
            browser.evaluate(f"document.querySelector('#feed').removeAttribute({attribute!r})")
            passed.append(attribute + " scroll region rejected before input")

        browser.evaluate(
            "(()=>{const e=document.querySelector('#feed'); "
            "e.scrollTop=e.scrollHeight-e.clientHeight-13})()"
        )
        page = browser.observe(screenshot=False)
        scroll = next(a for a in page["actions"] if a["label"] == "Scroll down Search results")
        assert 0 < scroll["delta"] <= 13
        page_y = browser.evaluate("scrollY")
        browser.act(scroll, page)
        browser.observe(screenshot=False)
        assert browser.evaluate("scrollY") == page_y
        passed.append("nested scroll delta is clamped at the region boundary")

        page = browser.observe(screenshot=False)
        labels = {a["label"] for a in page["actions"]}
        assert "Scroll down Inner results" in labels
        assert "Scroll down Outer results" not in labels
        inner = next(a for a in page["actions"] if a["label"] == "Scroll down Inner results")
        outer_y = browser.evaluate("document.querySelector('#outer-feed').scrollTop")
        browser.act(inner, page)
        browser.observe(screenshot=False)
        assert browser.evaluate("document.querySelector('#inner-feed').scrollTop") > 0
        assert browser.evaluate("document.querySelector('#outer-feed').scrollTop") == outer_y
        passed.append("nearest nested scroll region receives the wheel event")

        mutations = {
            "visible context": "document.querySelector('#context').textContent='Cart total: $100'",
            "accessible label": "document.querySelector('#target').setAttribute('aria-label','Delete account')",
            "field property": "document.querySelector('#field').value='London'",
            "checkbox property": "document.querySelector('#toggle').checked=true",
            "disabled target": "document.querySelector('#target').disabled=true",
            "read-only field": "document.querySelector('#field').readOnly=true",
            "hidden target": "document.querySelector('#target').style.display='none'",
            "replaced node": "document.querySelector('#target').outerHTML=document.querySelector('#target').outerHTML",
            "dropdown option": "document.querySelector('select').options[1].text='Coastal'",
        }
        for label, expression in mutations.items():
            browser.evaluate("document.querySelector('#target').style.display='block'; "
                             "document.querySelector('#target').disabled=false")
            page = browser.observe(screenshot=False)
            browser.evaluate(expression)
            assert not browser.fresh(page), label
            passed.append(label + " invalidates")

        browser.evaluate("document.querySelector('#target').disabled=false; "
                         "document.querySelector('#target').style.display='block'")
        page = browser.observe(screenshot=False)
        action = next(a for a in page["actions"] if a["label"] == "Delete account")
        # A textless overlay removes covered targets from the action table and must block a click.
        browser.evaluate("const cover=document.createElement('div'); "
                         "cover.style.cssText='position:fixed;inset:0;z-index:9999;background:white'; "
                         "document.body.append(cover)")
        assert not browser.fresh(page)
        try:
            browser.act(action, page)
        except (RuntimeError, StalePage):
            pass
        else:
            raise AssertionError("Covered target was clicked")
        assert browser.evaluate("window.clicks") == 1
        passed.append("overlay blocked before input")

        browser.evaluate("document.body.innerHTML=" + repr("""
          <form><p id="price">Total $10</p>
          <button type="button" id="buy">Buy</button>
          <button type="button" id="nonstop" aria-pressed="false">Nonstop</button>
          <label>Search <input id="query" role="combobox" aria-controls="suggestions"></label>
          <div role="listbox" id="suggestions"></div>
          <label><input id="check" type="checkbox">Enabled</label>
          <label><input id="radio" type="radio">Choice</label>
          <input id="readonly" aria-label="Read only" readonly>
          <input id="secret" type="password" value="never expose this">
          <button id="off" disabled>Disabled</button>
          <select id="category" aria-label="Category">
            <option>All</option><option>Design</option><option disabled>Unavailable</option>
          </select></form><aside id="unrelated">News</aside>
        """))
        page = browser.observe(screenshot=False)
        buy = next(a for a in page["actions"] if a["label"] == "Buy")
        browser.evaluate("document.querySelector('#unrelated').textContent='New unrelated news'")
        assert browser.fresh(page, buy)
        assert not browser.fresh(page)
        passed.append("click guard accepts unrelated visible updates; terminal guard rejects them")
        for label, expression in {
            "nearby price": "document.querySelector('#price').textContent='Total $100'",
            "form value": "document.querySelector('#query').value='changed'",
            "form toggle": "document.querySelector('#check').checked=true",
            "target replacement": "document.querySelector('#buy').outerHTML=document.querySelector('#buy').outerHTML",
        }.items():
            page = browser.observe(screenshot=False)
            buy = next(a for a in page["actions"] if a["label"] == "Buy")
            browser.evaluate(expression)
            assert not browser.fresh(page, buy), label
            passed.append(label + " invalidates action-specific guard")

        page = browser.observe(screenshot=False)
        toggle = next(a for a in page["actions"] if a["label"] == "Nonstop")
        assert toggle["pressed"] == "false", toggle
        browser.evaluate("document.querySelector('#nonstop').setAttribute('aria-pressed','true')")
        assert not browser.fresh(page, toggle)
        assert browser.observe(screenshot=False)["fingerprint"] != page["fingerprint"]
        passed.append("toggle button pressed state is observed and guarded")

        page = browser.observe(screenshot=False)
        actions = page["actions"]
        for role in ("checkbox", "radio"):
            assert {a["kind"] for a in actions if a.get("role") == role} == {"click"}
        assert {a["kind"] for a in actions if a["label"] == "Read only"} == {"click"}
        assert not any(a["label"] == "Disabled" or a.get("value") == "never expose this" for a in actions)
        assert [a["value"] for a in actions if a["kind"] == "select"] == ["Design"]
        passed.append("native controls expose only supported operations and safe values")

        select = next(a for a in actions if a["kind"] == "select")
        browser.act(select, page)
        assert browser.evaluate("document.querySelector('#category').value") == "Design"
        passed.append("native dropdown selects an observed option")

        browser.evaluate("document.querySelector('#query').addEventListener('input',()=>setTimeout(()=>{"
                         "document.querySelector('#suggestions').innerHTML='<div role=option>Generated</div>'"
                         "},60))")
        page = browser.observe(screenshot=False)
        field = next(a for a in page["actions"] if a["kind"] == "fill")
        browser.act(field, page, text="Generated")
        page = browser.observe(screenshot=False)
        value = browser.evaluate("document.querySelector('#query').value")
        assert value == "Generated", repr(value)
        assert any(a.get("role") == "option" for a in page["actions"])
        passed.append("real text input waits for asynchronous combobox suggestions")
        assert browser.fresh(page, field)
        browser.call("Emulation.setDeviceMetricsOverride", width=360, height=400,
                     deviceScaleFactor=1, mobile=False)
        browser.evaluate("document.body.innerHTML=" + repr("""
          <button>Background</button>
          <div style="position:fixed;inset:0;background:white;z-index:99999">
            <a onclick="this.parentElement.remove()">Dismiss popup</a>
          </div>
          <span onclick="window.customClicked=true">Custom control</span>
          <div tabindex="0">Focusable control</div>
        """))
        page = browser.observe(screenshot=False)
        assert not any(a["label"] == "Background" for a in page["actions"])
        dismiss = next(a for a in page["actions"] if a["label"] == "Dismiss popup")
        browser.act(dismiss, page)
        page = browser.observe(screenshot=False)
        assert any(a["label"] == "Background" for a in page["actions"])
        passed.append("occluded controls are excluded until the popup is dismissed")
        custom = next(a for a in page["actions"] if a["label"] == "Custom control")
        browser.act(custom, page)
        assert browser.evaluate("window.customClicked") is True
        assert any(a["label"] == "Focusable control" for a in page["actions"])
        passed.append("scripted anchors and focusable controls expose observed click targets")
        browser.evaluate("document.body.style.height='2500px';window.scrollTo(0,0)")
        page = browser.observe(screenshot=False)
        scroll = next(a for a in page["actions"] if a["id"] == "scroll_down")
        browser.act(scroll, page)
        browser.observe(screenshot=False)
        for _ in range(20):
            if browser.evaluate("scrollY") > 0:
                break
            time.sleep(0.05)
        assert browser.evaluate("scrollY") > 0, browser.evaluate(
            "({width:innerWidth,height:innerHeight,scrollHeight:document.documentElement.scrollHeight})")
        passed.append("scrolling works below the former fixed 650-pixel input coordinate")


        browser.evaluate("document.body.innerHTML=" + repr("""
          <form id="publication">
            <button name="published" value="0">Save draft</button>
            <button type="submit" name="published" value="1"><span>Publish now</span></button>
            <button type="button" value="preview">Preview publication</button>
            <button type="reset" value="reset">Reset publication</button>
          </form>
          <output id="saved">Nothing saved</output>
          <input type="submit" value="Submit search">
          <input type="button" value="Open search">
          <input type="reset" value="Reset search">
          <button type="button">Cancel</button>
          <button type="button" value="">Back</button>
          <label for="save-copy">Save a copy</label>
          <button id="save-copy" type="button" value="copy">Save</button>
          <button value="archive" aria-label="Archive publication">Archive</button>
          <span id="delete-label">Delete publication</span>
          <button value="delete" aria-labelledby="delete-label">Delete</button>
        """))
        browser.evaluate("document.querySelector('#publication').addEventListener('submit',event=>{"
                         "event.preventDefault();"
                         "window.submittedValue=new FormData(event.target,event.submitter).get('published');"
                         "document.querySelector('#saved').textContent=window.submittedValue==='0'"
                         "?'Draft saved':'Published';})")
        page = browser.observe(screenshot=False)
        assert {a["label"] for a in page["actions"] if a.get("role") == "button"} == {
            "Save draft", "Publish now", "Preview publication", "Reset publication",
            "Submit search", "Open search", "Reset search", "Cancel", "Back", "Save a copy",
            "Archive publication", "Delete publication",
        }, "Button labels must use their text while input button labels keep their values"
        passed.append("button names preserve visible text, input values, and explicit ARIA labels")
        for label, submitted, outcome in [("Save draft", "0", "Draft saved"), ("Publish now", "1", "Published")]:
            page = browser.observe(screenshot=False)
            elements, targets, _ = action_space(page["actions"])
            element = next(e for e in elements if e["label"] == label)
            action = targets["CLICK"][element["index"]]
            browser.act(action, page)
            assert browser.evaluate("window.submittedValue") == submitted
            assert browser.evaluate("document.querySelector('#saved').textContent") == outcome
        passed.append("named submit buttons retain their original form values after clicking by label")

        page = browser.observe(screenshot=False)
        action = next(a for a in page["actions"] if a["label"] == "Publish now")
        assert browser.fresh(page)
        assert browser.fresh(page, action)

        browser.evaluate("""document.body.innerHTML=
          '<section id="dense-feed" aria-label="Dense results" style="position:fixed;left:500px;top:140px;'
          +'width:320px;height:180px;overflow-y:auto"><div style="height:800px">Results</div></section>'
          +Array.from({length:260},(_,i)=>'<button style="position:fixed;left:0;top:0">Button '
          +i+'</button>').join('')""")
        page = browser.observe(screenshot=False)
        labels = {a["label"] for a in page["actions"]}
        retained_buttons = sum(a["kind"] == "click" for a in page["actions"])
        assert len(page["actions"]) == 250, (
            len(page["actions"]), retained_buttons, page["omitted_actions"], labels,
        )
        assert retained_buttons + page["omitted_actions"] == 260
        assert "Scroll down Dense results" in labels
        assert "Wait for the page to update" in labels
        passed.append("250-action cap retains bounded scroll and wait controls")

        assert browser.fresh(page)
        browser.call("Page.navigate", url="about:blank")
        new_page = browser.observe(screenshot=False)
        assert new_page["page_key"] != page["page_key"]
        assert not browser.fresh(page)
        assert not browser.fresh(page, action)
        passed.append("navigation invalidates the old document")

        browser.evaluate("document.body.innerHTML=" + repr("""
          <form>
            <label><input id="preference" type="checkbox">Announcements</label>
            <button id="inherit" type="button">Use workspace default</button>
          </form>
          <label><input id="partial" type="checkbox" checked>Partial selection</label>
          <label><input id="choice" type="radio" checked>Radio choice</label>
          <div role="checkbox" aria-checked="mixed">ARIA partial selection</div>
        """))
        browser.evaluate("""(() => {
          const preference=document.querySelector('#preference');
          window.preferenceValue=false; window.preferenceInputs=0;
          preference.addEventListener('change',()=>{
            window.preferenceValue=preference.checked; window.preferenceInputs++;
          });
          document.querySelector('#inherit').addEventListener('click',()=>{
            window.preferenceValue=null; preference.checked=false; preference.indeterminate=true;
          });
          document.querySelector('#partial').indeterminate=true;
          document.querySelector('#choice').indeterminate=true;
        })()""")
        before = browser.observe(screenshot=False)
        preference = next(a for a in before["actions"] if a["label"] == "Announcements")
        assert preference["checked"] == "false"
        inherit = next(a for a in before["actions"] if a["label"] == "Use workspace default")
        browser.act(inherit, before)
        page = browser.observe(screenshot=False)
        elements, _, _ = action_space(page["actions"])
        checked = {e["label"]: e["checked"] for e in elements if "checked" in e}
        assert checked == {
            "Announcements": "mixed", "Partial selection": "mixed",
            "Radio choice": "true", "ARIA partial selection": "mixed",
        }, checked
        assert page["fingerprint"] != before["fingerprint"]
        assert browser.evaluate("window.preferenceValue") is None
        passed.append("native mixed checkbox state reaches the element table without changing radio or ARIA state")

        assert not browser.fresh(before)
        assert not browser.fresh(before, preference)
        assert not browser.fresh(before, inherit)
        try:
            browser.act(preference, before)
        except StalePage:
            pass
        else:
            raise AssertionError("A click based on the old checkbox state was executed")
        assert browser.evaluate("window.preferenceInputs") == 0
        assert browser.evaluate("window.preferenceValue") is None
        preference = next(a for a in page["actions"] if a["label"] == "Announcements")
        browser.act(preference, page)
        assert browser.evaluate("window.preferenceInputs") == 1
        assert browser.evaluate("window.preferenceValue") is True
        assert browser.evaluate("document.querySelector('#preference').indeterminate") is False

        page = browser.observe(screenshot=False)
        partial = next(a for a in page["actions"] if a["label"] == "Partial selection")
        browser.evaluate("document.querySelector('#partial').checked=false")
        assert not browser.fresh(page)
        assert not browser.fresh(page, partial)
        passed.append("mixed-state changes reject stale clicks and preserve underlying checked-state guards")

        for attribute in ('contenteditable="true"', 'contenteditable', 'contenteditable=""',
                          'contenteditable="plaintext-only"', 'contenteditable="TRUE"',
                          'contenteditable="PLAINTEXT-ONLY"'):
            browser.evaluate("document.body.innerHTML=" + repr(f"""
              <div id="editor" {attribute} aria-label="Draft"
                   style="width:400px;min-height:60px;border:1px solid">Old draft</div>
              <button onclick="document.querySelector('output').textContent=
                  document.querySelector('#editor').innerText">Save draft</button><output></output>
              <div contenteditable="false" aria-label="Locked">Locked text</div>
              <div contenteditable="invalid" aria-label="Invalid">Plain text</div>
              <div aria-label="Static">Static text</div>
              <div contenteditable="" hidden aria-label="Hidden">Hidden text</div>
              <div contenteditable="plaintext-only" inert aria-label="Inert">Inert text</div>
              <div contenteditable="true" aria-readonly="true" aria-label="Read only">Read only text</div>
              <div id="other-editor" contenteditable="true" aria-label="Other draft">Keep this
                <span contenteditable="inherit" aria-label="Inherited">paragraph</span></div>
            """))
            page = browser.observe(screenshot=False)
            draft = [a for a in page["actions"] if a["label"] in {"Draft", "Open Draft"}]
            assert {a["kind"] for a in draft} == {"fill", "click"}, attribute
            assert all(a["role"] == "textbox" and a["value"] == "Old draft" for a in draft)
            assert not any(a["label"] in {"Locked", "Invalid", "Static", "Hidden", "Inert", "Inherited"}
                           for a in page["actions"])
            assert {a["kind"] for a in page["actions"] if a["label"] == "Read only"} == {"click"}
            field = next(a for a in draft if a["kind"] == "fill")
            browser.act(field, page, text="Revised draft")
            assert browser.evaluate("document.querySelector('#editor').innerText") == "Revised draft"
            page = browser.observe(screenshot=False)
            save = next(a for a in page["actions"] if a["label"] == "Save draft")
            browser.act(save, page)
            assert browser.evaluate("document.querySelector('output').textContent") == "Revised draft"
            assert browser.evaluate("document.querySelector('#other-editor').innerText") == "Keep this paragraph"
        passed.append("contenteditable variants replace and save text while respecting non-editable controls")

        page = browser.observe(screenshot=False)
        field = next(a for a in page["actions"] if a["label"] == "Draft" and a["kind"] == "fill")
        browser.evaluate("document.querySelector('#editor').contentEditable='false'")
        assert not browser.fresh(page)
        try:
            browser.act(field, page, text="Must not be inserted")
        except StalePage:
            pass
        else:
            raise AssertionError("Editor disabled after observation was typed into")
        assert browser.evaluate("document.querySelector('#editor').innerText") == "Revised draft"
        passed.append("removing editability invalidates the observed text action")

        browser.evaluate("document.body.innerHTML=" + repr("""
          <button type="button" onclick="document.querySelector('output').textContent='Inbox'">Move → Inbox</button>
          <button type="button" onclick="document.querySelector('output').textContent='Archive'">Move → Archive</button>
          <label>Origin → Destination<input value="Current"></label>
          <select aria-label="Delivery → Method"><option>Current</option>
            <option value="archive">Inbox → Archive</option><option value="trash">Inbox → Trash</option></select>
          <output></output>
        """))
        page = browser.observe(screenshot=False)
        elements, targets, _ = action_space(page["actions"])
        assert [e["label"] for e in elements] == [
            "Move → Inbox", "Move → Archive", "Origin → Destination", "Delivery → Method",
        ]
        assert elements[-1]["options"][0]["label"] == "Delivery → Method → Inbox → Archive"
        browser.act(targets["SELECT"]["4:1"], page)
        assert browser.evaluate("document.querySelector('select').value") == "archive"
        page = browser.observe(screenshot=False)
        _, targets, _ = action_space(page["actions"])
        browser.act(targets["CLICK"]["2"], page)
        assert browser.evaluate("document.querySelector('output').textContent") == "Archive"
        passed.append("literal arrows survive element names without changing select or click targets")
    finally:
        browser.close()

    filter_html = """<!doctype html><title>Destination filter</title>
      <label>Destination<input id="destination" type="search" value="Lisbon"></label>
      <p id="results">Showing stays in Lisbon</p>
      <script>
        window.typedValues=[];
        document.querySelector('#destination').addEventListener('input',event=>{
          window.typedValues.push(event.target.value);
          document.querySelector('#results').textContent=event.target.value
            ? 'Showing stays in '+event.target.value : 'Showing stays in all destinations';
        });
      </script>"""
    outcome = """({value:document.querySelector('#destination').value,
      inputs:window.typedValues, results:document.querySelector('#results').textContent})"""
    for goal, text in [
        ("Clear the Destination filter to show all destinations.", ""),
        ("Show stays in London.", "London"),
        ("Show stays in my preferred destination.", None),
    ]:
        def provider(_url, _key, body):
            if "questions" in body:
                questions = body["questions"]
                selected = {
                    "operation": "TYPE_TEXT",
                    "type_text_target": next(iter(questions["type_text_target"]["criteria"])),
                }
                return {"model": "offline-test", "answers": {
                    name: {"choice": choice, "confidence": 1.0,
                           "probabilities": {key: float(key == choice) for key in questions[name]["criteria"]}}
                    for name, choice in selected.items()
                }}
            return {"choices": [{"message": {"content": json.dumps({"text": text})}}]}

        with patch.dict("os.environ", {"TYPESAFE_API_KEY": "offline-test", "TEXT_MODEL_API_KEY": "offline-test"}), \
                patch.object(model, "post_json", side_effect=provider), \
                Agent("data:text/html," + quote(filter_html), goal) as agent:
            before = agent.browser.evaluate(outcome)
            if text is None:
                try:
                    agent.command("tick")
                except ValueError as error:
                    assert "nothing typed" in str(error)
                else:
                    raise AssertionError("Missing text was executed")
                assert agent.browser.evaluate(outcome) == before
                assert not agent.state["history"] and not agent.state["text_calls"]
            else:
                state = agent.command("tick")
                assert agent.browser.evaluate(outcome) == {
                    "value": text, "inputs": [text],
                    "results": "Showing stays in " + text if text else "Showing stays in all destinations",
                }
                assert len(state["history"]) == len(state["text_calls"]) == 1
                assert state["history"][0]["text"] == state["text_calls"][0]["value"] == text
    passed.append("agent ticks clear or replace the filter; missing text leaves the page and history unchanged")

    print("\n".join(passed))
    print(f"PASS: {len(passed)} browser guard checks; no live model calls")


if __name__ == "__main__":
    main()
