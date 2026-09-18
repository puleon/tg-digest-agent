from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.collector.conftest import FakeSource, no_sleep
from tgdigest.collector.config import ChannelsConfig, load_channels_config
from tgdigest.collector.sync import upsert_channels
from tgdigest.collector.types import ChannelInfo, FloodWait
from tgdigest.db.models import Channel

YAML = """
topics:
  scifi:
    title: SF
    channels:
      - username: "@lem_fans"
      - username: https://t.me/strugatsky
        source: forward
  humor:
    channels: []
  cinema:
    channels:
      - username: horror_stills
"""


def test_config_normalizes_usernames(tmp_path: Path) -> None:
    (tmp_path / "c.yaml").write_text(YAML)
    cfg = load_channels_config(tmp_path / "c.yaml")
    assert [(t, c.username, c.source) for t, c in cfg.flat()] == [
        ("scifi", "lem_fans", "manual"),
        ("scifi", "strugatsky", "forward"),
        ("cinema", "horror_stills", "manual"),
    ]


def test_config_rejects_unknown_topic() -> None:
    with pytest.raises(ValueError):
        ChannelsConfig.model_validate({"topics": {"sports": {"channels": []}}})


async def test_upsert_channels_creates_then_refreshes(
    factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    (tmp_path / "c.yaml").write_text(YAML)
    cfg = load_channels_config(tmp_path / "c.yaml")
    src = FakeSource()
    src.channels = {
        "lem_fans": ChannelInfo(1, "lem_fans", "Lem", 1200),
        "strugatsky": ChannelInfo(2, "strugatsky", "ABS", 800),
        "horror_stills": ChannelInfo(3, "horror_stills", "Horror", None),
    }
    await upsert_channels(factory, src, cfg, sleep=no_sleep)
    src.channels["lem_fans"] = ChannelInfo(1, "lem_fans", "Lem (renamed)", 1300)
    await upsert_channels(factory, src, cfg, sleep=no_sleep)

    async with factory() as s:
        rows = {c.id: c for c in (await s.execute(select(Channel))).scalars()}
    assert len(rows) == 3
    assert (rows[1].title, rows[1].subscribers, rows[1].topic) == ("Lem (renamed)", 1300, "scifi")
    assert rows[2].source == "forward" and rows[3].topic == "cinema"


async def test_upsert_channels_survives_flood_wait(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    cfg = ChannelsConfig.model_validate({"topics": {"humor": {"channels": [{"username": "x"}]}}})

    class Flaky(FakeSource):
        async def resolve_channel(self, username: str) -> ChannelInfo:
            self.resolve_calls += 1
            if self.resolve_calls == 1:
                raise FloodWait(5)
            return ChannelInfo(9, username, "X")

    src = Flaky()
    rows = await upsert_channels(factory, src, cfg, sleep=no_sleep)
    assert [r.id for r in rows] == [9] and src.resolve_calls == 2
