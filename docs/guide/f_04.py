"""Chapter 4: the collector."""
from figs import Fig


def telegram_ways() -> str:
    f = Fig(760, 250)
    f.text(20, 22, 'Три способа прочитать публичный канал Telegram', 12.5, 'bold', anchor='start')
    ways = [('Bot API', ['api.telegram.org/bot…', 'бот видит только чаты,', 'куда его добавили', 'чужой канал — нельзя'],
             'warn', '✕'),
            ('MTProto (Telethon)', ['клиентский протокол,', 'как у приложения', 'нужен аккаунт и номер', 'не получилось завести'],
             'sec', '·'),
            ('Веб-превью t.me/s/…', ['HTML, который Telegram', 'сам рисует для браузера', 'без аккаунта и ключей',
                                       'основной источник'], 'col', '✓')]
    for i, (t, ls, k, mark) in enumerate(ways):
        x = 20 + i * 248
        f.box(x, 45, 228, 150, t, ls, k, tsize=13, lsize=11)
        f.circle(x + 206, 66, 13, k, fill='#ffffff')
        f.text(x + 206, 71, mark, 15, 'bold', kind=k)
    f.text(20, 225, 'Telethon остался в коде как запасной источник: оба реализуют один интерфейс TelegramSource, '
           'и остальной код', 10.5, anchor='start', kind='ext')
    f.text(20, 241, 'не знает, откуда пришли сообщения.', 10.5, anchor='start', kind='ext')
    return f.render()


def preview_anatomy() -> str:
    f = Fig(780, 345)
    f.text(20, 22, 'Как кусок HTML превращается в строку базы (collector/web.py)', 12.5, 'bold', anchor='start')
    html = ['<div class="tgme_widget_message"', '     data-post="am_devs/49719">',
            '  <a class="…photo_wrap"', '     style="background-image:url(\'…/file.jpg\')">',
            '  <div class="…message_text js-message_text">', '  <span class="…views">9.35K</span>',
            '  <span class="tgme_reaction">😁 255</span>', '  <a class="…message_date">',
            '    <time datetime="2026-05-04T11:00:00+00:00">']
    f.rect(20, 40, 400, 290, 'file', r=8, fill='#0f172a')
    for i, ln in enumerate(html):
        f.text(32, 64 + i * 30, ln, 10.5, anchor='start', mono=True, color='#e2e8f0')
    fields = [(94, 'tg_message_id = 49719', 'регулярка по data-post'), (154, 'media: photo + URL', 'из CSS-стиля'),
              (184, 'text (пусто у 8648)', 'только js-message_text'), (214, 'views = 9 350', '«9.35K» → число'),
              (244, 'reactions_count = 255', 'сумма по эмодзи'), (304, 'posted_at = 2026-05-04 11:00', 'time внутри даты')]
    for y, field, how in fields:
        f.line(420, y - 5, 470, y - 5, 'col', sw=1.2)
        f.rect(472, y - 18, 290, 25, 'col', r=5)
        f.text(484, y - 1, field, 10.5, 'bold', kind='col', anchor='start', mono=True)
        f.text(752, y - 1, how, 9.5, kind='col', anchor='end', italic=True)
    return f.render()


def pagination() -> str:
    f = Fig(760, 210)
    f.text(20, 22, 'Листание назад: страница за страницей, пока не встретится уже собранное', 12.5, 'bold',
           anchor='start')
    pages = [('t.me/s/am_devs', '49740…49721', 'col'), ('?before=49721', '49720…49701', 'col'),
             ('?before=49701', '49700…49681', 'col'), ('?before=49681', '…49412', 'ext')]
    for i, (url, ids, k) in enumerate(pages):
        x = 20 + i * 185
        f.box(x, 50, 165, 70, url, [f'сообщения {ids}', '20 постов'], k, tsize=11, lsize=10)
        if i:
            f.line(x - 18, 85, x - 2, 85, 'col', sw=1.6)
    f.line(20, 150, 740, 150, 'ext', head=False, sw=1)
    f.text(20, 172, 'Стоп-условия: дошли до курсора канала (last_message_id — последний собранный) или до даты «полгода назад».',
           10.5, anchor='start')
    f.text(20, 192, 'Между страницами — пауза 0,8 с, между скачиваниями картинок — 0,15 с: страницы t.me не рассчитаны на поток.',
           10.5, anchor='start', kind='ext')
    return f.render()


def reverse_tunnel() -> str:
    f = Fig(780, 300)
    f.text(20, 22, 'make collect: сервер ходит в Telegram через ноутбук (обратный SOCKS-туннель SSH)', 12.5, 'bold',
           anchor='start')
    f.rect(20, 45, 360, 200, 'db', r=12, fill='#f8fafc')
    f.text(36, 66, 'Сервер shevek', 12.5, 'bold', kind='db', anchor='start')
    f.box(40, 80, 150, 64, 'tgdigest collector', ['httpx с прокси', 'socks5h://127.0.0.1:1080'], 'col', tsize=11,
          lsize=9.5)
    f.box(220, 80, 140, 64, 'sshd', ['слушает 127.0.0.1:1080', 'для этой сессии'], 'ext', tsize=11.5, lsize=9.5)
    f.line(190, 112, 218, 112, 'col')
    f.box(40, 170, 320, 56, 'Прямой путь в 149.154.0.0/16 (Telegram)', ['соединения не устанавливаются ✕'], 'warn',
          tsize=11, lsize=10)
    f.rect(420, 45, 340, 200, 'you', r=12, fill='#f0f9ff')
    f.text(436, 66, 'Ноутбук (Mac)', 12.5, 'bold', kind='you', anchor='start')
    f.box(440, 80, 150, 64, 'ssh -R 1080 shevek', ['клиент SSH открывает', 'соединения сам'], 'you', tsize=11,
          lsize=9.5)
    f.box(620, 80, 120, 64, 't.me', ['ответ за 0,5 с'], 'ext', tsize=12, lsize=10)
    f.line(590, 112, 618, 112, 'you', both=True)
    f.path('M360,112 C390,112 410,112 438,112', 'col', sw=2.2)
    f.label(400, 104, 'SSH', kind='col', size=10)
    f.text(20, 270, 'Флаг -R 1080 без адреса — «обратный динамический» проброс: на сервере появляется SOCKS-прокси, а '
           'соединения', 10.5, anchor='start', kind='ext')
    f.text(20, 287, 'выходят в интернет с ноутбука. Пока команда работает — сбор идёт; закрыли ноутбук — сбор встал '
           'и продолжится с курсора.', 10.5, anchor='start', kind='ext')
    return f.render()
