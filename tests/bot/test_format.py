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
    render_search,
    search_keyboard,
)


def test_feedback_callbacks_round_trip_within_64_bytes() -> None:
    trace = "0123456789abcdef0123456789abcdef"
    for fb in (
        Feedback("like", 123456, "search", trace_id=trace),
        Feedback("dislike", 1, "search", trace_id=None),
        Feedback("save", 987654321, "digest", digest_id=42),
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
    assert "1. @memes · 2026-09-01" in text and text.endswith("3.2 с</i>")


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
