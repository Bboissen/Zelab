#!/usr/bin/env python3
"""Render docs/SETUP.md as one HTML guide, optionally with installer status.

docs/SETUP.md is the only guide text. Each checklist step has a short `### title`
heading and a one-line first paragraph; the rest of the step folds into Details.
doctor.py attaches its status and open checks to that step. The renderer needs
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
:root {{ --fg: #172b3a; --muted: #5b6f7d; --bg: #f5f7f9; --card: #fff; --line: #dde4ea; --accent: #00758a;
  --code: #edf2f7; --ok: #c8edda; --todo: #ffe8a3; --bad: #ffd0d0; --grey: #e0e7ed; }}
body {{ max-width: 46rem; margin: 2rem auto; padding: 0 1rem; font: 15px/1.5 system-ui, sans-serif;
  color: var(--fg); background: var(--bg); }}
h1 {{ font-size: 1.4rem; margin: 0; }}
h2 {{ font-size: .8rem; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); margin: 1.6rem 0 .5rem; }}
p {{ margin: .2rem 0; }} li p {{ margin: 0; }} ul {{ margin: .3rem 0; padding-left: 1.2rem; }} li {{ margin: .15rem 0; }}
a {{ color: var(--accent); overflow-wrap: anywhere; }}
code {{ background: var(--code); border-radius: 4px; padding: .05rem .25rem; font-size: .88em; overflow-wrap: anywhere; }}
pre {{ background: var(--code); border-radius: 6px; padding: .6rem; overflow-x: auto; margin: .4rem 0; }}
pre code {{ padding: 0; }}
.intro {{ color: var(--muted); margin: .4rem 0 0; }}
.progress {{ display: flex; align-items: center; gap: .6rem; margin-top: .8rem; font-size: .85rem; color: var(--muted); }}
.bar {{ flex: 1; height: 6px; border-radius: 3px; background: var(--line); overflow: hidden; }}
.bar span {{ display: block; height: 100%; background: var(--accent); }}
.apps {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(13rem, 1fr)); gap: .5rem; }}
.app {{ background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: .6rem .75rem; }}
.app small {{ display: block; color: var(--muted); overflow-wrap: anywhere; }}
.todo {{ list-style: none; padding: 0; margin: 0; background: var(--card); border: 1px solid var(--line); border-radius: 8px; }}
.step {{ display: flex; gap: .7rem; padding: .7rem .9rem; margin: 0; border-top: 1px solid var(--line); }}
.step:first-child {{ border-top: 0; }}
.step.next {{ box-shadow: inset 3px 0 var(--accent); }}
.step input {{ margin-top: .3rem; accent-color: var(--accent); width: 1rem; height: 1rem; flex: none; }}
.step > div {{ flex: 1; min-width: 0; }}
.head {{ display: flex; align-items: center; gap: .5rem; }}
.head label {{ font-weight: 600; }}
.PASS .head label {{ color: var(--muted); text-decoration: line-through; }}
.desc {{ color: var(--muted); }}
.hint {{ font-size: .88rem; border-radius: 4px; padding: .25rem .5rem; margin: .35rem 0; background: var(--todo); }}
.hint.FAIL {{ background: var(--bad); }} .hint.WARN {{ background: var(--grey); }}
details {{ margin-top: .3rem; font-size: .92rem; }}
summary {{ cursor: pointer; color: var(--accent); font-size: .85rem; }}
.more {{ background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: .5rem .9rem; }}
.status {{ font-size: .72rem; border-radius: 4px; padding: .05rem .4rem; color: #172b3a; }}
.status.PASS {{ background: var(--ok); }} .status.PENDING, .status.WARN {{ background: var(--todo); }}
.status.FAIL {{ background: var(--bad); }} .status.MANUAL, .status.UNKNOWN {{ background: var(--grey); }}
@media (prefers-color-scheme: dark) {{
  :root {{ --fg: #edf3fa; --muted: #9fb2c1; --bg: #121c25; --card: #1a2733; --line: #2c3d4c; --accent: #5fd0e0;
    --code: #243646; --todo: #5a4a14; --bad: #5c2626; --grey: #2c3d4c; }}
  .hint {{ color: var(--fg); }}
}}
@media print {{ body {{ background: #fff; margin: 0; }} details {{ display: block; }} }}
</style></head><body>
<header><h1>{title}</h1>{intro}{progress}</header>
{applications}<h2>To do</h2>
<ol class="todo">
{steps}
</ol>
{extra}
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
            if link_base and not re.match(r"[a-z]+:|#", target):
                path, _, anchor = target.partition("#")
                target = (link_base / path).resolve().as_uri() + (f"#{anchor}" if anchor else "")
            return f'<a href="{html.escape(target)}">{label}</a>'
        out.append(re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", link, part))
    return "".join(out)


LIST_ITEM = re.compile(r"(\d+\.|-) ")


def blocks(lines, link_base):
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
            out.append(f"<h{level}>{inline(text, link_base)}</h{level}>")
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


def code_spans(text):
    """Escape doctor's check text, keeping only its `code` spans."""
    return "".join(f"<code>{html.escape(part[1:-1])}</code>" if part.startswith("`") and part.endswith("`")
                   and len(part) > 1 else html.escape(part) for part in re.split(r"(`[^`]*`)", text))


