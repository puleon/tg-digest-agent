"""Chapter 16: engineering practices."""
from figs import Fig, hbars


def dev_workflow() -> str:
    f = Fig(780, 270)
    f.text(20, 22, 'Где живёт код и где он работает', 12.5, 'bold', anchor='start')
    f.box(20, 60, 200, 150, 'Ноутбук', ['код, git, тесты,', 'ruff, mypy', 'make check', '', 'отчёты, README'],
          'you', tsize=12.5, lsize=10)
    f.box(290, 40, 200, 80, 'GitHub', ['puleon/tg-digest-agent', 'CI на каждый push'], 'ext', tsize=12.5, lsize=10)
    f.box(560, 60, 200, 150, 'Сервер shevek', ['сервисы в Docker', 'модели и данные', 'долгие прогоны:', 'ingest, оценки',
                                               'nohup-скрипты'], 'db', tsize=12.5, lsize=10)
    f.line(220, 90, 288, 80, 'you')
    f.label(254, 74, 'git push', kind='you', size=9.5)
    f.line(222, 160, 558, 160, 'col', sw=1.8)
    f.label(390, 152, 'make sync (rsync, без data/ и .env)', kind='col', size=10)
    f.line(558, 195, 222, 195, 'ing', sw=1.8)
    f.label(390, 205, 'make pull-results · scp файлов разметки', kind='ing', size=10)
    f.text(20, 250, 'Данные и секреты никогда не уходят в git: .env, data/ и модели исключены и из rsync, и из '
           'репозитория.', 10.5, anchor='start', kind='ext')
    return f.render()


def ci_jobs() -> str:
    f = Fig(780, 220)
    f.text(20, 22, 'CI на GitHub Actions: три задачи на каждый push и pull request', 12.5, 'bold', anchor='start')
    jobs = [('check', ['uv sync --frozen', 'ruff check + format', 'mypy --strict', 'pytest (≈ 180 тестов)']),
            ('integration', ['Postgres 16 как сервис', 'alembic upgrade head', 'alembic check', 'pytest -m integration']),
            ('secrets', ['вся история git', 'gitleaks: ключи,', 'токены, пароли'])]
    for i, (t, ls) in enumerate(jobs):
        x = 20 + i * 255
        f.box(x, 45, 235, 130, t, ls, 'ing', tsize=13, lsize=10.5)
    f.text(20, 205, 'Красный CI — повод остановиться: так поймали миграцию с JSON вместо JSONB и тест, который падал '
           '«иногда».', 10.5, anchor='start', kind='ext')
    return f.render()


def test_fakes() -> str:
    f = Fig(780, 250)
    f.text(20, 22, 'Как тестировать систему с моделью без модели', 12.5, 'bold', anchor='start')
    rows = [('Языковая модель', 'ScriptedLLM, CrewLLM, ComboLLM', 'отвечают по правилам, ломаются по заказу'),
            ('BGE-M3', 'FakeEmbedder', 'мешок хэшированных слов: общие слова → близкие векторы'),
            ('Qdrant', 'QdrantClient(":memory:")', 'настоящий клиент, индекс в памяти'),
            ('Postgres', 'SQLite в файле', 'та же схема из тех же классов'),
            ('HTTP: Википедия, страницы', 'httpx.MockTransport', 'заранее заданные ответы и ошибки'),
            ('Telegram', 'сохранённые страницы t.me/s', 'фикстуры в tests/collector/fixtures')]
    for i, (real, fake, how) in enumerate(rows):
        y = 45 + i * 33
        f.rect(20, y, 190, 26, 'ext', r=4)
        f.text(115, y + 17, real, 10.5, kind='ext')
        f.line(210, y + 13, 238, y + 13, 'ing', sw=1.2)
        f.rect(240, y, 220, 26, 'ing', r=4)
        f.text(350, y + 17, fake, 10, kind='ing', mono=True)
        f.text(475, y + 17, how, 10, anchor='start')
    return f.render()


def cost_chart() -> str:
    return hbars('Сколько стоила бы операция по прайсу Claude Haiku 4.5 ($1 / $5 за млн токенов, июнь 2026)',
                 [('вопрос в режиме search', 0.87, 'srch', '0,9 цента'), ('вопрос в режиме research', 0.82, 'srch', '0,8 цента'),
                  ('проверка ответа на факты', 1.2, 'sec', '1,2 цента'), ('выпуск дайджеста', 2.5, 'dig', '2,5 цента'),
                  ('обогащение 100 постов', 20, 'ing', '20 центов')],
                 scale=17, labelw=240, width=780,
                 note='Локально те же операции стоят минуты процессора; деньги — только эквивалент для сравнения.')
