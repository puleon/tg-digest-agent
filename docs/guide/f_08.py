"""Chapter 8: embeddings, Qdrant and hybrid search."""
import math

from figs import Fig


def embedding_space() -> str:
    f = Fig(760, 330)
    f.text(20, 22, 'Эмбеддинги: тексты как точки, близость по смыслу — близость по направлению', 12.5, 'bold',
           anchor='start')
    f.rect(20, 40, 430, 280, 'file', r=10, fill='#fbfdff')
    groups = [
        ('коты и мемы', 'dig', (105, 78), [(80, 110), (115, 98), (135, 130)],
         [(55, 165, 'РЫБА ПИВО КОШКА'), (55, 179, 'котёнок шлёт имейл в УК')]),
        ('кино', 'col', (360, 78), [(330, 105), (365, 95), (385, 122)],
         [(318, 152, 'трейлер «Одиссеи»'), (318, 166, 'сборы «Человека-паука»')]),
        ('фантастика', 'ing', (300, 212), [(268, 235), (303, 226), (322, 250)],
         [(240, 276, 'новая антология Лавкрафта'), (240, 290, 'сериал «Полдень»')]),
    ]
    for name, k, (tx, ty), pts, labels in groups:
        f.text(tx, ty, name, 11, 'bold', kind=k)
        for x, y in pts:
            f.circle(x, y, 5, k, fill='#ffffff')
        for x, y, lab in labels:
            f.text(x, y, lab, 9, kind=k, anchor='start')
    # query
    f.line(178, 122, 141, 129, 'srch', dash=True, sw=1.3)
    f.circle(178, 120, 7, 'srch', fill='#4f46e5')
    f.text(190, 124, '«смешное про котов»', 10.5, 'bold', kind='srch', anchor='start')
    # right: cosine explanation
    f.text(480, 60, 'Косинусная близость', 12.5, 'bold', anchor='start')
    ox, oy = 520, 250
    a1, a2 = math.radians(62), math.radians(28)
    f.line(ox, oy, ox + 170 * math.cos(a1), oy - 170 * math.sin(a1), 'dig', sw=2)
    f.line(ox, oy, ox + 170 * math.cos(a2), oy - 170 * math.sin(a2), 'srch', sw=2)
    f.path(f'M{ox + 50 * math.cos(a2):.1f},{oy - 50 * math.sin(a2):.1f} A50,50 0 0,0 '
           f'{ox + 50 * math.cos(a1):.1f},{oy - 50 * math.sin(a1):.1f}', 'ext', head=False, sw=1.2)
    f.text(ox + 62, oy - 46, 'θ', 13, 'bold', kind='ext')
    f.text(ox + 92, oy - 160, 'пост', 10.5, kind='dig', anchor='start')
    f.text(ox + 162, oy - 82, 'запрос', 10.5, kind='srch', anchor='start')
    f.text(480, 290, 'cos θ = 1 — одно направление, 0 — не связаны.', 10.5, anchor='start')
    f.text(480, 307, 'Векторы нормированы, поэтому это просто', 10.5, anchor='start', kind='ext')
    f.text(480, 322, 'сумма произведений координат.', 10.5, anchor='start', kind='ext')
    f.text(30, 310, 'Иллюстрация в двух измерениях; у BGE-M3 их 1 024.', 9.5, anchor='start', kind='ext', italic=True)
    return f.render()


def dense_sparse() -> str:
    f = Fig(780, 230)
    f.text(20, 22, 'Один проход BGE-M3 — два представления текста «РЫБА ПИВО КОШКА …a cat…»', 12.5, 'bold',
           anchor='start')
    f.box(20, 45, 360, 150, 'Плотный (dense)', ['1 024 числа, почти все не нули:', '[0.021, −0.047, 0.013, … , 0.008]',
                                                '', 'кодирует смысл целиком:', 'похожие по смыслу тексты — рядом,',
                                                'даже без общих слов'], 'srch', tsize=12.5, lsize=10.5)
    f.box(400, 45, 360, 150, 'Разреженный (sparse)', ['вес только у слов текста:', '{«КОШ»: 0.31, «ПИВО»: 0.28, «cat»: 0.22, …}',
                                                       '', 'похоже на поиск по словам,', 'но веса выучены моделью:',
                                                       'важные слова весят больше'], 'col', tsize=12.5, lsize=10.5)
    f.text(20, 220, 'Числа иллюстративные. Плотный хорошо ловит перефразировки, разреженный — имена, названия и редкие '
           'слова. Гибрид берёт лучшее из обоих.', 10.5, anchor='start', kind='ext')
    return f.render()


