"""SVG figures for the architecture guide. Standard library only.

Every figure is a function returning an SVG string; build.py substitutes them for
{{fig:name}} placeholders in the chapters. One colour per layer, used everywhere:
collector sky, ingest green, search indigo, digest pink, service orange, the model purple, the outside
world grey.
"""
from __future__ import annotations

import html
import math

FONT = "'Helvetica Neue', Helvetica, Arial, sans-serif"
MONO = "Menlo, 'SF Mono', Consolas, monospace"

KINDS = {
    #          stroke     fill       text
    'nc':     ('#2563eb', '#dbeafe', '#1e3a8a'),
    'cc':     ('#059669', '#d1fae5', '#064e3b'),
    'llm':    ('#7c3aed', '#ede9fe', '#4c1d95'),
    'sec':    ('#d97706', '#fef3c7', '#78350f'),
    'ext':    ('#6b7280', '#f3f4f6', '#1f2937'),
    'you':    ('#0284c7', '#e0f2fe', '#0c4a6e'),
    'voice':  ('#db2777', '#fce7f3', '#831843'),
    'file':   ('#475569', '#f8fafc', '#0f172a'),
    'warn':   ('#dc2626', '#fee2e2', '#7f1d1d'),
    'plain':  ('#334155', '#ffffff', '#0f172a'),
}


def e(s: str) -> str:
    return html.escape(str(s), quote=True)


