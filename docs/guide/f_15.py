"""Chapter 15: containers and the internal network."""
from figs import Fig, hbars


def vm_vs_container() -> str:
    f = Fig(780, 270)
    f.text(20, 22, 'Виртуальная машина против контейнера', 12.5, 'bold', anchor='start')
    # VM
    f.text(200, 50, 'Виртуальные машины', 12, 'bold', kind='ext')
    for i, app in enumerate(['Postgres', 'Qdrant']):
        x = 40 + i * 170
        f.box(x, 62, 150, 34, app, [], 'db', tsize=11)
        f.box(x, 98, 150, 34, 'своё ядро Linux', [], 'warn', tsize=10.5)
        f.box(x, 134, 150, 34, 'виртуальное железо', [], 'ext', tsize=10.5)
    f.box(40, 175, 320, 34, 'гипервизор', [], 'ext', tsize=11)
    f.box(40, 213, 320, 34, 'ядро хоста + железо', [], 'file', tsize=11)
    # containers
    f.text(590, 50, 'Контейнеры', 12, 'bold', kind='ext')
    for i, app in enumerate(['Postgres', 'Qdrant', 'ClickHouse']):
        x = 420 + i * 118
        f.box(x, 62, 108, 34, app, [], 'db', tsize=11)
        f.box(x, 98, 108, 70, 'свои: процессы,', ['файлы, сеть,', 'имя хоста'], 'col', tsize=9.5, lsize=9.5)
    f.box(420, 175, 344, 34, 'Docker: пространства имён + cgroups', [], 'sec', tsize=11)
    f.box(420, 213, 344, 34, 'одно общее ядро Linux хоста + железо', [], 'file', tsize=11)
    f.text(20, 265, 'Контейнер — обычный процесс хоста, которому ядро показывает урезанный мир. Своего ядра нет — '
           'стартует за доли секунды.', 10.5, anchor='start', kind='ext')
    return f.render()


def image_layers() -> str:
    f = Fig(780, 230)
    f.text(20, 22, 'Образ, слои, контейнер и том — на примере Postgres', 12.5, 'bold', anchor='start')
    layers = [('слой: Debian (базовый)', 'ext'), ('слой: пакеты PostgreSQL 16', 'ext'), ('слой: скрипты запуска', 'ext')]
    for i, (t, k) in enumerate(layers):
        f.rect(40, 170 - i * 38, 260, 32, k, r=4)
        f.text(170, 191 - i * 38, t, 10.5, kind=k)
    f.text(170, 70, 'образ postgres:16 (642 МБ),', 10.5, kind='ext', italic=True)
    f.text(170, 85, 'только чтение, общий для всех', 10.5, kind='ext', italic=True)
    f.rect(330, 96, 190, 32, 'col', r=4, dash=True)
    f.text(425, 117, 'слой записи контейнера', 10.5, kind='col')
    f.text(425, 150, 'исчезает при удалении', 10, kind='col', italic=True)
    f.text(425, 164, 'контейнера', 10, kind='col', italic=True)
    f.box(560, 70, 200, 90, 'том postgres_data', ['/var/lib/postgresql/data', '223 МБ, живёт отдельно', 'от контейнера'], 'db',
          tsize=11.5, lsize=9.5)
    f.line(520, 112, 558, 112, 'db', both=True)
    f.text(20, 222, 'Данные — только в томах: контейнер можно удалить и пересоздать из нового образа, база останется.',
           10.5, anchor='start', kind='ext')
    return f.render()