def sections(lines):
    """Split SETUP.md into its title, intro, `### step`s and closing `## section`s."""
    title, intro, steps, extra = "", [], {}, []
    current, fenced = intro, False
    for line in lines:
        if line.startswith("```"):
            fenced = not fenced
        if not fenced and line.startswith("# "):
            title = line[2:]
        elif not fenced and line.startswith("### "):
            current = steps.setdefault(line[4:], [])
        elif not fenced and line.startswith("## "):
            extra.append((line[3:], []))
            current = extra[-1][1]
        else:
            current.append(line)
    return title, intro, steps, extra


def render_html(statuses=None, generated=None, checks=None, link_base=None, applications=None):
    """Render SETUP.md as a short to-do list; each step's extra text folds away."""
    statuses, checks = statuses or {}, checks or {}
    title, intro, steps, extra = sections(GUIDE.read_text().splitlines())
    following = next((item for item in ITEMS if statuses.get(item.key, ("",))[0] != "PASS"), None) \
        if generated else None

    rows = []
    for item in ITEMS:
        text = "\n".join(steps[item.title]).strip().split("\n\n", 1)
        desc, more = inline(" ".join(text[0].split()), link_base), text[1] if len(text) > 1 else ""
        label, detail = statuses.get(item.key, (None, ""))
        done = " checked disabled" if label == "PASS" else ""
        badge = (f'<span class="status {html.escape(label)}" title="{html.escape(detail)}">'
                 f"{html.escape(BADGES.get(label, label))}</span>") if label else ""
        hints = "".join(f'<p class="hint {html.escape(state)}"><strong>{html.escape(name)}</strong>: '
                        f"{code_spans(info)}</p>"
                        for state, name, info in checks.get(item.key, []) if state != "PASS" and label != "MANUAL")
        details = (f'<details{" open" if item is following else ""}><summary>Details</summary>'
                   f"{blocks(more.splitlines(), link_base)}</details>") if more else ""
        state = f' class="step {html.escape(label or "")}{" next" if item is following else ""}"'
        rows.append(f'<li{state} id="{item.key}"><input type="checkbox" id="step-{item.key}"{done}><div>'
                    f'<div class="head"><label for="step-{item.key}">{html.escape(item.title)}</label>{badge}</div>'
                    f'<p class="desc">{desc}</p>{hints}{details}</div></li>')

    progress = ""
    if generated:
        count = sum(statuses.get(item.key, ("",))[0] == "PASS" for item in ITEMS)
        upcoming = f' Next: <a href="#{following.key}">{html.escape(following.title)}</a>.' if following else ""
        progress = (f'<div class="progress"><div class="bar"><span style="width:{100 * count // len(ITEMS)}%">'
                    f"</span></div><span>{count} of {len(ITEMS)} steps done.{upcoming} "
                    f"Updated {html.escape(generated)}.</span></div>")

    links = []
    for app in applications or []:
        name, url = html.escape(app['name']), html.escape(app['url'], quote=True)
        entry = f'<a href="{url}"><strong>{name}</strong></a>' if app['tailnet'] \
            else f'<strong>{name}</strong> <small><code>{url}</code> (server only)</small>'
        links.append(f'<div class="app">{entry}<small>Login: {html.escape(app["user"])}</small>'
                     f'<small>Password: <code>{html.escape(app["password"])}</code></small></div>')
    apps = f'<h2>Your apps</h2><div class="apps">{"".join(links)}</div>' if links else ""

    rest = "".join(f'<h2>{inline(name, link_base)}</h2><div class="more">{blocks(body, link_base)}</div>'
                   for name, body in extra)
    return PAGE.format(title=inline(title, link_base), intro=f'<p class="intro">{inline(" ".join(" ".join(intro).split()), link_base)}</p>',
                       progress=progress, applications=apps, steps="\n".join(rows), extra=rest)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html", type=Path, required=True, help="Write the static public guide here")
    args = parser.parse_args(argv)
    args.html.write_text(render_html())
    print(f"Guide: {args.html}")


if __name__ == "__main__":
    main()
