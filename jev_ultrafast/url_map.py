"""URL maps (experimental): loaded data files of URL patterns that open a site's known pages directly.

An entry is offered as its own operation while the current page is on the entry's site. The model
only chooses it; the text model fills its typed parameters from the goal, they are validated here,
and code builds the URL on the entry's own site. The model never writes a URL.
"""

import json
import os
import re
from datetime import date
from pathlib import Path
from urllib.parse import quote, urlparse

ID = re.compile(r"^[a-z][a-z0-9_]{2,60}$")
PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*)\}")
TYPES = {"text", "date", "choice", "integer"}
BUILT_IN = {"CLICK", "TYPE_TEXT", "SELECT", "DONE", "BLOCKED", "WAIT", "SCROLL_DOWN", "SCROLL_UP"}


class UrlMapError(ValueError):
    """A map file, or the parameters chosen for one of its entries, is not valid."""


def load(source=None):
    """Entries from a map path, a list of paths, or a parsed map; JEV_URL_MAP (os.pathsep-separated) if None."""
    if source is None:
        source = [p for p in os.environ.get("JEV_URL_MAP", "").split(os.pathsep) if p.strip()]
    sources = source if isinstance(source, list) else [source]
    entries = []
    for item in sources:
        data = item if isinstance(item, dict) else json.loads(Path(item).read_text(encoding="utf-8"))
        for raw in data.get("entries", []):
            entries.append(_entry(raw))
    operations = [e["operation"] for e in entries]
    if len(set(operations)) != len(operations):
        raise UrlMapError("URL map entry ids must be unique")
    return entries


def _entry(raw):
    entry_id = raw.get("id", "")
    if not ID.match(entry_id):
        raise UrlMapError(f"URL map entry id {entry_id!r} must be lowercase letters, digits and underscores")
    operation = "OPEN_" + entry_id.upper()
    if operation in BUILT_IN:
        raise UrlMapError(f"URL map entry {entry_id!r} collides with a built-in operation")
    description = raw.get("description", "")
    if not isinstance(description, str) or not 10 <= len(description) <= 200:
        raise UrlMapError(f"URL map entry {entry_id!r} needs a 10-200 character description")
    template = raw.get("url", "")
    site = urlparse(template)
    if site.scheme != "https" or not site.hostname or "{" in site.netloc:
        raise UrlMapError(f"URL map entry {entry_id!r} needs an https URL with a fixed host")
    parameters = raw.get("parameters", {})
    if set(PLACEHOLDER.findall(template)) != set(parameters):
        raise UrlMapError(f"URL map entry {entry_id!r} must use exactly its declared parameters in its URL")
    for name, spec in parameters.items():
        kind = spec.get("type")
        if kind not in TYPES or not spec.get("description"):
            raise UrlMapError(f"Parameter {name!r} of {entry_id!r} needs a type in {sorted(TYPES)} and a description")
        if kind == "choice" and not (isinstance(spec.get("options"), list) and spec["options"]):
            raise UrlMapError(f"Choice parameter {name!r} of {entry_id!r} needs options")
    match = raw.get("match", {})
    return {
        "id": entry_id,
        "operation": operation,
        "description": description,
        "url": template,
        "host": site.hostname,
        "path_prefix": match.get("path_prefix", "/"),
        "parameters": parameters,
    }


def actions(entries, page_url):
    """The entries offered on this page: those whose site and path prefix match it."""
    page = urlparse(page_url)
    return [
        {
            "id": entry["operation"].lower(),
            "kind": "shortcut",
            "shortcut": entry["id"],
            "label": f"Open directly: {entry['description']}",
        }
        for entry in entries
        if page.scheme == "https" and page.hostname == entry["host"] and page.path.startswith(entry["path_prefix"])
    ]


def _value(name, spec, value):
    kind = spec["type"]
    if kind == "text":
        limit = spec.get("max_length", 80)
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= limit or any(ord(c) < 32 for c in value):
            raise UrlMapError(f"{name} must be 1-{limit} characters of text")
        return value.strip()
    if kind == "date":
        try:
            if isinstance(value, str) and len(value) == 10:
                return date.fromisoformat(value).isoformat()
        except ValueError:
            pass
        raise UrlMapError(f"{name} must be a date as YYYY-MM-DD")
    if kind == "choice":
        if value not in spec["options"]:
            raise UrlMapError(f"{name} must be one of {spec['options']}")
        return value
    low, high = spec.get("min", 1), spec.get("max", 9)
    if type(value) is not int or not low <= value <= high:
        raise UrlMapError(f"{name} must be a whole number from {low} to {high}")
    return str(value)


def build(entry, values):
    """The entry's URL with every parameter validated and encoded, on the entry's own site."""
    if not isinstance(values, dict) or set(values) != set(entry["parameters"]):
        raise UrlMapError(f"Parameters must be exactly {sorted(entry['parameters'])}")
    encoded = {}
    for name, spec in entry["parameters"].items():
        encoded[name] = quote(_value(name, spec, values[name]), safe="")
    url = PLACEHOLDER.sub(lambda m: encoded[m.group(1)], entry["url"])
    built = urlparse(url)
    if built.scheme != "https" or built.hostname != entry["host"]:
        raise UrlMapError("The built URL left the entry's site")
    return url


def context(entry, goal, page, history, today=None):
    """What the text model sees when filling an entry's parameters."""
    return {
        "goal": goal,
        "today": (today or date.today()).isoformat(),
        "shortcut": entry["description"],
        "parameters": entry["parameters"],
        "page": {"title": page["title"], "url": page["url"]},
        "recent_actions": [{k: h.get(k) for k in ("action", "text")} for h in history[-6:]],
    }
