"""Chapter 2: the journey of one post."""
from figs import Fig


def post_journey() -> str:
    f = Fig(760, 600)
    f.text(20, 20, 'Пост 8648 (@am_devs, 4 мая 2026): что о нём знает система после каждого шага', 12.5, 'bold',
           anchor='start')
    stages = [
        ('1', 'Коллектор', '19.09, 15:15 UTC', 'col',
         ['id 8648 · канал am_devs · сообщение 49719', 'text: ""  (пустой — только картинка)',
          'photo → 175/7867690264049983175.jpg', 'views 9 350 · реакций 255']),
        ('2', 'Модель зрения', 'Gemma 4, 21.09', 'ing',
         ['ocr_text: "РЫБА\\nПИВО\\nКОШКА"', 'caption: "A humorous sign on a door lists fish,',
          '  beer, and a cat, implying the cat is also for sale…"']),
        ('3', 'Классификатор', 'Qwen3.6, 21.09', 'ing',
         ['{"topic": "humor", "is_ad": false,', ' "is_spoiler": false, "quality": 4}',
          'injection_flag: false']),
        ('4', 'Дедупликация', 'хэши, без модели', 'ing',
         ['sha256 файла: ed08618e…a3d732', 'pHash: 0x68a69c629f389729',
          'похожих нет → свой кластер не нужен']),
        ('5', 'Индексатор', 'BGE-M3 → Qdrant', 'srch',
         ['4 варианта текста × (dense 1024 + sparse)', 'payload: тема, дата, канал, is_ad, качество…',
          'content_hash 3a27a874…, meta_hash 79887b06…']),
        ('6', 'Поисковый агент', 'по вашему запросу', 'srch',
         ['«что-нибудь смешное про котов»', '→ найден гибридным поиском, оценён как', '  релевантный → [post 8648] в ответе']),
    ]
    for i, (n, title, sub, k, lines) in enumerate(stages):
        y = 40 + i * 92
        f.rect(20, y, 190, 80, k, r=10)
        f.circle(42, y + 22, 12, k, fill=('#0284c7' if k == 'col' else '#059669' if k == 'ing' else '#4f46e5'))
        f.text(42, y + 26.5, n, 12, 'bold', color='#ffffff')
        f.text(62, y + 27, title, 13, 'bold', kind=k, anchor='start')
        f.text(36, y + 52, sub, 10.5, kind=k, anchor='start', italic=True)
        f.rect(228, y, 512, 80, 'file', r=8, fill='#f8fafc')
        for j, ln in enumerate(lines):
            f.text(242, y + 20 + j * 16, ln, 10.5, anchor='start', mono=True, color='#1e293b')
        f.line(210, y + 40, 226, y + 40, k, head=False, sw=1.4)
        if i < len(stages) - 1:
            f.line(115, y + 80, 115, y + 90, 'ext', sw=1.4)
    return f.render()
