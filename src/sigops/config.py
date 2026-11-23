"""Load feed subscriptions from a TOML file."""

from __future__ import annotations

import tomllib
from pathlib import Path

from sigops.models import FeedSource


class ConfigError(ValueError):
    """Raised when the config file is missing or malformed."""


def load_feeds(path: Path) -> list[FeedSource]:
    """Parse `feeds.toml` and return one FeedSource per `[feeds.<id>]` table.

    Each table must have a non-empty `url`. The table key becomes `feed_id`.
    Raises ConfigError if the file is absent, is not valid TOML, has no
    `feeds` table, or a table lacks `url`.
    """

    if not path.exists():
        raise ConfigError(f"Config file {path} not found")

    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"Config file {path} is not valid TOML") from e

    feeds_table = data.get("feeds")
    if not isinstance(feeds_table, dict):
        raise ConfigError(f"Config file {path} is missing the 'feeds' table")

    feeds: list[FeedSource] = []
    for feed_id, table in feeds_table.items():
        if not isinstance(table, dict) or not table.get("url"):
            raise ConfigError(
                f"Config file {path} is malformed: "
                f"[feeds.{feed_id}] must have a non-empty 'url'"
            )
        try:
            feeds.append(FeedSource(feed_id=feed_id, **table))
        except (TypeError, ValueError) as e:
            raise ConfigError(
                f"Config file {path} is malformed: [feeds.{feed_id}]"
            ) from e
    return feeds
