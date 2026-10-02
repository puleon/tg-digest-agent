"""Chapter 6: the ingest agent and LangGraph."""
from figs import Fig, gbars, hbars


def langgraph_basics() -> str:
    f = Fig(780, 290)
    f.text(20, 22, 'LangGraph: состояние, узлы и рёбра', 12.5, 'bold', anchor='start')
    f.rect(20, 40, 210, 230, 'file', r=10, fill='#f8fafc')
    f.text(125, 62, 'Состояние (State)', 12.5, 'bold', kind='file')
    for i, ln in enumerate(['словарь, который', 'передаётся от узла', 'к узлу:', '', 'post_id, text,',
                            'needs_vision,', 'vision, labels,', 'degraded, usage']):
        f.text(125, 86 + i * 18, ln, 10.5, kind='file', mono=i >= 4)
    f.box(270, 50, 180, 80, 'Узел (node)', ['async-функция:', 'получает состояние,', 'возвращает изменения'], 'ing',
          tsize=12, lsize=10)
    f.box(270, 160, 180, 90, 'Ребро (edge)', ['обычное: «после A — B»', 'условное: функция', 'читает состояние и', 'называет следующий узел'],
          'ing', tsize=12, lsize=10)
    f.box(490, 50, 270, 200, 'compile() → ainvoke(state)', ['граф проверяется и собирается', 'в исполнимый объект;',
                                                           'ainvoke прогоняет его от START', 'до END, сливая изменения',
                                                           'каждого узла в состояние', '', 'один граф — тысячи запусков:',
                                                           'по одному на пост'], 'srch', tsize=12, lsize=10.5)
    f.line(230, 150, 268, 90, 'ext', sw=1.2)
    f.line(450, 90, 488, 110, 'ext', sw=1.2)
    f.line(450, 205, 488, 190, 'ext', sw=1.2)
    return f.render()


def ingest_graph() -> str:
    f = Fig(780, 260)
    f.text(20, 22, 'Граф ingest-агента (src/tgdigest/ingest/graph.py)', 12.5, 'bold', anchor='start')
    f.circle(40, 120, 16, 'ext')
    f.text(40, 124, 'START', 8, 'bold', kind='ext')
    f.box(80, 90, 120, 60, 'triage', ['нужно ли зрение?'], 'ing', tsize=12.5, lsize=10)
    f.box(260, 40, 170, 70, 'describe_images', ['Gemma 4 на каждую', 'картинку поста'], 'ing', tsize=12, lsize=10)
    f.box(470, 90, 130, 60, 'classify', ['Qwen3.6, few-shot'], 'ing', tsize=12.5, lsize=10)
    f.box(630, 90, 130, 60, 'detect_injection', ['регулярки'], 'ing', tsize=11.5, lsize=10)
    f.circle(700, 200, 16, 'ext')
    f.text(700, 204, 'END', 8, 'bold', kind='ext')
    f.line(56, 120, 78, 120, 'ext')
    f.path('M200,110 C230,90 240,75 258,75', 'ing')
    f.label(232, 70, 'да', kind='ing', size=10)
    f.path('M200,130 C300,170 400,150 468,125', 'ing')
    f.label(330, 172, 'нет: текст длинный, тема не визуальная', kind='ing', size=10)
    f.path('M430,75 C460,75 470,95 485,98', 'ing')
    f.line(600, 120, 628, 120, 'ing')
    f.line(695, 150, 698, 182, 'ext')
    f.text(20, 230, 'Условное ребро после triage выбирает ветку; остальные рёбра безусловные. Ошибка любого шага не роняет '
           'граф,', 10.5, anchor='start', kind='ext')
    f.text(20, 247, 'а дописывает пометку в state["degraded"] — и пост сохраняется с тем, что удалось получить.', 10.5,
           anchor='start', kind='ext')
    return f.render()


