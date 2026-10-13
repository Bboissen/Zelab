#!/usr/bin/env python3
"""Render docs/SETUP.md as one HTML guide, optionally with installer status.

docs/SETUP.md is the only guide text. Each checklist step has a short `### title`
heading; doctor.py attaches its status to that heading. The renderer needs
only the standard library because `make` runs it before the controller exists.
"""

import argparse
from dataclasses import dataclass
import html
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs/SETUP.md"


@dataclass(frozen=True)
class Item:
    key: str
    phase: str
    title: str


ITEMS = (
    Item("host", "Before provisioning", "Server"),
    Item("admin", "Before privileged changes", "Administrator"),
    Item("tailscale", "Before private remote access", "Tailscale"),
    Item("harness", "Before active worker tasks", "Worker harness"),
    Item("hermes", "After Hermes exists", "Hermes"),
    Item("discord", "Before the first Discord test", "Discord"),
    Item("proof", "Before declaring success", "Test"),
)

PHASES = tuple(dict.fromkeys(item.phase for item in ITEMS))
STEPS = {item.title: item for item in ITEMS}
# Status labels from doctor.py, as the guide shows them.
BADGES = {"PASS": "Done", "PENDING": "To do", "FAIL": "Blocked", "WARN": "Advice",
          "MANUAL": "Check by hand", "UNKNOWN": "Not checked yet"}

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Zelab setup</title>
<style>
* {{ box-sizing: border-box; }}
body {{ max-width: 54rem; margin: 1.5rem auto; padding: 0 1.2rem; font: 15px/1.4 system-ui,sans-serif; color: #172b3a; background: #fff; }}
h1 {{ font-size: 1.5rem; margin: 0 0 .6rem; }}
h3 {{ font-size: 1rem; margin: 0; display: inline-flex; gap: .4rem; align-items: center; }}
.step {{ margin: .55rem 0; }} .step p {{ display: inline; margin: 0; }}
p {{ margin: .15rem 0 .5rem; }}
a {{ color: #00667d; overflow-wrap: anywhere; }}
pre, code {{ background: #edf2f7; border-radius: 4px; }}
code {{ padding: .1rem .2rem; overflow-wrap: anywhere; }}
pre {{ padding: .65rem; overflow-x: auto; margin: .5rem 0; }}
pre code {{ padding: 0; }}
.summary {{ background: #eaf6f8; border-left: 3px solid #00758a; padding: .6rem; margin-bottom: .6rem; }}
.status {{ font-size: .75rem; border-radius: 4px; padding: .1rem .4rem; color: #172b3a; }}
.PASS {{ background: #c8edda; }} .PENDING, .WARN {{ background: #ffe8a3; }}
.FAIL {{ background: #ffd0d0; }} .MANUAL, .UNKNOWN {{ background: #e0e7ed; }}
input {{ accent-color: #00758a; }}
.failure {{ color: #a22121; }}
.applications {{ border-top: 1px solid #91a8b6; margin-top: .7rem; padding-top: .5rem; }}
.application {{ margin: .35rem 0; }}
.application small {{ display: block; overflow-wrap: anywhere; }}
@media (prefers-color-scheme: dark) {{
  body {{ color: #edf3fa; background: #172431; }}
  pre, code {{ background: #243646; }} a {{ color: #85e3f1; }}
  .summary {{ background: #183c46; }} .failure {{ color: #ffabab; }}
}}
@media print {{ body {{ font-size: 10pt; margin: 0; }} }}
</style></head><body>
{title}{summary}{body}{applications}
</body></html>
"""


def inline(text, link_base=None):
    """Escape text, then render `code`, **bold** and [links](target)."""
    parts = re.split(r"(`[^`]*`)", text)
    out = []
    for part in parts:
        if part.startswith("`") and part.endswith("`") and len(part) > 1:
            out.append(f"<code>{html.escape(part[1:-1])}</code>")
            continue
        part = html.escape(part, quote=False)
        part = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", part)
        part = re.sub(r"(?<![\w*])\*([^*\s][^*]*)\*(?![\w*])", r"<em>\1</em>", part)

        def link(match):
            label, target = match.group(1), html.unescape(match.group(2))
            if callable(link_base) and not re.match(r"[a-z]+:|#", target):
                resolved = link_base(target)
                if resolved is None:
                    return f'{label} (in the server checkout: <code>{html.escape(target)}</code>)'
                target = resolved
            elif link_base and not re.match(r"[a-z]+:|#", target):
                path, _, anchor = target.partition("#")
                target = (link_base / path).resolve().as_uri() + (f"#{anchor}" if anchor else "")
            return f'<a href="{html.escape(target)}">{label}</a>'
        out.append(re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", link, part))
    return "".join(out)


LIST_ITEM = re.compile(r"(\d+\.|-) ")


def blocks(lines, link_base, heading=None):
    """Render the Markdown subset used by SETUP.md (no raw HTML is passed through)."""
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
        elif line.startswith("```"):
            end = next((j for j in range(i + 1, len(lines)) if lines[j].startswith("```")), len(lines))
            out.append(f"<pre><code>{html.escape(chr(10).join(lines[i + 1:end]))}</code></pre>")
            i = end + 1
        elif match := re.match(r"(#{1,3}) (.*)", line):
            level, text = len(match.group(1)), match.group(2)
            out.append(heading(level, text) if heading else f"<h{level}>{inline(text, link_base)}</h{level}>")
            i += 1
        elif line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [cell.strip() for cell in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-+:?", cell) for cell in cells):
                    rows.append(cells)
                i += 1
            head = "".join(f"<th>{inline(cell, link_base)}</th>" for cell in rows[0])
            body = "".join("<tr>" + "".join(f"<td>{inline(cell, link_base)}</td>" for cell in row) + "</tr>"
                           for row in rows[1:])
            out.append(f"<table><tr>{head}</tr>{body}</table>")
        elif line.startswith(">"):
            quoted = []
            while i < len(lines) and lines[i].startswith(">"):
                quoted.append(lines[i].lstrip("> ").rstrip())
                i += 1
            out.append(f"<blockquote>{blocks(quoted, link_base)}</blockquote>")
        elif match := LIST_ITEM.match(line):
            tag = "ul" if match.group(1) == "-" else "ol"
            items = []
            while i < len(lines) and (match := LIST_ITEM.match(lines[i])):
                indent = len(match.group(0))
                body = [lines[i][indent:]]
                i += 1
                # An item continues through indented lines and blank lines followed by them.
                while i < len(lines) and (lines[i].startswith(" " * indent) or not lines[i].strip()):
                    if not lines[i].strip() and not (i + 1 < len(lines) and lines[i + 1].startswith(" " * indent)):
                        break
                    body.append(lines[i][indent:])
                    i += 1
                items.append(f"<li>{blocks(body, link_base)}</li>")
                while i < len(lines) and not lines[i].strip():
                    i += 1
                if i < len(lines) and (tag == "ul") != lines[i].startswith("-"):
                    break
            out.append(f"<{tag}>{''.join(items)}</{tag}>")
        else:
            paragraph = []
            while i < len(lines) and lines[i].strip() and not re.match(r"```|#{1,3} |\||>", lines[i]) \
                    and not (paragraph and LIST_ITEM.match(lines[i])):
                paragraph.append(lines[i].strip())
                i += 1
            out.append(f"<p>{inline(' '.join(paragraph), link_base)}</p>")
    return "\n".join(out)


def render_html(statuses=None, generated=None, checks=None, link_base=None, standalone=False, applications=None):
    """Render only the short setup checklist; do not append other documents."""
    statuses, checks = statuses or {}, checks or {}

    def heading(level, text):
        item = STEPS.get(text) if level == 3 else None
        if item is None:
            anchor = ' id="guide-top"' if level == 1 else ""
            return f"<h{level}{anchor}>{inline(text, link_base)}</h{level}>"
        label, detail = statuses.get(item.key, (None, ""))
        done = " checked disabled" if label == "PASS" else ""
        badge = (f'<span class="status {html.escape(label)}" title="{html.escape(detail)}">'
                 f"{html.escape(BADGES.get(label, label))}</span>") if label else ""
        failures = "".join(f'<p class="failure">{html.escape(name)}: {html.escape(info)}</p>'
                           for state, name, info in checks.get(item.key, []) if state == 'FAIL')
        return (f'<h3 id="{item.key}"><input type="checkbox" id="step-{item.key}"{done}>'
                f'<label for="step-{item.key}">{inline(text, link_base)}</label>{badge}</h3>' + failures)

    summary = ""
    if generated:
        done = sum(statuses.get(item.key, ("",))[0] == "PASS" for item in ITEMS)
        following = next((item for item in ITEMS if statuses.get(item.key, ("",))[0] != "PASS"), None)
        upcoming = f' Next: <a href="#{following.key}">{html.escape(following.title)}</a>.' if following else ""
        summary = f'<p class="summary">{done} of {len(ITEMS)} steps done.{upcoming}</p>'
    body = blocks(GUIDE.read_text().splitlines(), link_base, heading)
    body = re.sub(r'(<h3[^>]*>.*?</h3>)\n(<p>.*?</p>)', r'<div class="step">\1 \2</div>', body, flags=re.S)
    title = re.search(r'<h1 id="guide-top">.*?</h1>', body).group(0)
    body = body.replace(title, "", 1)
    links = []
    for app in applications or []:
        name, url = html.escape(app['name']), html.escape(app['url'], quote=True)
        entry = f'<a href="{url}">{name}</a>' if app['tailnet'] else f'<strong>{name}</strong> <code>{url}</code> (server only)'
        links.append(f'<div class="application">{entry} — {html.escape(app["user"])}'
                     f'<small>Password: <code>{html.escape(app["password"])}</code></small></div>')
    apps = '<div class="applications">' + ''.join(links) + '</div>' if links else ''
    return PAGE.format(title=title, summary=summary, body=body, applications=apps)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html", type=Path, required=True, help="Write the static public guide here")
    args = parser.parse_args(argv)
    args.html.write_text(render_html())
    print(f"Guide: {args.html}")


if __name__ == "__main__":
    main()
