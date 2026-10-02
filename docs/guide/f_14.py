"""Chapter 14: observability with Langfuse."""
from figs import Fig


def trace_tree() -> str:
    f = Fig(780, 300)
    f.text(20, 22, 'Один трейс агента в Langfuse: дерево наблюдений с токенами и временем', 12.5, 'bold',
           anchor='start')
    f.rect(20, 40, 740, 250, 'obs', r=12, fill='#fefce8')
    f.text(36, 62, 'trace «agent» · user 0 · вход: «что-нибудь смешное про котов» · выход: ответ · 931 с · 5 203 ток.',
           11, 'bold', kind='obs', anchor='start')
    rows = [('agent', 'agent', 0, ''), ('route', 'generation', 1, 'qwen3.6 · 296 → 59 ток. · 48,6 с'),
            ('rewrite', 'generation', 1, '44,9 с'), ('retrieve', 'retriever', 1, '3 запроса · 44 кандидата · 8,2 с'),
            ('grade', 'generation', 1, '1 031 → 423 ток. · 251 с · релевантны 2 из 10'),
            ('rewrite_query', 'generation', 1, '23,5 с'), ('retrieve', 'retriever', 1, '0,5 с'),
            ('grade', 'generation', 1, '1 164 → 527 ток. · 288 с · 9 из 10'),
            ('synthesize', 'generation', 1, '1 243 → 460 ток. · 266 с · 8 цитат')]
    for i, (name, kind, depth, note) in enumerate(rows):
        y = 84 + i * 21
        x = 40 + depth * 24
        if depth:
            f.line(48, y - 6, x - 4, y - 6, 'obs', head=False, sw=1)
        k = {'agent': 'obs', 'generation': 'llm', 'retriever': 'srch'}[kind]
        f.rect(x, y - 15, 120, 18, k, r=3)
        f.text(x + 60, y - 2, name, 10, kind=k, mono=True)
        f.text(x + 130, y - 2, kind, 9.5, kind='ext', anchor='start', italic=True)
        f.text(x + 215, y - 2, note, 10, anchor='start')
    f.line(48, 84, 48, 252, 'obs', head=False, sw=1)
    f.rect(500, 255, 250, 26, 'dig', r=5)
    f.text(625, 272, 'score: like = 1 (кнопка 👍 в боте)', 10.5, kind='dig')
    return f.render()


def langfuse_arch() -> str:
    f = Fig(780, 330)
    f.text(20, 22, 'Как устроен Langfuse внутри (6 контейнеров проекта tgdigest)', 12.5, 'bold', anchor='start')
    f.box(20, 60, 150, 70, 'Код проекта', ['Langfuse SDK 4', '(OpenTelemetry)'], 'srch', tsize=12, lsize=10)
    f.box(20, 200, 150, 60, 'Вы в браузере', ['localhost:3000', 'через SSH-туннель'], 'you', tsize=12, lsize=10)
    f.box(230, 60, 160, 70, 'langfuse-web', ['приём событий', 'UI и API · :3000'], 'obs', tsize=12, lsize=10)
    f.box(450, 40, 140, 60, 'MinIO', ['сырые события', '(S3)'], 'db', tsize=12, lsize=10)
    f.box(450, 130, 140, 60, 'Redis', ['очередь задач'], 'db', tsize=12, lsize=10)
    f.box(620, 85, 140, 70, 'langfuse-worker', ['разбирает очередь', 'пишет в ClickHouse'], 'obs', tsize=11.5,
          lsize=10)
    f.box(450, 230, 140, 70, 'ClickHouse', ['трейсы, наблюдения,', 'оценки · 3,5 ГБ'], 'db', tsize=12, lsize=10)
    f.box(230, 230, 160, 70, 'Postgres', ['база langfuse: проекты,', 'ключи, промпты, датасеты'], 'db', tsize=12,
          lsize=10)
    f.line(170, 95, 228, 95, 'srch', sw=1.6)
    f.label(199, 88, 'HTTP', kind='srch', size=9.5)
    f.line(390, 80, 448, 70, 'obs', sw=1.4)
    f.line(390, 110, 448, 155, 'obs', sw=1.4)
    f.line(590, 155, 618, 130, 'db', sw=1.4)
    f.line(590, 70, 618, 105, 'db', sw=1.4)
    f.path('M690,155 C690,220 640,260 592,262', 'obs', sw=1.4)
    f.line(310, 130, 310, 228, 'obs', both=True, sw=1.4)
    f.path('M390,120 C430,180 440,230 448,250', 'obs', sw=1.2, dash=True)
    f.label(440, 200, 'чтение', kind='obs', size=9)
    f.line(170, 230, 228, 115, 'you', sw=1.4)
    f.text(20, 318, 'Запись идёт асинхронно: SDK копит события в памяти и отправляет пачками; трейсинг не замедляет агента '
           'и не может его уронить.', 10.5, anchor='start', kind='ext')
    return f.render()


def datasets_grid() -> str:
    f = Fig(780, 220)
    f.text(20, 22, 'Датасет и прогоны: одни и те же вопросы, разные версии системы', 12.5, 'bold', anchor='start')
    items = ['r01 Кратос', 'r02 сборы', 'r11 Wolverine', '…', 'r20 Хоттабыч']
    runs = [('A', [1, 1, 0, None, 1]), ('B', [1, 1, 0, None, 1]), ('C', [1, 1, 0, None, 1]), ('D', [1, 1, 1, None, 1])]
    f.text(150, 55, 'датасет research-tasks (20)', 10.5, 'bold', anchor='end', kind='obs')
    for i, it in enumerate(items):
        f.text(150, 82 + i * 24, it, 10.5, anchor='end', mono=True)
    for j, (name, vals) in enumerate(runs):
        x = 180 + j * 110
        f.text(x + 45, 55, f'прогон {name}', 10.5, 'bold', kind='obs')
        for i, v in enumerate(vals):
            y = 68 + i * 24
            if v is None:
                f.text(x + 45, y + 15, '…', 11, kind='ext')
                continue
            k = 'ing' if v else 'warn'
            f.rect(x + 10, y, 70, 19, k, r=3)
            f.text(x + 45, y + 14, 'success 1' if v else 'success 0', 9.5, kind=k)
    f.text(640, 82, 'в каждой клетке —', 10.5, anchor='start', kind='ext')
    f.text(640, 98, 'ответ и оценки:', 10.5, anchor='start', kind='ext')
    f.text(640, 114, 'success, steps,', 10.5, anchor='start', kind='ext', mono=True)
    f.text(640, 130, 'tokens, seconds', 10.5, anchor='start', kind='ext', mono=True)
    return f.render()
