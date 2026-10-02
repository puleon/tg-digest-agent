"""Chapter 9: the search agent."""
from figs import Fig, gbars


def free_vs_graph() -> str:
    f = Fig(780, 300)
    f.text(20, 22, 'Два способа построить агента', 12.5, 'bold', anchor='start')
    # free loop
    f.rect(20, 40, 360, 245, 'ext', r=12, fill='#f8fafc', dash=True)
    f.text(200, 62, 'Свободный цикл (function calling)', 12, 'bold', kind='ext')
    f.box(120, 80, 160, 56, 'Модель', ['решает, что дальше'], 'llm', tsize=12, lsize=10)
    f.box(40, 180, 140, 56, 'Инструмент', ['выбран моделью'], 'srch', tsize=12, lsize=10)
    f.box(220, 180, 140, 56, 'Ответ', ['когда модель решит'], 'svc', tsize=12, lsize=10)
    f.path('M150,136 C120,150 110,160 110,178', 'llm')
    f.label(95, 158, 'вызов', kind='llm', size=9.5)
    f.path('M180,200 C240,170 250,150 230,138', 'ext')
    f.label(240, 168, 'результат', kind='ext', size=9.5)
    f.line(250, 136, 285, 178, 'llm')
    f.text(200, 262, 'гибко, но порядок шагов и их число', 10.5, kind='ext', italic=True)
    f.text(200, 277, 'решает модель — каждый раз по-разному', 10.5, kind='ext', italic=True)
    # graph
    f.rect(400, 40, 360, 245, 'srch', r=12, fill='#eef2ff', dash=True)
    f.text(580, 62, 'Явный граф (этот проект)', 12, 'bold', kind='srch')
    nodes = ['route', 'rewrite', 'retrieve', 'grade', 'decide']
    for i, n in enumerate(nodes):
        x = 415 + i * 68
        f.box(x, 90, 60, 40, n, [], 'srch' if n != 'decide' else 'sec', tsize=10.5)
        if i:
            f.line(x - 8, 110, x - 1, 110, 'srch', sw=1.2)
    f.text(580, 160, 'модель заполняет «бланки»: режим, формулировки,', 10.5, kind='srch')
    f.text(580, 176, 'оценки, текст ответа; порядок шагов, повторы', 10.5, kind='srch')
    f.text(580, 192, 'и остановку решает код по правилам', 10.5, kind='srch')
    f.text(580, 230, 'предсказуемо, измеримо, безопасно;', 10.5, kind='srch', italic=True)
    f.text(580, 246, 'новый вид действия — новый узел в коде', 10.5, kind='srch', italic=True)
    return f.render()


def agent_graph() -> str:
    f = Fig(780, 400)
    f.text(20, 22, 'Граф поискового агента (src/tgdigest/agent/graph.py)', 12.5, 'bold', anchor='start')
    f.circle(40, 80, 16, 'ext')
    f.text(40, 84, 'START', 8, 'bold', kind='ext')
    f.box(70, 55, 110, 50, 'route', ['режим, фильтры'], 'srch', tsize=12, lsize=9.5)
    f.box(210, 55, 110, 50, 'rewrite', ['1–3 формулировки'], 'srch', tsize=12, lsize=9.5)
    f.box(350, 55, 110, 50, 'retrieve', ['search_index × N'], 'srch', tsize=12, lsize=9.5)
    f.box(490, 55, 110, 50, 'grade', ['релевантен?'], 'srch', tsize=12, lsize=9.5)
    f.box(630, 50, 130, 60, 'decide', ['правила в коде'], 'sec', tsize=12, lsize=9.5)
    for x in (56, 180, 320, 460, 600):
        f.line(x, 80, x + 28, 80, 'srch', sw=1.4)
    # branches from decide
    br = [('requery', 'новые формулировки', 'srch', 175), ('broaden', 'снять тему и период', 'srch', 235),
          ('verify', 'Википедия + страница', 'ing', 295), ('synthesize', 'ответ с цитатами', 'svc', 355),
          ('caveat', 'ответ с оговоркой', 'warn', 355)]
    f.box(330, 150, 140, 50, 'requery', ['≤ 2 раз'], 'srch', tsize=11.5, lsize=9.5)
    f.box(130, 150, 160, 50, 'broaden', ['один раз, если 0'], 'srch', tsize=11.5, lsize=9.5)
    f.box(500, 220, 150, 50, 'verify', ['режим research'], 'ing', tsize=11.5, lsize=9.5)
    f.box(500, 310, 150, 50, 'synthesize', ['ответ + [post N]'], 'svc', tsize=11.5, lsize=9.5)
    f.box(680, 220, 90, 50, 'caveat', ['⚠ бюджет'], 'warn', tsize=11, lsize=9.5)
    f.path('M660,110 C600,160 520,170 472,172', 'srch')
    f.path('M640,110 C560,140 360,130 292,165', 'srch')
    f.path('M690,110 C690,170 640,200 640,218', 'ing')
    f.path('M700,110 C740,180 720,270 652,325', 'svc')
    f.line(725, 110, 725, 218, 'warn')
    f.path('M400,150 C400,130 405,118 405,107', 'srch')
    f.path('M210,150 C210,125 330,118 352,100', 'srch')
    f.line(575, 270, 575, 308, 'ing')
    f.box(680, 300, 90, 40, 'END', [], 'ext', tsize=11)
    f.line(650, 335, 678, 325, 'ext')
    f.line(725, 270, 725, 298, 'warn')
    f.text(20, 230, 'Бюджеты:', 11, 'bold', anchor='start')
    f.text(20, 248, '6 кругов поиска', 10.5, anchor='start', kind='ext')
    f.text(20, 264, '40 000 токенов', 10.5, anchor='start', kind='ext')
    f.text(20, 290, 'Достаточно, если:', 11, 'bold', anchor='start')
    f.text(20, 308, 'релевантных ≥ 3 (research: ≥ 2)', 10.5, anchor='start', kind='ext')
    f.text(20, 324, 'или ≥ 30 % проверенных', 10.5, anchor='start', kind='ext')
    f.text(20, 380, 'Каждая стрелка из decide — возвращаемое значение функции decide(state); LangGraph переходит по нему.',
           10.5, anchor='start', kind='ext', italic=True)
    return f.render()


