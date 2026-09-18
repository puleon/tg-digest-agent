from __future__ import annotations

from tgdigest.ingest.normalize import dedup_key, extract_mentions, extract_urls, normalize_text


def test_normalize_strips_invisible_chars_quotes_and_whitespace() -> None:
    raw = "Пере​читал   «Солярис»\r\n\r\n\n🔥🔥🔥\n  — не о контакте "
    assert normalize_text(raw) == 'Перечитал "Солярис"\n— не о контакте'


def test_normalize_is_idempotent_and_handles_empty() -> None:
    assert normalize_text("") == "" and normalize_text(None) == ""  # type: ignore[arg-type]
    once = normalize_text("a  b\n\nc")
    assert normalize_text(once) == once == "a b\nc"


def test_extract_urls_and_mentions() -> None:
    text = "см. https://example.com/a?x=1. и www.kino.ru/films, автор @lem_fans (не @ab)"
    assert extract_urls(text) == ["https://example.com/a?x=1", "www.kino.ru/films"]
    assert extract_mentions(text) == ["lem_fans"]


def test_dedup_key_ignores_case_links_and_punctuation() -> None:
    a = "Когда дедлайн был ВЧЕРА!!! https://t.me/x @memes"
    b = "когда дедлайн был вчера"
    assert dedup_key(a) == dedup_key(b) == "когда дедлайн был вчера"