class Fig:
    def __init__(self, w: int, h: int):
        self.w, self.h, self.parts = w, h, []

    # -- primitives -------------------------------------------------------------
    def rect(self, x, y, w, h, kind='plain', r=10, dash=False, fill=None, sw=1.6, opacity=1):
        st, fl, _ = KINDS[kind]
        d = ' stroke-dasharray="6 4"' if dash else ''
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" ry="{r}" '
                          f'fill="{fill or fl}" stroke="{st}" stroke-width="{sw}"{d} opacity="{opacity}"/>')

    def text(self, x, y, s, size=12, weight='normal', color=None, anchor='middle', kind=None,
             italic=False, mono=False):
        c = color or (KINDS[kind][2] if kind else '#0f172a')
        st = ' font-style="italic"' if italic else ''
        fam = MONO if mono else FONT
        self.parts.append(f'<text x="{x}" y="{y}" font-family="{fam}" font-size="{size}" '
                          f'font-weight="{weight}" fill="{c}" text-anchor="{anchor}"{st}>{e(s)}</text>')

    def box(self, x, y, w, h, title, lines=(), kind='plain', r=10, tsize=13, lsize=11, dash=False,
            mono_lines=False, title_top=None):
        self.rect(x, y, w, h, kind, r, dash)
        n = len(lines)
        total = tsize + n * (lsize + 4)
        ty = title_top if title_top is not None else y + (h - total) / 2 + tsize - 1
        self.text(x + w / 2, ty, title, tsize, 'bold', kind=kind)
        for i, ln in enumerate(lines):
            self.text(x + w / 2, ty + (i + 1) * (lsize + 4) + 1, ln, lsize, kind=kind, mono=mono_lines)

    def line(self, x1, y1, x2, y2, kind='ext', dash=False, sw=1.6, head=True, both=False):
        st = KINDS[kind][0]
        d = ' stroke-dasharray="5 4"' if dash else ''
        m = f' marker-end="url(#a-{kind})"' if head else ''
        m += f' marker-start="url(#s-{kind})"' if both else ''
        self.parts.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{st}" '
                          f'stroke-width="{sw}"{d}{m}/>')

    def path(self, d, kind='ext', dash=False, sw=1.6, head=True):
        st = KINDS[kind][0]
        da = ' stroke-dasharray="5 4"' if dash else ''
        m = f' marker-end="url(#a-{kind})"' if head else ''
        self.parts.append(f'<path d="{d}" fill="none" stroke="{st}" stroke-width="{sw}"{da}{m}/>')

    def label(self, x, y, s, size=10.5, kind='ext', anchor='middle', bg=True, italic=False):
        if bg:
            wpx = len(s) * size * 0.56 + 8
            x0 = x - wpx / 2 if anchor == 'middle' else (x - 4 if anchor == 'start' else x - wpx + 4)
            self.parts.append(f'<rect x="{x0}" y="{y - size}" width="{wpx}" height="{size + 5}" '
                              f'rx="3" fill="#ffffff" opacity="0.92"/>')
        self.text(x, y, s, size, kind=kind, anchor=anchor, italic=italic)

    def circle(self, cx, cy, r, kind='plain', fill=None):
        st, fl, _ = KINDS[kind]
        self.parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{fill or fl}" stroke="{st}" stroke-width="1.6"/>')

    def raw(self, s):
        self.parts.append(s)

    def render(self):
        defs = ['<defs>']
        for k, (st, _, _) in KINDS.items():
            defs.append(f'<marker id="a-{k}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
                        f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{st}"/></marker>')
            defs.append(f'<marker id="s-{k}" viewBox="0 0 10 10" refX="1" refY="5" markerWidth="7" markerHeight="7" '
                        f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{st}"/></marker>')
        defs.append('</defs>')
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" '
                f'width="100%" style="max-width:{self.w}px" role="img">' + ''.join(defs) + ''.join(self.parts) + '</svg>')


# ---------------------------------------------------------------------------------------------
# Chapter 1: the big picture
# ---------------------------------------------------------------------------------------------

# Layer colours for this guide (added to KINDS): collector, ingest, search+agent, digest,
# service, observability, storage.
KINDS.update({
    'col':  ('#0284c7', '#e0f2fe', '#0c4a6e'),
    'ing':  ('#059669', '#d1fae5', '#064e3b'),
    'srch': ('#4f46e5', '#e0e7ff', '#312e81'),
    'dig':  ('#db2777', '#fce7f3', '#831843'),
    'svc':  ('#ea580c', '#ffedd5', '#7c2d12'),
    'obs':  ('#a16207', '#fef9c3', '#713f12'),
    'db':   ('#475569', '#f1f5f9', '#0f172a'),
})


# --- charts ------------------------------------------------------------------------------------

def hbars(title, rows, unit='', scale=1.0, note=None, height=None, labelw=250, width=720):
    """Horizontal bars: rows = (label, value, kind, text)."""
    n = len(rows)
    f = Fig(width, height or (60 + n * 34 + (24 if note else 0)))
    f.text(20, 22, title, 12.5, 'bold', anchor='start')
    for i, (lab, v, k, txt) in enumerate(rows):
        y = 40 + i * 34
        f.text(labelw - 10, y + 17, lab, 11.5, anchor='end')
        f.rect(labelw, y, max(v * scale, 3), 24, k, r=4)
        f.text(labelw + 6 + max(v * scale, 3), y + 17, txt or f'{v:g} {unit}', 11.5, anchor='start',
               weight='bold', kind=k)
    if note:
        f.text(20, 40 + n * 34 + 14, note, 10.5, anchor='start', kind='ext', italic=True)
    return f.render()


def gbars(title, groups, series, vmax, note=None, width=760, height=300, fmt='{:.2f}', label_size=10.5):
    """Grouped vertical bars. groups = [(label, [v1, v2, ...])], series = [(name, kind)]."""
    f = Fig(width, height)
    f.text(20, 22, title, 12.5, 'bold', anchor='start')
    top, base = 52, height - 62
    left, right = 50, width - 20
    span = right - left
    gw = span / len(groups)
    bw = min(34, (gw - 16) / len(series))
    for t in (0.25, 0.5, 0.75, 1.0):
        y = base - (base - top) * t
        f.line(left, y, right, y, 'ext', head=False, sw=0.6, dash=True)
        f.text(left - 6, y + 4, fmt.format(vmax * t), 9.5, kind='ext', anchor='end')
    f.line(left, base, right, base, 'ext', head=False, sw=1)
    for gi, (glab, vals) in enumerate(groups):
        gx = left + gi * gw + (gw - bw * len(series)) / 2
        for si, v in enumerate(vals):
            if v is None:
                continue
            k = series[si][1]
            h = (base - top) * v / vmax
            x = gx + si * bw
            f.rect(x + 1, base - h, bw - 2, h, k, r=2)
            f.text(x + bw / 2, base - h - 4, fmt.format(v), 9, kind=k, weight='bold')
        for li, part in enumerate(glab.split('\n')):
            f.text(left + gi * gw + gw / 2, base + 15 + li * 13, part, label_size)
    lx = left
    for name, k in series:
        f.rect(lx, height - 22, 13, 11, k, r=2)
        f.text(lx + 18, height - 13, name, 10.5, anchor='start')
        lx += 30 + len(name) * 6.4
    if note:
        f.text(right, height - 13, note, 10, kind='ext', anchor='end', italic=True)
    return f.render()


# --- figure modules: f_*.py, one per chapter; every lower-case function there is a figure --------
import importlib  # noqa: E402
import sys as _sys  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

_sys.modules.setdefault('figs', _sys.modules[__name__])
FIGURES = {}
for _p in sorted(_Path(__file__).resolve().parent.glob('f_*.py')):
    _m = importlib.import_module(_p.stem)
    for _name, _fn in vars(_m).items():
        if callable(_fn) and getattr(_fn, '__module__', '') == _p.stem and _name.islower() \
                and not _name.startswith('_'):
            if _name in FIGURES:
                raise SystemExit(f'figure {_name!r} defined twice')
            FIGURES[_name] = _fn
