"""Chapter 3: language models under the hood."""
from figs import Fig, KINDS, gbars, hbars


def tokens_split() -> str:
    f = Fig(740, 190)
    f.text(20, 22, 'Как токенизатор Qwen3.6 режет текст (живой вызов /tokenize на сервере)', 12.5, 'bold',
           anchor='start')
    rows = [('«что-нибудь смешное про котов»', ['что', '-', 'нибудь', '␣смеш', 'ное', '␣про', '␣к', 'отов'], '8 токенов'),
            ('«funny cats»', ['fun', 'ny', '␣cats'], '3 токена'),
            ('«Кратос»', ['К', 'рат', 'ос'], '3 токена')]
    for i, (lab, pieces, n) in enumerate(rows):
        y = 44 + i * 46
        f.text(20, y + 21, lab, 11.5, anchor='start')
        x = 250
        for j, pc in enumerate(pieces):
            w = 16 + len(pc) * 8.6
            k = 'llm' if j % 2 == 0 else 'srch'
            f.rect(x, y + 4, w, 26, k, r=4)
            f.text(x + w / 2, y + 22, pc, 11.5, kind=k, mono=True)
            x += w + 4
        f.text(720, y + 22, n, 11.5, 'bold', anchor='end', kind='llm')
    f.text(20, 182, '␣ — пробел, который токенизатор приклеивает к началу слова. Русский текст дороже английского: '
           'меньше готовых кусков.', 10, kind='ext', anchor='start', italic=True)
    return f.render()


def next_token() -> str:
    f = Fig(740, 230)
    f.text(20, 22, 'Один шаг генерации: модель выдаёт вероятность каждого из ~150 тыс. токенов словаря', 12.5,
           'bold', anchor='start')
    f.box(20, 60, 230, 110, 'Контекст', ['…Пользователь: что-нибудь', 'смешное про котов', 'Ассистент: Вот подборка',
                                         'смешных постов про'], 'file', tsize=12, lsize=10.5)
    f.box(280, 80, 110, 70, 'Модель', ['один прогон', 'по всем слоям'], 'llm', tsize=12, lsize=10)
    f.line(250, 115, 278, 115, 'ext')
    f.line(390, 115, 418, 115, 'ext')
    cands = [('␣котов', 0.62), ('␣кошек', 0.21), ('␣кот', 0.06), ('␣животных', 0.03), ('…', 0.08)]
    for i, (tok, p) in enumerate(cands):
        y = 50 + i * 30
        f.text(510, y + 16, tok, 11.5, anchor='end', mono=True)
        f.rect(520, y, p * 300, 22, 'llm' if i == 0 else 'ext', r=3)
        f.text(526 + p * 300, y + 16, f'{p:.2f}', 10.5, anchor='start', kind='llm' if i == 0 else 'ext')
    f.text(20, 214, 'Числа иллюстративные. При temperature = 0 берётся самый вероятный токен — у нас так почти везде: '
           'нужны воспроизводимые ответы.', 10, kind='ext', anchor='start', italic=True)
    return f.render()


def prefill_decode() -> str:
    f = Fig(760, 250)
    f.text(20, 22, 'Два режима работы модели (Qwen3.6, 16 потоков, замер D1)', 12.5, 'bold', anchor='start')
    # prefill block
    f.rect(20, 50, 300, 70, 'llm', r=8)
    f.text(170, 76, 'Чтение промпта (prefill)', 13, 'bold', kind='llm')
    f.text(170, 96, '3 900 токенов сразу пачкой → 15 с', 11, kind='llm')
    f.text(170, 112, '≈ 260 токенов/с — упор в вычисления', 10.5, kind='llm', italic=True)
    # decode tokens
    x = 340
    for i in range(13):
        f.rect(x + i * 29, 50, 25, 70, 'srch', r=4)
    f.text(340 + 13 * 29 / 2, 140, 'Генерация (decode): по токену, каждый заново читает активные веса', 11,
           kind='srch')
    f.text(340 + 13 * 29 / 2, 158, '200 токенов → 10,5 с · ≈ 19 токенов/с — упор в скорость памяти', 10.5, kind='srch',
           italic=True)
    f.line(20, 186, 740, 186, 'ext', sw=1, head=False)
    f.text(20, 206, 'Для агента это значит: длинный промпт относительно дёшев, длинный ответ — дорог.', 11.5, 'bold',
           anchor='start')
    f.text(20, 226, 'Поэтому оценка релевантности просит «не больше 5 слов на пост», а редактор дайджеста — одну строку.',
           11, anchor='start', kind='ext')
    return f.render()


