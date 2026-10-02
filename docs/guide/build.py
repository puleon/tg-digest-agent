#!/usr/bin/env python3
"""Build the architecture guide: chapters + SVG figures -> guide.html -> PDF.

    python3 docs/guide/build.py            # html + pdf
    python3 docs/guide/build.py --html     # html only

Standard library only (the figures are hand-written SVG, see figs.py). Chapters are HTML
fragments in chapters/NN-name.html; a line {{fig:name}} or {{fig:name|caption}} becomes a
figure. The table of contents is built from the chapters' <h1> and <h2>. The PDF is printed
by headless Chrome, which needs no extra tooling on a Mac. Same pipeline as the companion
guide in the nanoclaw-assistant repository, so the two read as one series.
"""
from __future__ import annotations

import html
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import figs  # noqa: E402

CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
OUT_HTML = HERE / 'guide.html'
OUT_PDF = HERE / 'tg-digest-architecture-guide.pdf'

FIG_RE = re.compile(r'\{\{fig:([a-z_0-9]+)(?:\|(.+?))?\}\}', re.S)
H_RE = re.compile(r'<h([12])>(.+?)</h\1>')


def strip_tags(s: str) -> str:
    return re.sub(r'<[^>]+>', '', s)


def render_chapter(n: int, src: str, toc: list) -> str:
    fig_no = 0

    def fig(m):
        nonlocal fig_no
        name, cap = m.group(1), m.group(2)
        if name not in figs.FIGURES:
            raise SystemExit(f'unknown figure {name!r}')
        fig_no += 1
        caption = f'<figcaption><b>Рис. {n}.{fig_no}.</b> {cap}</figcaption>' if cap else ''
        return f'<figure class="fig">{figs.FIGURES[name]()}{caption}</figure>'

    src = FIG_RE.sub(fig, src)
    sec = 0

    def head(m):
        nonlocal sec
        level, text = m.group(1), m.group(2)
        if level == '1':
            anchor = f'ch{n}'
            toc.append((1, n, strip_tags(text), anchor))
            return f'<h1 id="{anchor}"><span class="chnum">Глава {n}</span>{text}</h1>'
        sec += 1
        anchor = f'ch{n}-{sec}'
        toc.append((2, f'{n}.{sec}', strip_tags(text), anchor))
        return f'<h2 id="{anchor}"><span class="secnum">{n}.{sec}</span> {text}</h2>'

    src = H_RE.sub(head, src)
    return f'<section class="chapter">{src}</section>'


def build() -> Path:
    toc: list = []
    chapters = sorted((HERE / 'chapters').glob('[0-9][0-9]-*.html'))
    body = []
    for i, p in enumerate(chapters, 1):
        body.append(render_chapter(i, p.read_text(encoding='utf-8'), toc))

    toc_html = ['<nav class="toc"><h1 class="toc-title">Содержание</h1><ol>']
    for level, num, text, anchor in toc:
        cls = 't1' if level == 1 else 't2'
        toc_html.append(f'<li class="{cls}"><a href="#{anchor}"><span>{num}</span>'
                        f'{html.escape(text)}</a></li>')
    toc_html.append('</ol></nav>')

    cover = (HERE / 'cover.html').read_text(encoding='utf-8')
    cover = FIG_RE.sub(lambda m: f'<figure class="fig">{figs.FIGURES[m.group(1)]()}</figure>', cover)
    css = (HERE / 'style.css').read_text(encoding='utf-8')
    doc = f'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<title>Как устроен Tematic Telegram Index</title>
<style>{css}</style></head>
<body>
{cover}
{''.join(toc_html)}
{''.join(body)}
</body></html>'''
    OUT_HTML.write_text(doc, encoding='utf-8')
    return OUT_HTML


def pdf(src: Path) -> Path:
    cmd = [CHROME, '--headless=new', '--disable-gpu', '--no-pdf-header-footer',
           '--generate-pdf-document-outline', '--virtual-time-budget=10000',
           f'--print-to-pdf={OUT_PDF}', src.as_uri()]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return OUT_PDF


if __name__ == '__main__':
    out = build()
    print('html:', out)
    if '--html' not in sys.argv:
        print('pdf: ', pdf(out))
