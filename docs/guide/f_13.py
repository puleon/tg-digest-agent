"""Chapter 13: the API and the Telegram bot."""
from figs import Fig


def async_loop() -> str:
    f = Fig(780, 230)
    f.text(20, 22, 'Асинхронность: пока один запрос ждёт модель, сервер обслуживает другие', 12.5, 'bold',
           anchor='start')
    rows = [('/search «коты»', [(0, 4, 'svc', ''), (4, 120, 'llm', 'ждёт модель (await)'), (124, 8, 'svc', '')]),
            ('/health', [(20, 3, 'svc', '')]),
            ('/why/1/17867', [(40, 2, 'svc', '')]),
            ('/search «Кратос»', [(60, 4, 'svc', ''), (64, 100, 'llm', 'ждёт модель')])]
    sc = 3.0
    for i, (lab, segs) in enumerate(rows):
        y = 45 + i * 34
        f.text(150, y + 17, lab, 10.5, anchor='end', mono=True)
        for x0, w, k, t in segs:
            f.rect(160 + x0 * sc, y, max(w * sc, 4), 24, k, r=3,
                   fill='#ffffff' if k == 'llm' else None, dash=(k == 'llm'))
            if t:
                f.text(160 + (x0 + w / 2) * sc, y + 16, t, 10, kind=k)
    f.text(160, 196, 'оранжевое — работа процесса API (миллисекунды); пунктир — ожидание ответа от llama-server '
           '(десятки секунд),', 10.5, anchor='start', kind='ext')
    f.text(160, 213, 'в это время цикл событий свободен. Поиск по Qdrant и реранкер считаются в отдельном потоке '
           '(asyncio.to_thread).', 10.5, anchor='start', kind='ext')
    return f.render()


def bot_sequence() -> str:
    f = Fig(780, 400)
    f.text(20, 22, 'Один вопрос боту: кто кому что отправляет', 12.5, 'bold', anchor='start')
    lanes = [('Вы', 'you', 70), ('Серверы Telegram', 'ext', 210), ('Бот (сервер)', 'svc', 360),
             ('API (сервер)', 'svc', 510), ('Агент + модель', 'srch', 670)]
    for name, k, x in lanes:
        f.box(x - 65, 40, 130, 34, name, [], k, tsize=11)
        f.line(x, 74, x, 385, k, head=False, sw=1, dash=True)
    msgs = [(70, 210, 100, 'you', '«что-нибудь смешное про котов»'),
            (210, 360, 130, 'ext', 'getUpdates (long polling) → новое сообщение'),
            (360, 210, 160, 'svc', 'sendMessage «Ищу… пару минут»'),
            (360, 510, 190, 'svc', 'POST /search {query, user_id} по 127.0.0.1'),
            (510, 670, 220, 'srch', 'run_agent(query): 6–12 шагов'),
            (670, 510, 260, 'srch', 'ответ, цитаты, трейс, деградации'),
            (510, 360, 290, 'svc', 'JSON-ответ'),
            (360, 210, 320, 'svc', 'editMessageText: ответ + кнопки 👍 👎 💾'),
            (210, 70, 350, 'ext', 'вы видите ответ'),
            ]
    for x1, x2, y, k, t in msgs:
        f.line(x1, y, x2, y, k, sw=1.4)
        f.label((x1 + x2) / 2, y - 5, t, kind=k, size=9.5)
    f.text(20, 395, 'Telegram не ходит к боту сам: бот открывает запрос «есть новое?», Telegram держит его, пока не придёт '
           'сообщение, и бот сразу открывает следующий.', 10, anchor='start', kind='ext', italic=True)
    return f.render()


def keyboard() -> str:
    f = Fig(780, 210)
    f.text(20, 22, 'Кнопки под ответом и что в них зашито (callback_data, не больше 64 байт)', 12.5, 'bold',
           anchor='start')
    f.rect(20, 45, 360, 140, 'file', r=12, fill='#f8fafc')
    f.text(36, 68, '1. Заговор кошек: мем предупреждает…', 10.5, anchor='start')
    f.text(36, 86, '2. Список на двери: РЫБА, ПИВО, КОШКА…', 10.5, anchor='start')
    btns = ['👍 8648', '👎 8648', '💾 8648']
    for i, b in enumerate(btns):
        f.rect(36 + i * 110, 110, 100, 30, 'svc', r=6)
        f.text(86 + i * 110, 130, b, 11, kind='svc')
    f.rect(36, 148, 320, 28, 'svc', r=6)
    f.text(196, 167, '❓ почему этот пост (в выпуске)', 10.5, kind='svc')
    codes = [('fb:l:8648:s:3f9c…', 'лайк · пост 8648 · из поиска · id трейса'),
             ('fb:d:8648:s:3f9c…', 'дизлайк — так же'),
             ('fb:s:8648:d:1', 'сохранить · из выпуска №1'),
             ('why:1:17867', '/why для выпуска 1, поста 17867')]
    for i, (c, t) in enumerate(codes):
        y = 60 + i * 32
        f.text(410, y, c, 11, 'bold', anchor='start', mono=True, kind='svc')
        f.text(410, y + 14, t, 9.5, anchor='start', kind='ext')
    return f.render()


