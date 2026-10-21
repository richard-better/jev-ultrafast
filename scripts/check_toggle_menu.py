"""Local-browser regression for transparent checkbox menu toggles. No model calls or external websites.

Many sites (including Wikipedia's Vector skin) open menus with a transparent checkbox laid over an icon.
The checkbox is the real hit target, so it must be offered and clickable even though its opacity is 0.
A transparent checkbox hidden underneath other content must still be rejected, both when observed and
when it is covered after observation. The same toggle inside an open shadow root must also work."""

from urllib.parse import quote

from jev_ultrafast.browser import Browser, StalePage

HTML = """<!doctype html><title>Toggle menu</title>
<style>
body{margin:30px;font:16px sans-serif}
.toggle{position:relative;width:32px;height:32px}
.toggle input{position:absolute;inset:0;margin:0;width:32px;height:32px;opacity:0;z-index:1;cursor:pointer}
.toggle span{position:absolute;inset:0}
#menu{display:none;padding-top:40px}
#menu-toggle:checked ~ #menu{display:block}
#buried{position:absolute;top:200px;left:30px;width:32px;height:32px;opacity:0}
#cover{position:absolute;top:190px;left:20px;width:80px;height:60px;background:#eee;z-index:2}
#host{position:absolute;top:300px;left:30px}
#late-cover{position:absolute;top:290px;left:20px;width:80px;height:60px;background:#ddd;z-index:3;display:none}
</style>
<div class="toggle"><input id="menu-toggle" type="checkbox" aria-label="Main menu"><span>&#9776;</span>
<nav id="menu"><a href="#about" onclick="window.opened='about'">About this site</a></nav></div>
<input id="buried" type="checkbox" aria-label="Buried toggle"><div id="cover">cover</div>
<div id="host"></div><div id="late-cover">late cover</div>
<script>
document.getElementById('host').attachShadow({mode:'open'}).innerHTML=
  '<style>input{position:absolute;inset:0;margin:0;width:32px;height:32px;opacity:0;z-index:1}' +
  'div{position:relative;width:32px;height:32px}</style>' +
  '<div><input type="checkbox" aria-label="Shadow menu"><span>&#9776;</span></div>';
</script>"""


def shadow_checked(browser):
    return browser.evaluate("document.getElementById('host').shadowRoot.querySelector('input').checked")


def main():
    browser = Browser("data:text/html," + quote(HTML))
    passed = []
    try:
        page = browser.observe(screenshot=False)
        labels = [a["label"] for a in page["actions"]]
        toggle = next((a for a in page["actions"] if a["label"] == "Main menu"), None)
        assert toggle, f"Transparent menu toggle should be offered; got {labels}"
        passed.append("transparent hit-testable checkbox is offered")

        assert not any(a["label"] == "Buried toggle" for a in page["actions"]), "Covered transparent checkbox offered"
        passed.append("covered transparent checkbox is not offered")

        shadow = next((a for a in page["actions"] if a["label"] == "Shadow menu"), None)
        assert shadow, f"Transparent toggle in an open shadow root should be offered; got {labels}"
        passed.append("transparent checkbox in an open shadow root is offered")

        # Covering the toggle after observation is geometry, not page meaning: the executor refuses it
        # before any input instead.
        browser.evaluate("document.getElementById('late-cover').style.display='block'")
        try:
            browser.act(shadow, page)
        except StalePage:
            pass
        else:
            raise AssertionError("A transparent toggle covered after observation must be refused")
        assert shadow_checked(browser) is False, "The refused toggle must not have been clicked"
        browser.evaluate("document.getElementById('late-cover').style.display='none'")
        passed.append("transparent checkbox covered after observation is refused before input")

        page = browser.observe(screenshot=False)
        shadow = next(a for a in page["actions"] if a["label"] == "Shadow menu")
        browser.act(shadow, page)
        assert shadow_checked(browser) is True
        passed.append("transparent checkbox in an open shadow root is clicked")

        page = browser.observe(screenshot=False)
        toggle = next(a for a in page["actions"] if a["label"] == "Main menu")
        browser.act(toggle, page)
        assert browser.evaluate("document.querySelector('#menu-toggle').checked") is True
        passed.append("transparent toggle click executes")

        page = browser.observe(screenshot=False)
        about = next((a for a in page["actions"] if a["label"] == "About this site"), None)
        assert about, "Menu link should be offered after opening the menu"
        browser.act(about, page)
        assert browser.evaluate("window.opened") == "about"
        passed.append("link inside the opened menu is clickable")
    finally:
        browser.close()
    for item in passed:
        print("PASS:", item)


if __name__ == "__main__":
    main()
