from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tgdigest.api.app import explain
from tgdigest.service import Services


def test_health_and_metrics_endpoints(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code in (200, 503) and set(r.json()["checks"]) == {
        "postgres",
        "qdrant",
        "llm",
        "langfuse",
    }
    m = client.get("/metrics")
    assert m.status_code == 200 and "tgdigest_requests_total" in m.text
    assert 'endpoint="health"' in m.text and "# TYPE tgdigest_request_seconds summary" in m.text


def test_search_returns_answer_citations_and_posts(client: TestClient) -> None:
    r = client.post("/search", json={"query": "мем про кота", "user_id": 5})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "search" and body["answer"].startswith("Вот что нашлось")
    assert body["citations"] and {p["post_id"] for p in body["posts"]} == set(body["citations"])
    assert (
        body["posts"][0]["url"].startswith("https://t.me/") and body["usage"]["prompt_tokens"] > 0
    )
    assert client.post("/search", json={"query": ""}).status_code == 422
    assert "tgdigest_llm_tokens_total" in client.get("/metrics").text


def test_digest_why_and_feedback_round_trip(client: TestClient, services: Services) -> None:
    r = client.post("/digest", json={"user_id": 9, "minutes": 5, "days": 8})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["digest_id"] and len(d["items"]) >= 3 and d["critic_iterations"] == 1
    assert {it["topic"] for it in d["items"]} == {"humor", "scifi", "cinema"}
    assert "score_parts" in d["items"][0]

    latest = client.get("/digest/latest", params={"user_id": 9}).json()
    assert latest["digest_id"] == d["digest_id"] and latest["items"] == d["items"]
    assert client.get("/digest/latest", params={"user_id": 404}).status_code == 404
    assert client.get(f"/digest/{d['digest_id']}").json()["plan"]["items"] == 5

    post_id = d["items"][0]["post_id"]
    why = client.get(f"/why/{d['digest_id']}/{post_id}").json()
    assert why["post_id"] == post_id and "балл" in why["explanation"] and why["score_parts"]
    assert client.get(f"/why/{d['digest_id']}/999999").status_code == 404

    fb = client.post(
        "/feedback",
        json={
            "user_id": 9,
            "post_id": post_id,
            "signal": "like",
            "context": "digest",
            "digest_id": d["digest_id"],
        },
    )
    assert fb.status_code == 200 and fb.json()["id"] >= 1
    assert fb.json()["scored"] is False  # the fake tracer gave the digest no trace id
    fb2 = client.post(
        "/feedback",
        json={
            "user_id": 9,
            "post_id": post_id,
            "signal": "dislike",
            "context": "search",
            "trace_id": "trace-xyz",
        },
    )
    tracer: Any = services.tracer
    assert fb2.json()["scored"] is True and tracer.scores[-1] == (
        "trace-xyz",
        "user_feedback",
        -1.0,
    )
    assert (
        client.post(
            "/feedback", json={"user_id": 9, "post_id": post_id, "signal": "meh"}
        ).status_code
        == 422
    )
    assert 'signal="like"' in client.get("/metrics").text

    prof = client.post("/profile/9/rebuild").json()
    assert prof["votes"] == 1 and prof["like_rate"] == 0.0  # the later dislike superseded the like
    assert client.get("/profile/9").json()["votes"] == 1 and client.get("/profile/77").json()["new"]

    second = client.post("/digest", json={"user_id": 9, "minutes": 5, "days": 8}).json()
    assert not {it["post_id"] for it in d["items"]} & {it["post_id"] for it in second["items"]}


def test_schedule_and_post_endpoints(client: TestClient) -> None:
    assert client.get("/users/scheduled").json() == []
    assert client.post("/profile/3/schedule", json={"hour": 9}).json()["digest_hour"] == 9
    assert client.get("/users/scheduled").json() == [{"user_id": 3, "digest_hour": 9}]
    assert client.post("/profile/3/schedule", json={"hour": None}).json()["digest_hour"] is None
    assert client.get("/users/scheduled").json() == []
    assert client.post("/profile/3/schedule", json={"hour": 25}).status_code == 422
    p = client.get("/post/5").json()
    assert p["channel"] == "sf" and p["url"] == "https://t.me/sf/105"
    assert client.get("/post/12345").status_code == 404


def test_explain_reads_the_score_components() -> None:
    item = {
        "topic": "humor",
        "channel": "memes",
        "fresh": True,
        "score": 0.81,
        "score_parts": {"similarity": 0.9, "topic": 0.9, "channel": 0.7, "engagement": 0.8},
    }
    text = explain(item, {"per_topic": {"humor": 4}})
    assert (
        text.startswith("Итоговый балл 0.81.") and "очень похоже" in text and "в приоритете" in text
    )
    assert "популярнее" in text and "4 мест" in text and "свежий" in text
    assert "не сохранены" in explain({"topic": "humor"}, {})