def bot_placement() -> str:
    f = Fig(780, 300)
    f.text(20, 22, 'Бот живёт на сервере рядом с API; обоих держит systemd', 12.5, 'bold', anchor='start')
    f.rect(20, 45, 540, 210, 'db', r=12, fill='#f8fafc')
    f.text(36, 66, 'Сервер shevek · systemd --user (Linger=yes: стартует при загрузке)', 11, 'bold', kind='db',
           anchor='start')
    f.box(40, 88, 180, 66, 'tgdigest-api', ['127.0.0.1:8000', 'агент, профиль, дайджест'], 'svc', tsize=12,
          lsize=9.5)
    f.box(300, 88, 160, 66, 'tgdigest-bot', ['long polling;', 'ждёт BOT_TOKEN в .env'], 'svc', tsize=12,
          lsize=9.5)
    f.line(222, 121, 298, 121, 'svc', both=True)
    f.label(260, 113, 'HTTP', kind='svc', size=9.5)
    f.box(40, 182, 180, 52, 'Postgres · Qdrant · LLM', ['контейнеры, 127.0.0.1'], 'db', tsize=11, lsize=9.5)
    f.line(130, 156, 130, 180, 'db', both=True)
    f.box(300, 182, 160, 52, 'xray-tg', ['VPN-маршрут в Telegram'], 'sec', tsize=11.5, lsize=9.5)
    f.line(380, 156, 380, 180, 'sec')
    f.box(600, 182, 160, 52, 'api.telegram.org', ['getUpdates, sendMessage'], 'ext', tsize=11, lsize=9.5)
    f.line(462, 208, 598, 208, 'sec', sw=2)
    f.box(600, 88, 160, 66, 'Вы', ['Telegram на телефоне'], 'you', tsize=12, lsize=9.5)
    f.line(680, 156, 680, 180, 'ext', both=True)
    f.text(20, 278, 'Раньше Telegram с сервера был недоступен, и бот запускался на ноутбуке, а в API ходил через '
           'make tunnel.', 10.5, anchor='start', kind='ext')
    f.text(20, 295, 'Теперь всё на одной машине: бот работает круглосуточно, и ежедневные выпуски не зависят от '
           'открытого ноутбука.', 10.5, anchor='start', kind='ext')
    return f.render()


def refresh_flow() -> str:
    f = Fig(780, 350)
    f.text(20, 22, 'Обновление корпуса: два способа запустить, один юнит, итог в Telegram', 12.5, 'bold',
           anchor='start')
    f.box(20, 40, 120, 50, 'Вы', ['/update'], 'you', tsize=12, lsize=10)
    f.box(170, 40, 120, 50, 'бот', ['POST /refresh'], 'svc', tsize=12, lsize=10)
    f.box(320, 40, 190, 50, 'API', ['файл запроса +', 'systemctl --user start'], 'svc', tsize=12, lsize=9.5)
    f.box(600, 40, 160, 50, 'таймер systemd', ['каждую ночь в 02:30'], 'file', tsize=11.5, lsize=9.5)
    f.line(140, 65, 168, 65, 'you')
    f.line(290, 65, 318, 65, 'svc')
    f.line(415, 90, 415, 128, 'svc')
    f.line(680, 90, 680, 128, 'file')
    f.rect(20, 130, 740, 112, 'ing', r=12, fill='#f0fdf4')
    f.text(32, 150, 'tgdigest-refresh.service — oneshot, один прогон за раз (блокировка файла)', 11, 'bold',
           kind='ing', anchor='start')
    steps = [('сбор', ['новые посты']), ('просмотры', ['за 7 дней']), ('обогащение', ['Gemma: картинки,', 'Qwen: посты']),
             ('ссылки', ['и сущности', '(кино)']), ('дубли', ['кластеры']), ('индекс', ['BGE-M3']),
             ('профили', ['голоса', 'из бота'])]
    for i, (t, ls) in enumerate(steps):
        x = 32 + i * 104
        f.box(x, 162, 94, 66, t, ls, 'ing', tsize=11, lsize=9)
        if i:
            f.line(x - 9, 195, x - 1, 195, 'ing', sw=1.4)
    f.box(20, 270, 330, 52, 'data/refresh/status.json', ['шаг, счётчики, ошибка · история прогонов'], 'db',
          tsize=11, lsize=9.5)
    f.line(185, 242, 185, 268, 'db')
    f.label(268, 259, 'после каждого шага', kind='db', size=9.5)
    f.box(400, 270, 360, 52, 'итог в Telegram', ['тому, кто запросил; ночью — только при ошибке'], 'you',
          tsize=11, lsize=9.5)
    f.line(580, 242, 580, 268, 'you')
    f.text(20, 344, '/update status: API читает файл состояния и проверяет блокировку — с самим процессом '
           'обновления он не разговаривает.', 10, anchor='start', kind='ext', italic=True)
    return f.render()
