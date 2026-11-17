"""Write each drawing of `results.html` as two SVG files, one for a light page and one for a dark page.

    python docs/figures/export.py --chrome PATH_OF_A_HEADLESS_CHROME

The page builds its charts in the browser. This script lets a headless Chrome build them, takes each drawing, and adds
the legend and the colors, so that a README or a release note can show the file as an image.
"""

import argparse
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).parent
W = 880
MONO = 'ui-monospace, SFMono-Regular, Menlo, Consolas, "DejaVu Sans Mono", monospace'
SERIF = 'Georgia, "Times New Roman", serif'
THEMES = {   # the colors of the page, for each theme
    "light": {"bg": "#f6f3ec", "panel": "#eee9dd", "ink": "#1c222c", "muted": "#5c6470", "rule": "#d6cfc0",
              "blue": "#0F86B0", "brown": "#A8501C", "gold": "#9A7400", "red": "#C23A2E"},
    "dark": {"bg": "#10141c", "panel": "#161b25", "ink": "#ece8dd", "muted": "#9aa3b2", "rule": "#2a3140",
             "blue": "#2B9FC6", "brown": "#C8763E", "gold": "#E8C547", "red": "#F0685A"},
}
BLUE_LINE, BROWN_LINE = ("line", "blue", "the agent with Uniselector"), ("line", "brown", "the agent alone")
# The drawings in the order of the page: the file name, how many drawings of the page make it, and its legend.
FIGURES = [
    ("context", 1, [BROWN_LINE, BLUE_LINE]),
    ("two-loops", 1, []),
    ("looks", 1, [("dot", "blue", "inside the limit"), ("dot", "gold", "outside: a wake")]),
    ("detector", 1, [("dot", "blue", "the statement is true (42)"), ("ring", "brown", "the statement is not true (21)"),
                    ("ring", "red", "not true, and the model said true (1)")]),
    ("longer-wait", 3, [BROWN_LINE, BLUE_LINE]),
    ("runners-in-order", 1, []),
    ("timeline", 1, [("dot", "blue", "a look, all inside"), ("dot", "gold", "a look, one outside"),
                     ("ring", "gold", "outside, and the wake waits for the runner")]),
    ("sessions", 1, [("bar", "brown", "the agent alone"), ("bar", "blue", "the agent with Uniselector")]),
    ("replacement-check", 1, []),
]


def css(c: dict) -> str:
    return f"""
text {{ font-family: {MONO}; font-size: 12px; fill: {c['ink']}; }}
text.m {{ fill: {c['muted']}; }} text.s {{ font-family: {SERIF}; font-style: italic; font-size: 14.5px; }} text.h {{ font-weight: 600; }}
.ax {{ stroke: {c['rule']}; stroke-width: 1; fill: none; }} .ln {{ fill: none; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }}
.sb {{ stroke: {c['blue']}; }} .sr {{ stroke: {c['brown']}; }} .sg {{ stroke: {c['gold']}; }} .sm {{ stroke: {c['muted']}; }} .si {{ stroke: {c['ink']}; }} .sx {{ stroke: {c['red']}; }}
.fb {{ fill: {c['blue']}; }} .fr {{ fill: {c['brown']}; }} .fg {{ fill: {c['gold']}; }} .fx, text.fx {{ fill: {c['red']}; }} .fbg, text.fbg {{ fill: {c['bg']}; }} .fp {{ fill: {c['panel']}; }} .fn {{ fill: none; }} .fm {{ fill: {c['muted']}; }}
.box {{ fill: {c['panel']}; stroke: {c['rule']}; stroke-width: 1.5; }} .dash {{ stroke-dasharray: 5 5; }}
text.halo {{ paint-order: stroke; stroke: {c['bg']}; stroke-width: 4px; stroke-linejoin: round; }}
.chip.on rect {{ stroke: {c['gold']}; stroke-width: 2.5; }}
"""


def legend(keys: list, c: dict, y: float) -> str:
    out, x = [], 16
    for kind, color, label in keys:
        if kind == "line":
            out.append(f'<rect x="{x}" y="{y - 5.5}" width="18" height="3" rx="1.5" fill="{c[color]}"/>')
            x += 26
        elif kind == "bar":
            out.append(f'<rect x="{x}" y="{y - 9}" width="14" height="10" rx="2" fill="{c[color]}"/>')
            x += 22
        else:
            ring = kind == "ring"
            out.append(f'<circle cx="{x + 5}" cy="{y - 4}" r="{3.5 if ring else 4.5}" fill="{c["bg"] if ring else c[color]}"'
                       + (f' stroke="{c[color]}" stroke-width="2"/>' if ring else "/>"))
            x += 18
        out.append(f'<text x="{x}" y="{y}" class="m">{label}</text>')
        x += len(label) * 7.3 + 26   # the width of one character of the 12px type, and the space to the next key
    return "".join(out)


def parts(svg: str) -> tuple:
    """The view box, the label and the inner part of one drawing of the page."""
    head, inner = re.match(r"<svg([^>]*)>(.*)</svg>$", svg, re.S).groups()
    box = [float(n) for n in re.search(r'viewBox="([^"]+)"', head).group(1).split()]
    label = re.search(r'aria-label="([^"]*)"', head).group(1)
    return box, label, inner


def figure(drawings: list, keys: list, titles: list, c: dict) -> str:
    top = 34 if keys else 0
    if len(drawings) == 1:
        (_, _, _, h), label, body = parts(drawings[0])
    else:   # small charts side by side, each one under its title
        each, label, body, h = W / len(drawings), "", "", 0
        for i, (drawing, title) in enumerate(zip(drawings, titles)):
            (_, _, w, h0), text, inner = parts(drawing)
            label += text + " "
            body += f'<text x="{i * each + each / 2}" y="18" text-anchor="middle" class="m">{title}</text>'
            body += f'<g transform="translate({i * each + (each - w) / 2} 30)">{inner}</g>'
            h = max(h, h0 + 30)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 {-top} {W} {h + top}" width="{W}" role="img" aria-label="{label.strip()}">'
            f"<title>{label.strip()}</title><style>{css(c)}</style>"
            f'<rect x="0" y="{-top}" width="{W}" height="{h + top}" rx="8" fill="{c["bg"]}"/>'
            f"{legend(keys, c, -12)}{body}</svg>\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chrome", required=True, help="the path of a Chrome or a headless shell of Chrome")
    args = ap.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "page.html"
        page.write_text('<!doctype html><html><head><meta charset="utf-8">' + (HERE / "results.html").read_text())
        dom = subprocess.run([args.chrome, "--headless", "--no-sandbox", "--force-prefers-reduced-motion", "--virtual-time-budget=4000",
                              "--dump-dom", page.as_uri()], capture_output=True, text=True, check=True).stdout
    drawings = re.findall(r"<svg\b.*?</svg>", dom, re.S)
    titles = re.findall(r"<h3>(.*?)</h3>", dom)
    assert len(drawings) == sum(n for _, n, _ in FIGURES), f"the page has {len(drawings)} drawings"
    at = 0
    for name, n, keys in FIGURES:
        for theme, colors in THEMES.items():
            text = figure(drawings[at:at + n], keys, titles, colors)
            ET.fromstring(text)   # the file must be XML that an image reader can read
            (HERE / f"{name}-{theme}.svg").write_text(text)
        at += n


if __name__ == "__main__":
    main()