def bi_cross() -> str:
    f = Fig(780, 250)
    f.text(20, 22, 'Две архитектуры: быстро и заранее против медленно и точно', 12.5, 'bold', anchor='start')
    # bi-encoder
    f.rect(20, 40, 360, 190, 'srch', r=12, fill='#eef2ff', dash=True)
    f.text(200, 62, 'Би-энкодер (BGE-M3) — поиск', 12, 'bold', kind='srch')
    f.box(40, 80, 110, 44, 'запрос', [], 'file', tsize=11)
    f.box(40, 150, 110, 44, 'пост', ['заранее, ночью'], 'file', tsize=11, lsize=9.5)
    f.box(180, 80, 80, 44, 'модель', [], 'srch', tsize=11)
    f.box(180, 150, 80, 44, 'модель', [], 'srch', tsize=11)
    f.line(150, 102, 178, 102, 'ext')
    f.line(150, 172, 178, 172, 'ext')
    f.box(285, 115, 80, 44, 'cos', ['вектор·вектор'], 'srch', tsize=11, lsize=9)
    f.line(260, 102, 283, 128, 'ext')
    f.line(260, 172, 283, 148, 'ext')
    f.text(200, 218, 'миллионы сравнений в секунду', 10.5, kind='srch', italic=True)
    # cross
    f.rect(400, 40, 360, 190, 'dig', r=12, fill='#fdf2f8', dash=True)
    f.text(580, 62, 'Кросс-энкодер (реранкер) — перепроверка', 12, 'bold', kind='dig')
    f.box(420, 110, 120, 60, 'запрос + пост', ['одним текстом'], 'file', tsize=11, lsize=9.5)
    f.box(570, 110, 80, 60, 'модель', ['читает', 'вместе'], 'dig', tsize=11, lsize=9.5)
    f.box(680, 115, 66, 50, '0.93', ['оценка'], 'dig', tsize=12, lsize=9)
    f.line(540, 140, 568, 140, 'ext')
    f.line(650, 140, 678, 140, 'ext')
    f.text(580, 218, 'один прогон на пару: ≈ 0,2 с — только для топ-30…50', 10.5, kind='dig', italic=True)
    return f.render()


def hnsw() -> str:
    f = Fig(780, 260)
    f.text(20, 22, 'HNSW: как найти ближайшие точки, не сравнивая со всеми', 12.5, 'bold', anchor='start')
    layers = [(60, [(120, 0), (520, 0)], 'верхний слой: мало точек, длинные связи'),
              (130, [(120, 0), (260, 0), (400, 0), (520, 0), (640, 0)], 'средний'),
              (200, [(80, 0), (150, 0), (200, 0), (260, 0), (330, 0), (400, 0), (470, 0), (520, 0), (580, 0),
                     (640, 0), (700, 0)], 'нижний слой: все точки, короткие связи')]
    for y, pts, lab in layers:
        f.rect(40, y - 26, 700, 52, 'file', r=8, fill='#f8fafc')
        f.text(48, y - 12, lab, 9.5, kind='ext', anchor='start', italic=True)
        xs = [p[0] for p in pts]
        for a, b in zip(xs, xs[1:]):
            f.line(a, y + 6, b, y + 6, 'ext', head=False, sw=1)
        for x in xs:
            f.circle(x, y + 6, 6, 'srch', fill='#ffffff')
    path = [(120, 66), (520, 66), (520, 136), (640, 136), (640, 206), (580, 206)]
    for (x1, y1), (x2, y2) in zip(path, path[1:]):
        f.line(x1, y1, x2, y2, 'warn', sw=2.2)
    f.circle(580, 206, 7, 'warn', fill='#dc2626')
    f.text(590, 240, 'найдено: ближайший к запросу', 10.5, kind='warn', anchor='start')
    f.text(40, 245, 'Спуск по слоям: на каждом — жадно к точке, ближайшей к запросу. Qdrant: m = 16, ef_construct = 100.',
           10.5, anchor='start', kind='ext')
    return f.render()


