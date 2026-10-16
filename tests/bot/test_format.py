from __future__ import annotations

from tgdigest.bot.format import (
    Feedback,
    decode_feedback,
    decode_why,
    digest_keyboard,
    encode_feedback,
    encode_why,
    render_digest,
    render_profile,
    render_saved,
    render_search,
    saved_keyboard,
    search_keyboard,
)


def test_feedback_callbacks_round_trip_within_64_bytes() -> None:
    trace = "0123456789abcdef0123456789abcdef"
    for fb in (
        Feedback("like", 123456, "search", trace_id=trace),
        Feedback("dislike", 1, "search", trace_id=None),
        Feedback("save", 987654321, "digest", digest_id=42),
        Feedback("unsave", 5, "saved"),
    ):
        data = encode_feedback(fb)
        assert len(data.encode()) <= 64 and decode_feedback(data) == fb
    assert decode_feedback("fb:x:1:s:-") is None and decode_feedback("nope") is None
    assert decode_feedback("fb:l:abc:d:1") is None
    assert decode_why(encode_why(7, 99)) == (7, 99) and decode_why("why:a:b") is None


def test_keyboards_have_a_row_per_post_with_the_signals() -> None:
    kb = search_keyboard([{"post_id": 1}, {"post_id": 2}], "trace")
    assert kb is not None and len(kb.inline_keyboard) == 2
    assert [b.text for b in kb.inline_keyboard[0]] == ["1", "👍", "👎", "💾"]
    assert kb.inline_keyboard[1][1].callback_data == "fb:l:2:s:trace"
    dk = digest_keyboard(5, [{"post_id": 10}])
    assert dk is not None and [b.text for b in dk.inline_keyboard[0]] == [
        "1",
        "👍",
        "👎",
        "💾",
        "❓",
    ]
    assert dk.inline_keyboard[0][-1].callback_data == "why:5:10"
    assert search_keyboard([], None) is None


def test_render_search_links_citations_and_escapes_html() -> None:
    body = {
        "answer": "Вот [post 7] и <b>не тег</b> [post 9] [post 11]",
        "posts": [
            {
                "post_id": 7,
                "channel": "memes",
                "date": "2026-09-01",
                "url": "https://t.me/memes/107",
                "text": "Когда   дедлайн <завтра>, а кот\nуже спит на клавиатуре"
                " и не собирается уходить",
            },
            {"post_id": 9, "channel": "sf", "date": "2026-09-02", "url": None},
        ],
        "mode": "search",
        "iterations": 1,
        "seconds": 3.2,
    }
    text = render_search(body)
    assert '<a href="https://t.me/memes/107">[1]</a>' in text and "[2]" in text
    assert "&lt;b&gt;не тег&lt;/b&gt;" in text and "[post 11]" in text
    first = text.split("\n\n")[1].splitlines()[0]  # whitespace collapsed, escaped, cut to 60
    assert first.startswith('1. <a href="https://t.me/memes/107">Когда дедлайн &lt;завтра&gt;, а')
    assert first.endswith("…</a> — memes, 2026-09-01")
    assert "2. пост без текста — sf, 2026-09-02" in text  # no url: no link at all
    assert "@memes" not in text and text.endswith("3.2 с</i>")


def test_render_digest_and_profile() -> None:
    body = {
        "intro": "3 пункта",
        "items": [
            {
                "post_id": 1,
                "title": "Кот & дедлайн",
                "why": "смешно",
                "topic": "humor",
                "channel": "memes",
                "url": "https://t.me/memes/1",
            },
            {"post_id": 2, "title": None, "why": "", "topic": "scifi", "channel": "sf", "url": ""},
        ],
        "gaps": {"cinema": 1},
    }
    text = render_digest(body)
    assert (
        text.startswith("<b>3 пункта</b>")
        and 'href="https://t.me/memes/1">Кот &amp; дедлайн</a>' in text
    )
    assert "2. post 2" in text and "не хватило кандидатов" in text
    assert render_digest({"items": []}).startswith("Пусто")
    assert "Профиля ещё нет" in render_profile({"new": True})
    prof = render_profile(
        {
            "topic_weights": {"humor": 0.6, "scifi": 0.4},
            "votes": 12,
            "like_rate": 0.75,
            "interest_centroids": 2,
            "negative_prefs": ["<spam>"],
        }
    )
    assert "humor 0.60, scifi 0.40" in prof and "&lt;spam&gt;" in prof and "оценок: 12" in prof


def test_render_search_turns_the_models_markdown_into_html() -> None:
    body = {
        "answer": "Нашлось:\n\n*   **Пост о Гослинге**: фильм про космос [post 1]\n- второй пункт",
        "posts": [{"post_id": 1, "channel": "k", "date": "2026-04-14", "url": "https://t.me/k/1"}],
        "mode": "search",
        "iterations": 1,
        "seconds": 1.0,
    }
    text = render_search(body)
    assert '• <b>Пост о Гослинге</b>: фильм про космос <a href="https://t.me/k/1">[1]</a>' in text
    assert "\n• второй пункт" in text and "*" not in text


def test_saved_list_links_posts_and_offers_removal() -> None:
    items = [
        {
            "post_id": 30 + i,
            "channel": "k",
            "date": "2026-09-0" + str(i),
            "text": f"пост {i}",
            "url": f"https://t.me/k/{i}",
        }
        for i in range(1, 6)
    ]
    text = render_saved({"total": 7, "items": items})
    assert text.startswith("<b>Сохранённое: 7</b> <i>(последние 5)</i>")
    assert '3. <a href="https://t.me/k/3">пост 3</a> — k, 2026-09-03' in text
    kb = saved_keyboard(items)
    assert kb is not None and [len(r) for r in kb.inline_keyboard] == [4, 1]
    assert kb.inline_keyboard[1][0].text == "✕ 5"
    assert decode_feedback(kb.inline_keyboard[0][0].callback_data or "") == Feedback(
        "unsave", 31, "saved"
    )
    assert "Сохранённых постов нет" in render_saved({"total": 0, "items": []})
    assert saved_keyboard([]) is None
