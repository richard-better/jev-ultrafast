"""Where cache.point clicks, run in Node against a small fake page (no browser, no paid API).

cache.point is copied verbatim out of snapshot.js. The snapshot offers a control only where it
returns a point, and the act guard in browser.py clicks that same point.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")

SOURCE = (Path(__file__).resolve().parents[1] / "jev_ultrafast" / "snapshot.js").read_text(encoding="utf-8")


def snippet():
    """From the `roles` list through `cache.point`: definitions only, nothing that needs a DOM."""
    start = SOURCE.index("const roles=[")
    point = SOURCE.index("cache.point=e=>{")
    end = SOURCE.index("\n  };", point) + len("\n  };")
    return SOURCE[start:end]


# A choice card: <label> (100,100 200x150) holds a visible text span and a choice clipped to 1x1 px. The label
# covers the input, as a styled choice does. Each SCENE adds one thing: an overlay over everything, a control at
# the card's centre (a link, a button or a role=checkbox), an inert card, a second label with the first one
# covered, or an input that is on top and clicked directly.
FAKE_PAGE = r"""
const rect = (x, y, width, height) => ({x, y, width, height});
const inside = (r, x, y) => x >= r.x && x <= r.x + r.width && y >= r.y && y <= r.y + r.height;
function hits(n, token) {  // enough of CSS matching for the selectors cache.point uses
  const role = /^\[role="?([a-z]+)"?\]$/.exec(token);
  if (role) return n.role === role[1];
  if (token.startsWith('[')) return false;
  const [tag, attr] = token.split('[');
  if (tag === 'input' && attr) return n.tagName === 'INPUT' && attr === `type=${n.type}]`;
  return tag === n.tagName.toLowerCase() && (!attr || attr !== 'href]' || !!n.href);
}
function node(tag, r, extra = {}) {
  return {tagName: tag, parent: null, r, ...extra,
    matches(sel) { return sel.split(',').some(t => hits(this, t.trim())); },
    closest(sel) {
      for (let n = this; n; n = n.parent) if (n.matches(sel)) return n;
      return null;
    },
    getBoundingClientRect() { return this.r; },
    getClientRects() { return [this.r]; }};
}
const label = node('LABEL', rect(100, 100, 200, 150));
// a second <label for> of the same input, elsewhere; in the 'inert' scene it sits in an inert region
const label2 = node('LABEL', rect(400, 100, 200, 150), {inert: SCENE === 'inert'});
const text = node('SPAN', rect(110, 110, 180, 30)); text.parent = label;
const labels = SCENE === 'two' ? [label, label2] : SCENE === 'inert' ? [label2] : [label];
const input = node('INPUT', rect(105, 105, 1, 1), {type: TYPE, labels});
input.parent = label;
const overlay = node('DIV', rect(0, 0, 1280, 800));
const middle = rect(150, 150, 100, 50);
const inner = {link: node('A', middle, {href: '/terms'}), button: node('BUTTON', middle),
               rolebox: node('DIV', middle, {role: 'checkbox'})}[SCENE];
if (inner) inner.parent = label;
globalThis.innerWidth = 1280; globalThis.innerHeight = 800;
const at = (x, y) =>
  SCENE === 'overlay' || (SCENE === 'two' && inside(label.r, x, y)) ? overlay
  : SCENE === 'ontop' && inside(input.r, x, y) ? input
  : inside(label2.r, x, y) ? label2 : inside(text.r, x, y) ? text : inner && inside(inner.r, x, y) ? inner : label;
const cache = {
  hit: at,
  within(e, n) { for (; n; n = n.parent) if (n === e) return true; return false; },
  visible(n) { for (let p = n; p; p = p.parent) if (p.inert) return false; return true; },
};
"""


def where(scene, type_="radio"):
    script = (
        f"const SCENE = {json.dumps(scene)}, TYPE = {json.dumps(type_)};\n{FAKE_PAGE}\n{snippet()}\n"
        "console.log(JSON.stringify(cache.point(input)));"
    )
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20, check=True)
    return json.loads(out.stdout)


CHOICES = pytest.mark.parametrize("type_", ["radio", "checkbox"])


@CHOICES
def test_a_choice_hidden_inside_its_label_is_clicked_on_the_label(type_):
    # A person clicks the card and the option is chosen; the 1x1 input itself is covered by its own label.
    assert where("card", type_) == {"x": 200, "y": 175}


@CHOICES
def test_a_choice_on_top_is_clicked_at_its_own_centre(type_):
    assert where("ontop", type_) == {"x": 105.5, "y": 105.5}


@CHOICES
def test_a_choice_covered_by_something_else_is_still_refused(type_):
    assert where("overlay", type_) is None


def test_only_a_checkbox_or_radio_falls_back_to_its_label():
    # A text field under its label has no label fallback: clicking the label would only focus it.
    assert where("card", "text") is None


@CHOICES
@pytest.mark.parametrize("scene", ["link", "button", "rolebox"])
def test_a_label_whose_centre_holds_another_control_is_refused(scene, type_):
    # The click would go to that control (follow the link, press the button) instead of choosing the option.
    assert where(scene, type_) is None


@CHOICES
def test_a_label_in_an_inert_region_is_never_clicked(type_):
    assert where("inert", type_) is None  # the click would do nothing there


@CHOICES
def test_a_covered_label_gives_way_to_the_next_usable_one(type_):
    assert where("two", type_) == {"x": 500, "y": 175}