def moe() -> str:
    f = Fig(740, 270)
    f.text(20, 22, 'Смесь экспертов (MoE): на каждый токен работает лишь малая часть сети', 12.5, 'bold',
           anchor='start')
    f.box(20, 110, 110, 56, 'токен', ['«котов»'], 'file', tsize=12, lsize=11)
    f.box(160, 100, 120, 76, 'Маршрутизатор', ['выбирает', 'нужных экспертов'], 'llm', tsize=12, lsize=10)
    f.line(130, 138, 158, 138, 'ext')
    active = {1, 5, 9}
    pos = {i: (330 + (i % 6) * 66, 60 + (i // 6) * 110) for i in range(12)}
    for i in active:
        x, y = pos[i]
        f.line(280, 138, x, y + 30, 'llm', sw=1.3)
    for i in range(12):
        x, y = pos[i]
        k = 'llm' if i in active else 'ext'
        f.rect(x, y, 56, 60, k, r=6, fill=None if i in active else '#f8fafc')
        f.text(x + 28, y + 35, f'E{i + 1}', 11.5, 'bold' if i in active else 'normal', kind=k)
    f.text(20, 252, 'На рисунке работают 3 эксперта из 12. У Qwen3.6-35B-A3B на токен работает ≈ 3 млрд параметров из 35 млрд.',
           11, anchor='start', kind='llm')
    return f.render()


def router() -> str:
    f = Fig(780, 330)
    f.text(20, 22, 'llama-server в режиме роутера: один адрес, модели загружаются по требованию', 12.5, 'bold',
           anchor='start')
    clients = [('Ingest, оценки', 'fast / vlm'), ('API: агент, дайджест', 'fast'), ('Судья D16', 'heavy'),
               ('Ассистент (соседний проект)', 'отдельный пресет')]
    for i, (t, sub) in enumerate(clients):
        f.box(20, 45 + i * 66, 190, 54, t, [sub], 'svc' if i < 3 else 'ext', tsize=11.5, lsize=10)
        f.line(210, 72 + i * 66, 268, 160, 'ext', sw=1.2)
    f.rect(270, 40, 240, 270, 'llm', r=12, fill='#faf5ff')
    f.text(390, 64, 'llm-server :8080', 13, 'bold', kind='llm')
    f.text(390, 82, 'POST /v1/chat/completions', 10.5, kind='llm', mono=True)
    f.text(390, 98, '{"model": "qwen3.6-35b-a3b", …}', 10, kind='llm', mono=True)
    lines = ['читает models.ini:', 'пресет = файл модели + флаги', 'держит ≤ 2 моделей (--models-max 2)',
             'лишнюю выгружает', 'спит после 30 мин простоя', 'у каждой модели свой процесс,', 'свои слоты и кэш']
    for i, ln in enumerate(lines):
        f.text(290, 130 + i * 20, ln, 10.5, kind='llm', anchor='start')
    models = [('qwen3.6-35b-a3b', 'текст · 21 ГБ · 2 слота', 'llm'),
              ('gemma-4-26b-a4b', 'картинки · 18 ГБ · 4 слота', 'llm'),
              ('gpt-oss-120b', 'судья · 60 ГБ · 1 слот', 'llm'),
              ('qwen3.6…-assistant', 'ассистент · не спит', 'ext')]
    for i, (name, sub, k) in enumerate(models):
        y = 45 + i * 66
        f.box(560, y, 200, 54, name, [sub], k, tsize=11, lsize=10)
        f.line(510, 160, 558, y + 27, 'llm' if k == 'llm' else 'ext', sw=1.2, dash=True)
    return f.render()


def vision_tokens() -> str:
    f = Fig(760, 220)
    f.text(20, 22, 'Как модель «видит» картинку', 12.5, 'bold', anchor='start')
    f.box(20, 50, 120, 120, 'картинка', ['800×600', 'base64 в', 'сообщении'], 'file', tsize=12, lsize=10)
    f.box(170, 70, 140, 80, 'mmproj', ['визуальный', 'энкодер (ViT)'], 'llm', tsize=12, lsize=10)
    f.line(140, 110, 168, 110, 'ext')
    for i in range(10):
        f.rect(340 + i * 20, 92, 16, 36, 'llm', r=3)
    f.text(440, 150, 'токены изображения', 10.5, kind='llm')
    f.line(310, 110, 336, 110, 'ext')
    f.text(440, 80, 'Gemma 4: ≈ 260 · Qwen3.6: ≥ 1 024', 10.5, kind='llm')
    f.box(570, 60, 170, 100, 'языковая модель', ['читает их как', 'обычный промпт', 'и пишет JSON'], 'llm',
          tsize=12, lsize=10)
    f.line(545, 110, 568, 110, 'ext')
    f.text(20, 205, 'Больше токенов — мельче детали (кириллица на меме), но дольше чтение: 28 с против 10 с на картинку.',
           10.5, kind='ext', anchor='start', italic=True)
    return f.render()


def ocr_compare() -> str:
    f = Fig(760, 220)
    f.text(20, 22, 'Выбор модели зрения (D4): точность против скорости', 12.5, 'bold', anchor='start')
    # left: CER
    f.text(20, 52, 'Ошибка OCR (CER, меньше — лучше), 50 картинок', 11, 'bold', anchor='start')
    for i, (lab, v, k) in enumerate([('Qwen3.6', 0.000, 'srch'), ('Gemma 4', 0.111, 'dig')]):
        y = 66 + i * 34
        f.text(100, y + 17, lab, 11.5, anchor='end')
        f.rect(110, y, max(v * 1800, 3), 24, k, r=3)
        f.text(116 + max(v * 1800, 3), y + 17, f'{v:.3f}', 11, 'bold', kind=k, anchor='start')
    # right: throughput
    f.text(400, 52, 'Картинок в минуту без кэша (больше — лучше)', 11, 'bold', anchor='start')
    for i, (lab, v, k) in enumerate([('Qwen3.6', 2.6, 'srch'), ('Gemma 4, 4 слота', 8.5, 'dig')]):
        y = 66 + i * 34
        f.text(530, y + 17, lab, 11.5, anchor='end')
        f.rect(540, y, v * 18, 24, k, r=3)
        f.text(546 + v * 18, y + 17, f'{v:g}', 11, 'bold', kind=k, anchor='start')
    f.text(20, 160, 'Qwen читает идеально, но в 3,3 раза медленнее: весь корпус занял бы ≈ 100 часов против ≈ 30.', 11,
           anchor='start')
    f.text(20, 180, 'Решение: картинки описывает Gemma 4, текст классифицирует Qwen3.6; ошибки Gemma — мелкий английский',
           11, anchor='start', kind='ext')
    f.text(20, 197, 'на скриншотах и крошечные подписи, русский текст мемов она читает дословно.', 11, anchor='start',
           kind='ext')
    return f.render()


def constrained() -> str:
    f = Fig(780, 300)
    f.text(20, 22, 'Ответ строго по схеме: грамматика запрещает модели «неправильные» токены', 12.5, 'bold',
           anchor='start')
    f.box(20, 45, 200, 112, 'Pydantic-модель', ['class PostLabels:', '  topic: Literal[…]', '  is_ad: bool',
                                                 '  quality: int 1..5'], 'srch', tsize=12, lsize=10, mono_lines=True)
    f.box(250, 45, 150, 112, 'JSON Schema', ['model_json_', 'schema()', '→ в запрос', 'response_format'], 'srch',
          tsize=12, lsize=10)
    f.box(430, 45, 150, 112, 'Грамматика', ['llama-server', 'переводит схему', 'в GBNF'], 'llm', tsize=12, lsize=10)
    f.line(220, 101, 248, 101, 'ext')
    f.line(400, 101, 428, 101, 'ext')
    f.box(610, 45, 150, 112, 'Генерация', ['на каждом шаге', 'токены вне', 'грамматики', 'получают p = 0'], 'llm',
          tsize=12, lsize=10)
    f.line(580, 101, 608, 101, 'ext')
    # masking example
    f.text(20, 190, 'Пример шага: модель уже написала  {"topic": "', 11, anchor='start', mono=True)
    toks = [('humor', True), ('cinema', True), ('scifi', True), ('other', True), ('мемы', False), ('Humor', False),
            ('\n', False)]
    x = 20
    for t, ok in toks:
        w = 20 + len(t) * 8
        f.rect(x, 205, w, 28, 'ing' if ok else 'warn', r=4, dash=not ok)
        f.text(x + w / 2, 224, t, 11, kind='ing' if ok else 'warn', mono=True)
        x += w + 8
    f.text(20, 260, 'зелёные — разрешены схемой, красные — вычеркнуты до выбора токена. Невалидный JSON по форме '
           'получить нельзя;', 10.5, anchor='start', kind='ext')
    f.text(20, 278, 'по смыслу — можно (поэтому ответ ещё и проверяет Pydantic), и он может оборваться на лимите длины.',
           10.5, anchor='start', kind='ext')
    return f.render()


def repair_ladder() -> str:
    f = Fig(760, 300)
    f.text(20, 22, 'LLMClient.structured(): что происходит, если ответ не прошёл проверку', 12.5, 'bold',
           anchor='start')
    f.box(20, 50, 170, 60, 'Запрос по схеме', ['max_tokens = N'], 'llm', tsize=12, lsize=10)
    f.box(230, 50, 150, 60, 'Pydantic', ['валиден?'], 'srch', tsize=12, lsize=10)
    f.line(190, 80, 228, 80, 'ext')
    f.box(430, 30, 150, 46, 'Готово', [], 'ing', tsize=12)
    f.line(380, 70, 428, 55, 'ing')
    f.label(400, 52, 'да', kind='ing', size=10)
    f.box(230, 150, 150, 60, 'Обрыв по длине?', ['finish = length'], 'sec', tsize=11.5, lsize=10)
    f.line(305, 110, 305, 148, 'warn')
    f.label(320, 132, 'нет', kind='warn', size=10, anchor='start')
    f.box(430, 120, 300, 54, 'Повтор с бюджетом 2N', ['одна попытка: больше места, а не «исправь»'], 'llm',
          tsize=11.5, lsize=10)
    f.line(380, 170, 428, 150, 'llm')
    f.label(405, 150, 'да', kind='llm', size=10)
    f.box(430, 190, 300, 54, 'Просьба исправить', ['ответ + текст ошибки → «верни только JSON»'], 'llm', tsize=11.5,
          lsize=10)
    f.line(380, 190, 428, 215, 'llm')
    f.label(405, 214, 'нет', kind='llm', size=10)
    f.box(430, 254, 300, 40, 'Снова невалидно → LLMOutputError', [], 'warn', tsize=11.5)
    f.text(20, 250, 'Исключение ловит вызывающий шаг', 10.5, anchor='start', kind='ext')
    f.text(20, 266, 'и деградирует: «vision:failed»,', 10.5, anchor='start', kind='ext')
    f.text(20, 282, '«classify:failed», «grade: …».', 10.5, anchor='start', kind='ext')
    return f.render()


def bench() -> str:
    return gbars('Скорость моделей на процессоре (D1, docker, 16 потоков)',
                 [('Qwen3.6-35B-A3B\nтекст', [223, 19.0]), ('Gemma 4 26B-A4B\nкартинки', [204, 16.9]),
                  ('gpt-oss-120b\nсудья', [142, 17.2])],
                 [('чтение промпта, токенов/с', 'llm'), ('генерация, токенов/с', 'col')], 260, fmt='{:.0f}',
                 height=280, note='генерация почти одинаковая: она упирается в память, а не в размер модели')
