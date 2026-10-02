#!/usr/bin/env python3
"""Render a few figures (or pages of the built PDF) to PNG for a visual check.

    python3 docs/guide/preview.py fig big_map tokens ...   -> OUT/fig-<name>.png
    python3 docs/guide/preview.py pages 1-6 12            -> OUT/page-NNN.png
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
OUT = Path(sys.argv[2] if False else '/tmp/guide-preview')
OUT = Path(__import__('os').environ.get('GUIDE_PREVIEW', '/tmp/guide-preview'))
OUT.mkdir(parents=True, exist_ok=True)


def figs_to_png(names: list[str]) -> None:
    import fitz  # PyMuPDF
    import figs
    css = (HERE / 'style.css').read_text(encoding='utf-8')
    for name in names:
        html = (f'<!doctype html><html><head><meta charset="utf-8"><style>{css}'
                '@page{size:A4;margin:12mm}</style></head><body>'
                f'<figure class="fig">{figs.FIGURES[name]()}</figure></body></html>')
        src = OUT / f'fig-{name}.html'
        src.write_text(html, encoding='utf-8')
        pdf = OUT / f'fig-{name}.pdf'
        subprocess.run(['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '--headless=new',
                        '--disable-gpu', '--no-pdf-header-footer', f'--print-to-pdf={pdf}', src.as_uri()],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        doc = fitz.open(pdf)
        page = doc[0]
        pix = page.get_pixmap(matrix=fitz.Matrix(1.6, 1.6))
        png = OUT / f'fig-{name}.png'
        pix.save(png)
        print(png)


def pages_to_png(spec: list[str]) -> None:
    import fitz
    doc = fitz.open(HERE / 'tg-digest-architecture-guide.pdf')
    want: list[int] = []
    for s in spec:
        if '-' in s:
            a, b = s.split('-')
            want += list(range(int(a), int(b) + 1))
        else:
            want.append(int(s))
    print('pages total:', doc.page_count)
    for n in want:
        if 1 <= n <= doc.page_count:
            pix = doc[n - 1].get_pixmap(matrix=fitz.Matrix(1.25, 1.25))
            png = OUT / f'page-{n:03d}.png'
            pix.save(png)
            print(png)


def sheet(names: list[str], out: str) -> None:
    """Render figures, crop each to its content and stack them into one PNG."""
    import fitz
    figs_to_png(names)
    imgs = []
    for n in names:
        page = fitz.open(OUT / f'fig-{n}.pdf')[0]
        bbox = None
        for b in page.get_drawings():
            bbox = b['rect'] if bbox is None else bbox | b['rect']
        for t in page.get_text('blocks'):
            r = fitz.Rect(t[:4])
            bbox = r if bbox is None else bbox | r
        imgs.append(page.get_pixmap(matrix=fitz.Matrix(1.4, 1.4), clip=(bbox + (-6, -6, 6, 6)) & page.rect))
    w = max(p.width for p in imgs)
    h = sum(p.height + 10 for p in imgs)
    doc = fitz.open()
    pg = doc.new_page(width=w, height=h)
    y = 0
    for p in imgs:
        pg.insert_image(fitz.Rect(0, y, p.width, y + p.height), pixmap=p)
        y += p.height + 10
    pg.get_pixmap().save(OUT / out)
    print(OUT / out)


if __name__ == '__main__':
    if sys.argv[1] == 'fig':
        figs_to_png(sys.argv[2:])
    elif sys.argv[1] == 'sheet':
        sheet(sys.argv[3:], sys.argv[2])
    else:
        pages_to_png(sys.argv[2:])