def host_overview() -> str:
    f = Fig(800, 420)
    f.text(20, 22, 'Что работает на сервере shevek (снято 3 октября 2026)', 12.5, 'bold', anchor='start')
    # tgdigest project
    f.rect(20, 40, 470, 200, 'srch', r=12, fill='#eef2ff', dash=True)
    f.text(32, 60, 'проект tgdigest (docker-compose.yml этого репозитория)', 11, 'bold', kind='srch', anchor='start')
    svcs = [('postgres', 'postgres:16', '132 МБ'), ('qdrant', 'qdrant 1.19.1', '278 МБ'),
            ('clickhouse', 'clickhouse 25.12', '1,6 ГБ'), ('langfuse-web', 'langfuse 4.38', '624 МБ'),
            ('langfuse-worker', 'langfuse 4.38', '509 МБ'), ('minio', 'minio 2025-09', '148 МБ'),
            ('redis', 'redis 7', '12 МБ')]
    for i, (n, img, ram) in enumerate(svcs):
        col, row = i % 4, i // 4
        x, y = 32 + col * 114, 72 + row * 80
        f.box(x, y, 106, 66, n, [img, ram], 'db' if n in ('postgres', 'qdrant', 'clickhouse', 'minio', 'redis') else 'obs',
              tsize=10.5, lsize=9)
    # llm-stack
    f.rect(510, 40, 270, 200, 'llm', r=12, fill='#faf5ff', dash=True)
    f.text(522, 60, 'проект llm-stack (общий)', 11, 'bold', kind='llm', anchor='start')
    f.box(522, 72, 120, 80, 'llm-server', ['llama.cpp 0.4.1', '34 ГБ сейчас'], 'llm', tsize=11, lsize=9)
    f.box(650, 72, 120, 80, 'whisper-server', ['речь → текст', '1 ГБ'], 'llm', tsize=11, lsize=9)
    f.text(645, 180, 'модель ассистента резидентна;', 9.5, kind='llm')
    f.text(645, 194, 'модели tg_digest грузятся', 9.5, kind='llm')
    f.text(645, 208, 'по требованию', 9.5, kind='llm')
    # nanoclaw
    f.rect(20, 255, 470, 70, 'ext', r=12, fill='#f8fafc', dash=True)
    f.text(32, 275, 'соседний проект: персональный ассистент (NanoClaw)', 11, 'bold', kind='ext', anchor='start')
    f.text(32, 295, 'iron-proxy · iron-control (web + свой Postgres) · контейнеры агента по требованию', 10,
           kind='ext', anchor='start')
    f.text(32, 312, 'с сетями tgdigest не пересекается', 10, kind='ext', anchor='start', italic=True)
    # host processes
    f.rect(510, 255, 270, 155, 'svc', r=12, fill='#fff7ed')
    f.text(522, 275, 'процессы хоста (не в контейнере)', 11, 'bold', kind='svc', anchor='start')
    for i, ln in enumerate(['uv run tgdigest api — :8000', 'tgdigest collector / ingest /', '  dedup / index — по команде',
                            'скрипты оценки', 'BGE-M3 и реранкер внутри них', '(PyTorch на CPU)']):
        f.text(522, 296 + i * 18, ln, 10, kind='svc', anchor='start', mono=i < 3)
    f.rect(20, 340, 470, 70, 'file', r=12, fill='#f8fafc')
    f.text(32, 360, 'Хост: Ubuntu 24.04, ядро 6.17, Docker 29.3, 32 потока, 91 ГБ видимой памяти', 10.5, kind='file',
           anchor='start')
    f.text(32, 378, 'Модели на диске: Qwen 21 ГБ, Gemma 18 ГБ, gpt-oss 60 ГБ, whisper 0,8 ГБ', 10.5, kind='file',
           anchor='start')
    f.text(32, 396, 'Картинки корпуса: 1,6 ГБ в data/media (обычная папка, не том)', 10.5, kind='file', anchor='start')
    return f.render()


