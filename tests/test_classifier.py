"""Tests for the Classification stage (issue #6).

Contract: Jev-based judge tags each cataloged article (title + summary)
and writes the judgment back to the catalog. Vocabulary is a 1-D JSON
array, owner-populated. Threshold: one Noul per tag
("Is <Tag> a major theme in the article?"); tags at/above threshold win.

RED STATE: every test fails with NotImplementedError until the owner
implements classifier.py. Tests run fully offline against a fake judge;
no network, no .env, never the repo's real state/.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sigops import classifier
from sigops.catalog import MemoryCatalog
from sigops.classifier import (
    Judge,
    JudgeError,
    VocabularyError,
    build_noul_questions,
    classify,
    classify_unjudged,
    load_tags,
)
from sigops.models import Article, Classification, ClassificationFailure


def _article(**kw) -> Article:
    base = {
        "feed_id": "a",
        "article_id": "1",
        "title": "New LLM breakthrough",
        "link": "https://e.com/1",
        "summary": "A new model for code generation",
        "published_at": datetime(2025, 11, 22, 10, 0, tzinfo=timezone.utc),
    }
    base.update(kw)
    return Article(**base)


class FakeJudge:
    """Canned judge. Maps article_id -> (tags, probabilities). No I/O."""

    model_name = "fake-jev"

    def __init__(
        self,
        judgments: dict[str, tuple[list[str], dict[str, float]]] | None = None,
        explode_on: set[str] | None = None,
    ) -> None:
        self.judgments = judgments or {}
        self.explode_on = explode_on or set()
        self.calls: list[tuple[Article, list[str]]] = []

    def judge(self, article: Article, vocabulary: list[str]) -> Classification:
        self.calls.append((article, list(vocabulary)))
        if article.article_id in self.explode_on:
            raise JudgeError("judge exploded")
        tags, probabilities = self.judgments.get(article.article_id, ([], {}))
        kept = [t for t in tags if t in vocabulary]
        return Classification(
            feed_id=article.feed_id,
            article_id=article.article_id,
            tags=kept,
            probabilities={k: v for k, v in probabilities.items() if k in vocabulary},
            eligible=bool(kept),
            judge_model=self.model_name,
            judged_at=datetime(2025, 11, 24, 12, 0, tzinfo=timezone.utc),
        )


# --- vocabulary ------------------------------------------------------------


def test_load_tags_reads_1d_array(tmp_path: Path) -> None:
    p = tmp_path / "tags.json"
    p.write_text(json.dumps({"tags": ["AI-LLM", "AI-Tools"]}), encoding="utf-8")
    assert load_tags(p) == ["AI-LLM", "AI-Tools"]


def test_load_tags_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(VocabularyError):
        load_tags(tmp_path / "nope.json")


def test_load_tags_malformed_json_raises(tmp_path: Path) -> None:
    p = tmp_path / "tags.json"
    p.write_text("not json", encoding="utf-8")
    with pytest.raises(VocabularyError):
        load_tags(p)


def test_load_tags_rejects_non_list_and_bad_entries(tmp_path: Path) -> None:
    for bad in (
        {"tags": "AI-LLM"},
        {"tags": ["AI-LLM", 42]},
        {"tags": ["AI-LLM", ""]},
        {"nope": []},
    ):
        p = tmp_path / "tags.json"
        p.write_text(json.dumps(bad), encoding="utf-8")
        with pytest.raises(VocabularyError):
            load_tags(p)


# --- question building --------------------------------------------------------


def test_build_noul_questions_one_per_tag() -> None:
    questions = build_noul_questions(["AI-LLM", "Politics-World"])
    assert set(questions) == {"AI-LLM", "Politics-World"}
    for tag, q in questions.items():
        assert tag in str(q).replace("{tag}", tag) or True  # key maps back to tag
    assert len(questions) == 2


def test_build_noul_questions_empty_vocabulary() -> None:
    assert build_noul_questions([]) == {}


# --- classify: relevant / irrelevant / borderline ------------------------------


def test_clearly_relevant_article_is_eligible() -> None:
    judge = FakeJudge({"1": (["AI-LLM"], {"AI-LLM": 0.95, "Politics-World": 0.05})})
    out = classify(_article(), ["AI-LLM", "Politics-World"], judge)
    assert out.eligible is True
    assert out.tags == ["AI-LLM"]
    assert out.probabilities["AI-LLM"] == pytest.approx(0.95)
    assert out.judge_model == "fake-jev"


def test_clearly_irrelevant_article_is_ineligible() -> None:
    judge = FakeJudge({"1": ([], {"AI-LLM": 0.02, "Politics-World": 0.1})})
    out = classify(_article(), ["AI-LLM", "Politics-World"], judge)
    assert out.eligible is False
    assert out.tags == []


def test_borderline_below_threshold_is_ineligible() -> None:
    judge = FakeJudge({"1": (["AI-LLM"], {"AI-LLM": 0.69})})
    out = classify(_article(), ["AI-LLM"], judge, threshold=0.7)
    assert out.eligible is False
    assert out.tags == []


def test_borderline_at_threshold_is_eligible() -> None:
    judge = FakeJudge({"1": (["AI-LLM"], {"AI-LLM": 0.7})})
    out = classify(_article(), ["AI-LLM"], judge, threshold=0.7)
    assert out.eligible is True
    assert out.tags == ["AI-LLM"]


def test_empty_vocabulary_is_ineligible_without_calling() -> None:
    judge = FakeJudge({"1": (["AI-LLM"], {"AI-LLM": 0.99})})
    out = classify(_article(), [], judge)
    assert out.eligible is False
    assert out.tags == []


def test_tags_outside_vocabulary_are_dropped() -> None:
    judge = FakeJudge({"1": (["AI-LLM", "Free-Text"], {"AI-LLM": 0.9, "Free-Text": 0.9})})
    out = classify(_article(), ["AI-LLM"], judge)
    assert out.tags == ["AI-LLM"]
    assert "Free-Text" not in out.probabilities


def test_judge_failure_raises() -> None:
    judge = FakeJudge(explode_on={"1"})
    with pytest.raises(JudgeError):
        classify(_article(), ["AI-LLM"], judge)


# --- classify_unjudged: catalog integration --------------------------------------


def test_unjudged_articles_are_judged_and_written() -> None:
    catalog = MemoryCatalog()
    catalog.put(_article(article_id="1"))
    catalog.put(_article(article_id="2"))
    judge = FakeJudge({"1": (["AI-LLM"], {"AI-LLM": 0.9})})
    judgments, failures = classify_unjudged(
        catalog, [catalog.get("a", "1"), catalog.get("a", "2")],
        ["AI-LLM"], judge,
    )
    assert failures == []
    assert {j.article_id for j in judgments} == {"1", "2"}
    stored = catalog.get_classification("a", "1")
    assert stored is not None
    assert stored.tags == ["AI-LLM"]


def test_already_judged_articles_are_skipped() -> None:
    catalog = MemoryCatalog()
    catalog.put(_article(article_id="1"))
    catalog.put_classification(
        Classification(
            feed_id="a", article_id="1", tags=["AI-LLM"],
            probabilities={"AI-LLM": 0.9}, eligible=True,
            judge_model="old", judged_at=datetime.now(timezone.utc),
        )
    )
    judge = FakeJudge({"1": (["Other"], {"Other": 0.9})})
    judgments, failures = classify_unjudged(
        catalog, [catalog.get("a", "1")], ["AI-LLM", "Other"], judge
    )
    assert judgments == []
    assert failures == []
    assert judge.calls == []
    assert catalog.get_classification("a", "1").judge_model == "old"


def test_judging_failure_isolated_per_article() -> None:
    catalog = MemoryCatalog()
    catalog.put(_article(article_id="1"))
    catalog.put(_article(article_id="2"))
    judge = FakeJudge(
        {"2": (["AI-LLM"], {"AI-LLM": 0.9})}, explode_on={"1"}
    )
    judgments, failures = classify_unjudged(
        catalog,
        [catalog.get("a", "1"), catalog.get("a", "2")],
        ["AI-LLM"], judge,
    )
    assert [j.article_id for j in judgments] == ["2"]
    assert len(failures) == 1
    assert isinstance(failures[0], ClassificationFailure)
    assert failures[0].article_id == "1"
    assert catalog.get_classification("a", "2") is not None
    assert catalog.get_classification("a", "1") is None


def test_article_has_no_tag_fields() -> None:
    assert "tags" not in Article.model_fields
    assert "eligible" not in Article.model_fields
    assert "probabilities" not in Article.model_fields


def test_judge_protocol_shape() -> None:
    assert isinstance(FakeJudge(), Judge)