def trace_gantt() -> str:
    f = Fig(780, 300)
    f.text(20, 22, '«что-нибудь смешное про котов»: восемь шагов одного прогона (секунды, сервер под нагрузкой ingest)',
           12, 'bold', anchor='start')
    steps = [('route', 48.6, 'srch', '296 + 59 ток.'), ('rewrite', 44.9, 'srch', ''), ('retrieve', 8.2, 'col', '44 кандидата'),
             ('grade', 251.1, 'srch', '1 031 + 423 ток. · 2 из 10'), ('rewrite_query', 23.5, 'srch', ''),
             ('retrieve', 0.5, 'col', ''), ('grade', 287.9, 'srch', '1 164 + 527 ток. · 9 из 10'),
             ('synthesize', 266.0, 'svc', '1 243 + 460 ток. · 8 цитат')]
    t, sc = 0.0, 0.62
    for i, (n, sec, k, note) in enumerate(steps):
        y = 42 + i * 28
        f.text(130, y + 15, n, 10.5, anchor='end', mono=True)
        x = 140 + t * sc
        w = max(sec * sc, 2)
        f.rect(x, y, w, 20, k, r=3)
        label = f'{sec:g} с {("· " + note) if note else ""}'
        if x + w + 210 > 775:
            f.text(x - 5, y + 15, label, 9.5, anchor='end', kind=k)
        else:
            f.text(x + w + 5, y + 15, label, 9.5, anchor='start', kind=k)
        t += sec
    f.text(20, 282, 'Итого 931 с и 5 203 токена. Дорого письмо (grade, synthesize), а не поиск (retrieve < 10 с): '
           'на свободном сервере тот же прогон ≈ 2 минуты.', 10.5, anchor='start', kind='ext')
    return f.render()


def tool_contract() -> str:
    f = Fig(780, 230)
    f.text(20, 22, 'ToolRegistry.call(): каждый вызов инструмента проходит одни и те же ворота', 12.5, 'bold',
           anchor='start')
    steps = [('найти', 'по имени', 'not_found'), ('проверить', 'аргументы Pydantic', 'invalid_args'),
             ('подтвердить', 'если пишет', 'needs_confirmation'), ('выполнить', 'с таймаутом', 'timeout'),
             ('поймать', 'любое исключение', 'unavailable / failed')]
    for i, (t, sub, err) in enumerate(steps):
        x = 20 + i * 152
        f.box(x, 45, 138, 56, t, [sub], 'srch', tsize=12, lsize=10)
        f.rect(x + 4, 118, 130, 26, 'warn', r=4)
        f.text(x + 69, 135, err, 9.5, kind='warn', mono=True)
        f.line(x + 69, 101, x + 69, 116, 'warn', sw=1.1)
        if i:
            f.line(x - 12, 73, x - 2, 73, 'srch', sw=1.4)
    f.box(20, 162, 740, 50, 'ToolResult(ok, data | error_kind, error, seconds, args)',
          ['инструмент никогда не бросает исключение в граф: граф получает результат и решает, как деградировать'],
          'file', tsize=11.5, lsize=10)
    return f.render()


def research_runs() -> str:
    return gbars('20 research-задач: успех и внешний источник в четырёх прогонах',
                 [('A: веб недоступен', [19, 0]), ('B: Википедия\nпо всей фразе', [19, 9]),
                  ('C: по «ключам»\n(промпт v1)', [19, 9]), ('D: ключи v2 +\nтекст статьи', [20, 18])],
                 [('ответ верный, с цитатами', 'ing'), ('внешний источник дошёл до ответа', 'srch')], 20,
                 fmt='{:.0f}', height=290)