def networks_map() -> str:
    f = Fig(800, 470)
    f.text(20, 22, 'Сети Docker на сервере и как в них попадают процессы хоста', 12.5, 'bold', anchor='start')
    # tgdigest_default bus
    f.line(160, 120, 790, 120, 'srch', head=False, sw=3)
    f.text(20, 116, 'tgdigest_default', 11.5, 'bold', kind='srch', anchor='start')
    f.text(20, 131, '172.18.0.0/16', 10, kind='srch', anchor='start')
    members = [('minio', '.2'), ('redis', '.3'), ('clickhouse', '.4'), ('qdrant', '.5'), ('postgres', '.6'),
               ('langfuse-web', '.7'), ('langfuse-worker', '.8')]
    for i, (n, ip) in enumerate(members):
        x = 190 + i * 87
        f.box(x - 42, 50, 84, 40, n, [], 'db' if 'langfuse' not in n else 'obs', tsize=9 if len(n) > 12 else 9.5)
        f.line(x, 90, x, 120, 'file', head=False, sw=1.2)
        f.circle(x, 120, 4.5, 'srch', fill='#4f46e5')
        f.text(x + 6, 136, ip, 9.5, kind='srch', anchor='start')
    # llm_shared bus
    f.line(160, 220, 470, 220, 'llm', head=False, sw=3)
    f.text(20, 216, 'llm_shared', 11.5, 'bold', kind='llm', anchor='start')
    f.text(20, 231, '172.19.0.0/16', 10, kind='llm', anchor='start')
    for i, (n, ip) in enumerate([('llm-server', '.2'), ('whisper-server', '.3')]):
        x = 230 + i * 150
        f.box(x - 55, 160, 110, 36, n, [], 'llm', tsize=10)
        f.line(x, 196, x, 220, 'file', head=False, sw=1.2)
        f.circle(x, 220, 4.5, 'llm', fill='#7c3aed')
        f.text(x + 6, 236, ip, 9.5, kind='llm', anchor='start')
    f.text(500, 200, 'обе также в сети nanoclaw-egress (172.21/16,', 9.5, kind='ext', anchor='start')
    f.text(500, 214, 'internal) — для агентов ассистента', 9.5, kind='ext', anchor='start')
    # host
    f.rect(20, 265, 770, 195, 'file', r=12, fill='#f8fafc')
    f.text(36, 287, 'Хост: у каждой сети свой мост br-… с адресом .1 — шлюз сети', 11.5, 'bold', kind='file',
           anchor='start')
    pubs = [('127.0.0.1:5432', 'postgres'), ('127.0.0.1:6333', 'qdrant'), ('127.0.0.1:3000', 'langfuse-web'),
            ('127.0.0.1:9090', 'minio (S3)'), ('127.0.0.1:8080', 'llm-server'), ('127.0.0.1:8081', 'whisper'),
            ('172.17.0.1:8080', 'llm для docker0')]
    for i, (addr, to) in enumerate(pubs):
        col, row = i % 4, i // 4
        x, y = 36 + col * 188, 300 + row * 46
        f.rect(x, y, 178, 38, 'col', r=6)
        f.text(x + 89, y + 16, addr, 10, 'bold', kind='col', mono=True)
        f.text(x + 89, y + 31, '→ ' + to, 9.5, kind='col')
    f.box(36, 398, 250, 50, 'tgdigest api, collector, ingest…', ['ходят на 127.0.0.1:порт'], 'svc', tsize=10.5,
          lsize=9.5)
    f.box(300, 398, 250, 50, 'docker-proxy + NAT ядра', ['переправляют на 172.18.x / 172.19.x'], 'sec', tsize=10.5,
          lsize=9.5)
    f.box(564, 398, 210, 50, 'Внешний мир', ['не видит ни одного порта'], 'warn', tsize=10.5, lsize=9.5)
    f.line(286, 423, 298, 423, 'ext')
    return f.render()


def dns_lookup() -> str:
    f = Fig(780, 210)
    f.text(20, 22, 'Как langfuse-web находит ClickHouse по имени', 12.5, 'bold', anchor='start')
    f.box(20, 60, 170, 80, 'langfuse-web', ['CLICKHOUSE_URL=', 'http://clickhouse:8123'], 'obs', tsize=12, lsize=9.5)
    f.box(240, 60, 190, 80, 'DNS Docker 127.0.0.11', ['знает имена сервисов', 'своей сети'], 'sec', tsize=11.5,
          lsize=9.5)
    f.box(480, 60, 120, 80, '172.18.0.4', ['ответ'], 'srch', tsize=12, lsize=10)
    f.box(640, 60, 120, 80, 'clickhouse', [':8123'], 'db', tsize=12, lsize=10)
    f.line(190, 90, 238, 90, 'ext')
    f.label(214, 83, '«clickhouse?»', kind='ext', size=9)
    f.line(430, 100, 478, 100, 'ext')
    f.line(600, 100, 638, 100, 'db')
    f.text(20, 170, 'Изнутри langfuse-web: getent hosts clickhouse → 172.18.0.4, minio → .2, redis → .3, postgres → .6;',
           10.5, anchor='start', mono=False)
    f.text(20, 188, 'а llm-server — пусто: он в другой сети, и для langfuse-web его не существует.', 10.5,
           anchor='start', kind='warn')
    return f.render()


