"""Chapter 1 and the cover: the system at a glance."""
from figs import Fig


def cover_layers() -> str:
    f = Fig(740, 300)
    # pipeline band
    f.rect(10, 10, 720, 128, 'ing', r=14, fill='#f0fdf4', dash=True)
    f.text(24, 32, 'Конвейер данных — работает фоном, часами', 12, 'bold', kind='ing', anchor='start')
    steps = [('Telegram', ['≈ 24 тыс. сообщений'], 'ext'), ('Коллектор', ['HTML → БД'], 'col'),
             ('Ingest-агент', ['OCR · темы · реклама'], 'ing'), ('Дедупликация', ['кластеры повторов'], 'ing'),
             ('Индексатор', ['BGE-M3 → Qdrant'], 'srch')]
    for i, (t, ls, k) in enumerate(steps):
        x = 24 + i * 142
        f.box(x, 50, 124, 70, t, ls, k, tsize=12.5, lsize=10)
        if i:
            f.line(x - 18, 85, x - 2, 85, 'ext', sw=1.8)
    # service band
    f.rect(10, 162, 560, 128, 'svc', r=14, fill='#fff7ed', dash=True)
    f.text(24, 184, 'Сервис — отвечает по запросу, за минуты', 12, 'bold', kind='svc', anchor='start')
    svc = [('Вы', ['Telegram'], 'you'), ('Бот', ['aiogram'], 'svc'), ('API', ['FastAPI'], 'svc'),
           ('Агент · дайджест', ['LangGraph'], 'srch')]
    for i, (t, ls, k) in enumerate(svc):
        x = 24 + i * 136
        f.box(x, 202, 118, 70, t, ls, k, tsize=12.5, lsize=10)
        if i:
            f.line(x - 16, 237, x - 2, 237, 'ext', sw=1.8, both=True)
    f.box(590, 162, 140, 128, 'llama-server', ['Qwen3.6 — текст', 'Gemma 4 — картинки', 'gpt-oss — судья',
                                                'только CPU'], 'llm', tsize=12.5, lsize=10)
    f.line(660, 140, 660, 160, 'llm', dash=True, both=True)
    f.line(572, 237, 588, 237, 'llm', dash=True, both=True)
    return f.render()


def big_map() -> str:
    f = Fig(780, 600)
    f.text(20, 20, 'Карта системы: цвет — слой, сплошная стрелка — данные, пунктир — вызовы модели', 11.5, 'bold',
           anchor='start')
    # band labels
    f.text(20, 46, 'ФОНОМ', 9.5, 'bold', kind='ext', anchor='start')
    f.text(20, 330, 'ПО ЗАПРОСУ', 9.5, 'bold', kind='ext', anchor='start')
    # row 1: pipeline
    row1 = [(20, 'Telegram', ['t.me/s/канал', '28 каналов'], 'ext'),
            (175, 'Коллектор', ['HTML → строки', 'фото → файлы'], 'col'),
            (330, 'Ingest-агент', ['OCR, подпись,', 'тема, реклама'], 'ing'),
            (485, 'Дедупликация', ['хэши, pHash,', 'union-find'], 'ing'),
            (640, 'Индексатор', ['BGE-M3:', 'dense + sparse'], 'srch')]
    for x, t, ls, k in row1:
        f.box(x, 56, 125, 64, t, ls, k, tsize=12.5, lsize=10)
    for x in (145, 300, 455, 610):
        f.line(x + 2, 88, x + 28, 88, 'ext', sw=1.8)
    f.label(160, 140, 'через VPN-маршрут', kind='col', size=9.5)
    # row 2: storage + model
    f.box(175, 178, 280, 70, 'Postgres + папка медиа', ['посты · обогащение · кластеры', 'голоса · профиль · выпуски'],
          'db', tsize=12.5, lsize=10)
    f.box(640, 178, 125, 70, 'Qdrant', ['17 272 точки', '4 варианта текста'], 'srch', tsize=12.5, lsize=10)
    f.line(237, 120, 237, 176, 'col', sw=1.6)
    f.line(392, 176, 392, 122, 'ing', both=True, sw=1.6)
    f.line(520, 122, 440, 176, 'ing', both=True, sw=1.6)
    f.path('M455,225 C560,225 600,160 660,122', 'srch', sw=1.6)
    f.label(560, 205, 'читает', kind='srch', size=9.5)
    f.line(702, 120, 702, 176, 'srch', sw=1.6)
    # model
    f.box(470, 262, 160, 74, 'llama-server', ['Qwen3.6 · Gemma 4', 'gpt-oss-120b · CPU'], 'llm', tsize=12.5,
          lsize=10)
    f.path('M392,120 C392,160 470,260 500,262', 'llm', dash=True, sw=1.4, head=True)
    # row 3: service
    f.box(20, 360, 125, 64, 'Вы', ['Telegram', 'на телефоне'], 'you', tsize=12.5, lsize=10)
    f.box(175, 360, 125, 64, 'Бот', ['aiogram,', 'на сервере'], 'svc', tsize=12.5, lsize=10)
    f.box(330, 360, 125, 64, 'FastAPI', ['/search /digest', '/why /feedback'], 'svc', tsize=12.5, lsize=10)
    f.box(485, 360, 140, 64, 'Поисковый агент', ['route → retrieve', '→ grade → answer'], 'srch', tsize=12,
          lsize=10)
    f.box(485, 450, 140, 64, 'Профиль · дайджест', ['планировщик, куратор,', 'редактор, критик'], 'dig',
          tsize=12, lsize=10)
    f.box(655, 360, 110, 154, 'Langfuse', ['трейсы', 'датасеты', 'оценки', 'промпты'], 'obs', tsize=12.5, lsize=10)
    for x in (145, 300, 455):
        f.line(x + 2, 392, x + 28, 392, 'svc', both=True, sw=1.6)
    f.path('M455,410 C470,450 470,482 483,482', 'svc', sw=1.6, head=True)
    # online reads
    f.path('M625,372 C690,372 702,320 702,250', 'srch', sw=1.6)
    f.label(708, 312, 'поиск', kind='srch', size=9.5, anchor='start')
    f.line(520, 358, 535, 338, 'llm', dash=True, sw=1.4)
    f.path('M485,500 L315,500 L315,250', 'dig', sw=1.4)
    f.label(400, 494, 'кандидаты недели', kind='dig', size=9.5)
    f.path('M625,470 L642,470 L642,300 L632,300', 'llm', dash=True, sw=1.4)
    f.line(627, 392, 653, 392, 'obs', dash=True, sw=1.4)
    f.line(627, 482, 653, 482, 'obs', dash=True, sw=1.4)
    # telegram route note
    f.rect(20, 540, 270, 50, 'col', r=8, dash=True, fill='#f0f9ff')
    f.text(32, 560, 'Сбор и бот ходят в Telegram с сервера: адреса', 10.5, kind='col', anchor='start')
    f.text(32, 577, 'Telegram маршрутизируются через VPN (глава 15).', 10.5, kind='col', anchor='start')
    return f.render()
