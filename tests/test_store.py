"""Tests for the seen-state store."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sigops.models import Article, FeedFailure, IngestionResult
from sigops.store import SeenStore, StoreError


def _article(feed_id: str, article_id: str) -> Article:
    return Article(
        feed_id=feed_id, article_id=article_id, title="T", link=f"https://e.com/{article_id}"
    )


def test_missing_file_starts_empty(tmp_path: Path) -> None:
    store = SeenStore(tmp_path / "seen.json")
    assert store.filter_new([_article("a", "1")]) == [_article("a", "1")]


def test_does_not_create_file_until_something_is_seen(tmp_path: Path) -> None:
    path = tmp_path / "seen.json"
    SeenStore(path)
    assert not path.exists()


def test_mark_seen_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "seen.json"
    SeenStore(path).mark_seen([_article("a", "1")])

    reloaded = SeenStore(path)
    assert reloaded.filter_new([_article("a", "1")]) == []


def test_persists_as_readable_json(tmp_path: Path) -> None:
    path = tmp_path / "seen.json"
    SeenStore(path).mark_seen([_article("a", "2"), _article("a", "1")])

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"a": ["1", "2"]}


def test_ids_are_scoped_per_feed(tmp_path: Path) -> None:
    store = SeenStore(tmp_path / "seen.json")
    store.mark_seen([_article("a", "1")])
    assert store.filter_new([_article("b", "1")]) == [_article("b", "1")]


def test_filter_new_keeps_only_unseen(tmp_path: Path) -> None:
    store = SeenStore(tmp_path / "seen.json")
    store.mark_seen([_article("a", "1")])
    fresh = store.filter_new([_article("a", "1"), _article("a", "2")])
    assert fresh == [_article("a", "2")]


def test_mark_seen_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "seen.json"
    store = SeenStore(path)
    store.mark_seen([_article("a", "1")])
    store.mark_seen([_article("a", "1")])
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": ["1"]}


def test_empty_mark_seen_writes_nothing(tmp_path: Path) -> None:
    path = tmp_path / "seen.json"
    SeenStore(path).mark_seen([])
    assert not path.exists()


def test_malformed_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "seen.json"
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(StoreError):
        SeenStore(path)


def test_non_object_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "seen.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(StoreError):
        SeenStore(path)


def test_malformed_feed_entry_raises(tmp_path: Path) -> None:
    path = tmp_path / "seen.json"
    path.write_text('{"a": "not-a-list"}', encoding="utf-8")
    with pytest.raises(StoreError):
        SeenStore(path)


# --- dedupe -------------------------------------------------------------


def _result(*ids: str, failures: list[FeedFailure] | None = None) -> IngestionResult:
    return IngestionResult(
        articles=[_article("a", i) for i in ids],
        failures=failures or [],
    )


def test_dedupe_returns_only_unseen_and_records_them(tmp_path: Path) -> None:
    store = SeenStore(tmp_path / "seen.json")
    store.mark_seen([_article("a", "1")])

    out = store.dedupe(_result("1", "2"))

    assert [a.article_id for a in out.articles] == ["2"]
    assert store.filter_new([_article("a", "2")]) == []


def test_dedupe_passes_failures_through(tmp_path: Path) -> None:
    store = SeenStore(tmp_path / "seen.json")
    failure = FeedFailure(feed_id="a", url="https://e.com/a", reason="boom")

    out = store.dedupe(_result("1", failures=[failure]))

    assert out.failures == [failure]
    assert [a.article_id for a in out.articles] == ["1"]


def test_dedupe_second_run_is_empty(tmp_path: Path) -> None:
    store = SeenStore(tmp_path / "seen.json")
    store.dedupe(_result("1"))

    # A fresh store instance stands in for a new process.
    second = SeenStore(tmp_path / "seen.json").dedupe(_result("1"))

    assert second.articles == []
    assert second.failures == []


def test_dedupe_force_returns_seen_without_losing_state(tmp_path: Path) -> None:
    store = SeenStore(tmp_path / "seen.json")
    store.mark_seen([_article("a", "1")])

    out = store.dedupe(_result("1"), force=True)

    assert [a.article_id for a in out.articles] == ["1"]
    assert store.filter_new([_article("a", "1")]) == []