def startup_order() -> str:
    f = Fig(780, 270)
    f.text(20, 22, 'make up: порядок запуска по проверкам здоровья', 12.5, 'bold', anchor='start')
    first = [('postgres', 'pg_isready'), ('clickhouse', 'wget /ping'), ('redis', 'redis-cli ping'),
             ('minio', 'mc ready')]
    for i, (n, chk) in enumerate(first):
        f.box(20, 45 + i * 44, 170, 38, n, [chk], 'db', tsize=10.5, lsize=9)
        f.line(190, 64 + i * 44, 278, 120, 'ing', sw=1.1)
    f.box(280, 90, 170, 60, 'condition:', ['service_healthy', 'у всех четырёх'], 'sec', tsize=11, lsize=9.5)
    f.box(500, 60, 120, 44, 'langfuse-web', ['wget /api/public/health'], 'obs', tsize=10.5, lsize=8.5)
    f.box(500, 120, 120, 44, 'langfuse-worker', [], 'obs', tsize=10.5)
    f.line(450, 120, 498, 82, 'ing', sw=1.2)
    f.line(450, 120, 498, 142, 'ing', sw=1.2)
    f.box(650, 60, 110, 104, 'make up', ['ждёт, пока', 'все healthy', '(до ~1 мин)'], 'svc', tsize=11, lsize=9.5)
    f.line(620, 100, 648, 108, 'ext')
    f.box(20, 225, 170, 38, 'qdrant', ['tcp :6333'], 'db', tsize=10.5, lsize=9)
    f.text(205, 249, 'qdrant ни от кого не зависит и стартует сразу', 10, anchor='start', kind='ext', italic=True)
    return f.render()


def ssh_tunnels() -> str:
    f = Fig(780, 300)
    f.text(20, 22, 'Два вида SSH-туннелей: к сервису на сервере и из сервера наружу', 12.5, 'bold', anchor='start')
    # local forward
    f.text(20, 52, 'make tunnel  (ssh -L)', 11.5, 'bold', kind='col', anchor='start')
    f.box(20, 62, 200, 76, 'Ноутбук', ['браузер → localhost:3000', 'бот → localhost:8000'], 'you', tsize=11.5, lsize=9.5)
    f.box(560, 62, 200, 76, 'Сервер', ['127.0.0.1:3000 Langfuse', '127.0.0.1:8000 API'], 'db', tsize=11.5, lsize=9.5)
    f.line(222, 100, 556, 100, 'col', sw=2.2)
    f.label(390, 92, 'порты ноутбука ведут на loopback сервера', kind='col', size=10)
    # reverse
    f.text(20, 178, 'make collect / make egress  (ssh -R 1080)', 11.5, 'bold', kind='col', anchor='start')
    f.box(20, 188, 200, 76, 'Ноутбук', ['сам открывает', 'соединения с t.me'], 'you', tsize=11.5, lsize=9.5)
    f.box(560, 188, 200, 76, 'Сервер', ['SOCKS на 127.0.0.1:1080', 'коллектор / бот'], 'db', tsize=11.5, lsize=9.5)
    f.line(556, 226, 222, 226, 'col', sw=2.2)
    f.label(390, 218, 'сервер выходит в интернет через ноутбук', kind='col', size=10)
    f.text(20, 290, 'Оба направления идут внутри одного зашифрованного SSH-соединения; на сервере не открыто ни одного '
           'порта наружу.', 10.5, anchor='start', kind='ext')
    return f.render()


def volumes_chart() -> str:
    return hbars('Тома данных проекта tgdigest на диске',
                 [('clickhouse_data (трейсы Langfuse)', 3.57, 'obs', '3,57 ГБ'), ('qdrant_data (векторы)', 3.11, 'srch', '3,11 ГБ'),
                  ('clickhouse_logs', 0.99, 'obs', '0,99 ГБ'), ('postgres_data', 0.223, 'db', '223 МБ'),
                  ('minio_data (события Langfuse)', 0.010, 'db', '10 МБ'), ('redis_data', 0.0007, 'db', '0,7 МБ')],
                 scale=100, labelw=270, width=780)