def qdrant_point() -> str:
    f = Fig(780, 270)
    f.text(20, 22, 'Точка в коллекции posts: один пост — восемь векторов и паспорт', 12.5, 'bold', anchor='start')
    f.box(20, 45, 120, 200, 'id', ['8648', '', '(номер поста)'], 'srch', tsize=13, lsize=11)
    f.rect(160, 45, 300, 200, 'srch', r=10)
    f.text(310, 66, 'Именованные векторы', 12, 'bold', kind='srch')
    vs = [('text', 'sparse_text'), ('text_ocr', 'sparse_text_ocr'), ('text_ocr_caption', 'sparse_text_ocr_caption'),
          ('full', 'sparse_full')]
    for i, (d, s) in enumerate(vs):
        y = 82 + i * 38
        f.rect(172, y, 128, 28, 'srch', r=4, fill='#ffffff')
        f.text(236, y + 18, d, 10, kind='srch', mono=True)
        f.rect(310, y, 140, 28, 'col', r=4, fill='#ffffff')
        f.text(380, y + 18, s, 9, kind='col', mono=True)
    f.text(236, 240, 'dense 1 024, cosine', 9.5, kind='srch', italic=True)
    f.text(380, 240, 'sparse, точное', 9.5, kind='col', italic=True)
    f.rect(480, 45, 280, 200, 'file', r=10, fill='#f8fafc')
    f.text(620, 66, 'Payload (паспорт)', 12, 'bold', kind='file')
    pl = ['topic: "humor"   channel: "am_devs"', 'posted_at: 1777892400', 'is_ad: false   is_spoiler: false',
          'quality: 4.0   injection_flag: false', 'cluster_id: null', 'is_representative: true',
          'sources: {ocr, caption}', 'url: t.me/am_devs/49719', 'content_hash, meta_hash']
    for i, ln in enumerate(pl):
        f.text(492, 90 + i * 17, ln, 9.5, anchor='start', mono=True, color='#334155')
    return f.render()


def hybrid_pipeline() -> str:
    f = Fig(780, 300)
    f.text(20, 22, 'Поиск в агенте: от вопроса до двадцати кандидатов', 12.5, 'bold', anchor='start')
    f.box(20, 120, 110, 64, 'Вопрос', ['+ 1–3', 'формулировки'], 'you', tsize=12, lsize=10)
    for i, q in enumerate(['«смешные коты»', '«мемы про котов»', '«коты юмор»']):
        y = 50 + i * 74
        f.box(160, y, 130, 54, q, ['BGE-M3: dense', '+ sparse'], 'srch', tsize=10.5, lsize=9.5)
        f.line(130, 152, 158, y + 27, 'ext', sw=1.1)
        f.box(310, y, 170, 54, 'Qdrant', ['dense 100 + sparse 100', '→ RRF → фильтры'], 'srch', tsize=11, lsize=9.5)
        f.line(290, y + 27, 308, y + 27, 'ext', sw=1.1)
        f.box(500, y, 120, 54, 'реранкер', ['топ-30 → 20'], 'dig', tsize=11, lsize=9.5)
        f.line(480, y + 27, 498, y + 27, 'ext', sw=1.1)
        f.line(620, y + 27, 648, 152, 'ext', sw=1.1)
    f.box(650, 110, 110, 84, 'RRF', ['слить списки', 'формулировок', '→ 20 лучших'], 'srch', tsize=12, lsize=9.5)
    f.text(20, 272, 'Фильтры всегда: без рекламы, только представители кластеров; по запросу — тема, период, «без '
           'спойлеров», каналы.', 10.5, anchor='start', kind='ext')
    f.text(20, 290, 'Пост, найденный двумя формулировками, поднимается выше найденного одной.', 10.5, anchor='start',
           kind='ext')
    return f.render()


def variants() -> str:
    f = Fig(760, 210)
    f.text(20, 22, 'Четыре варианта текста поста — вложенные друг в друга', 12.5, 'bold', anchor='start')
    boxes = [(20, 40, 720, 160, 'full', '+ пересказы ссылок', 'srch'),
             (40, 70, 560, 120, 'text_ocr_caption', '+ подписи картинок', 'srch'),
             (60, 100, 400, 80, 'text_ocr', '+ распознанный текст', 'srch'),
             (80, 128, 220, 44, 'text', 'текст поста', 'srch')]
    for x, y, w, h, name, add, k in boxes:
        f.rect(x, y, w, h, k, r=10, fill='#ffffff' if name == 'text' else None, opacity=1)
        f.text(x + w - 10, y + 20, name, 11, 'bold', kind=k, anchor='end', mono=True)
        f.text(x + w - 10, y + 36, add, 10, kind=k, anchor='end', italic=True)
    return f.render()