def vision_ladder() -> str:
    f = Fig(780, 200)
    f.text(20, 22, 'Одна картинка: попытки по нарастающей и честная деградация', 12.5, 'bold', anchor='start')
    steps = [('Полный промпт', 'vlm_describe.v1', '300 токенов', 'ing'),
             ('Обрыв → ещё раз', 'тот же промпт', '600 токенов', 'ing'),
             ('Простой промпт', 'vlm_describe_simple.v1', '200 токенов', 'sec'),
             ('Деградация', 'vision:failed', 'только метаданные', 'warn')]
    for i, (t, a, b, k) in enumerate(steps):
        x = 20 + i * 192
        f.box(x, 45, 172, 80, t, [a, b], k, tsize=12, lsize=10)
        if i:
            f.line(x - 18, 85, x - 2, 85, 'ext', sw=1.6)
            f.label(x - 10, 74, 'не вышло', kind='ext', size=8.5)
    f.text(20, 155, 'Каждая попытка ограничена таймаутом. Нечитаемая картинка (is_readable = false, текста нет) — тоже '
           'пометка: vision:unreadable.', 10.5, anchor='start', kind='ext')
    f.text(20, 175, 'Прогон по юмору: 6 740 постов → 24 vision:failed, 4 vision:unreadable, 0 потерянных постов.', 10.5,
           anchor='start')
    return f.render()


def links_outcomes() -> str:
    return hbars('Ссылки из постов кино за три месяца: что с ними стало (1 410 постов с текстом)',
                 [('в посте нет внешней ссылки', 1215, 'ext', '1 215 постов'),
                  ('видео (YouTube и т.п.) — не качаем', 97, 'sec', '97 ссылок'),
                  ('скачано и пересказано', 83, 'ing', '83'),
                  ('страница не по делу', 7, 'sec', '7'),
                  ('таймаут', 5, 'warn', '5'),
                  ('пустая страница', 4, 'warn', '4'),
                  ('пейволл или заглушка', 3, 'warn', '3'),
                  ('403 · слишком большая · обрыв', 3, 'warn', '1 · 1 · 1')],
                 scale=0.3, labelw=290, width=780, note='Каждая неудача записана с причиной: выпуск может сказать, почему у ссылки '
                                               'нет пересказа.')


def grounding() -> str:
    f = Fig(780, 240)
    f.text(20, 22, 'Привязка фильма к Wikidata: упоминание → кандидаты → выбор', 12.5, 'bold', anchor='start')
    f.box(20, 50, 170, 90, 'Пост, 2026 год', ['«…новая „Мумия“', 'Ли Кронина…»'], 'file', tsize=12, lsize=10.5)
    f.box(220, 50, 160, 90, 'extract_entities', ['Qwen3.6 → JSON:', 'films: [{title:', '«Мумия», year: null}]'], 'ing',
          tsize=11.5, lsize=10)
    f.box(410, 50, 150, 90, 'Wikidata', ['поиск по названию', '→ 10 кандидатов', 'только фильмы/сериалы'], 'ext',
          tsize=12, lsize=10)
    f.line(190, 95, 218, 95, 'ext')
    f.line(380, 95, 408, 95, 'ext')
    cands = [('Мумия (1932)', '−0,15'), ('Мумия (1999)', '−0,15'), ('Мумия (2017)', '−0,15'), ('Мумия (2026)', '+0,25 ✓')]
    for i, (t, sc) in enumerate(cands):
        y = 40 + i * 28
        k = 'ing' if '✓' in sc else 'ext'
        f.rect(590, y, 170, 24, k, r=4)
        f.text(600, y + 16, t, 10.5, anchor='start', kind=k)
        f.text(752, y + 16, sc, 10.5, 'bold', anchor='end', kind=k)
    f.line(560, 95, 588, 95, 'ext')
    f.text(20, 178, 'Счёт кандидата = похожесть названия + поправка за год. Если год назван в посте, он решает (±0,5); если '
           'нет —', 10.5, anchor='start')
    f.text(20, 196, 'берётся год поста как подсказка (±0,25): каналы указывают год для старых фильмов, а без года обычно '
           'пишут о новинках.', 10.5, anchor='start')
    f.text(20, 220, 'Ответы кэшируются в entity_cache: повторное упоминание «Одиссеи» (79 раз) не идёт в сеть.', 10.5,
           anchor='start', kind='ext', italic=True)
    return f.render()


def distill_chart() -> str:
    return gbars('Дистилляция: ученик (логрег на эмбеддингах) против учителя (LLM), 875 тестовых постов',
                 [('тема', [0.864, 0.799, 0.338]), ('реклама', [0.850, 0.398, 0.906]), ('качество 1–5', [0.544, 0.333, 0.576])],
                 [('точность', 'ing'), ('каппа Коэна', 'srch'), ('доля «большинства»', 'ext')], 1.0,
                 height=280, note='каппа учитывает случайные совпадения; точность — нет')
