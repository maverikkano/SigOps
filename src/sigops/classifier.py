"""Classification stage.

Decides, per article, whether it is of interest, and attaches the tags
that say why. The judgment comes from title + description alone
(classification runs before the body is fetched).

Decisions:
- Judge is Jev (TypeSafe System One) via the `typesafe-sdk` package.
  Credentials (BaseURL + API key) come from a `.env` file; `.env.example`
  is the template.
- Vocabulary is a 1-D JSON array of tag strings, owner-populated
  (e.g. ["AI-LLM", "AI-Tools", ...]).
- Threshold: one Noul question per tag — "Is <Tag> a major theme in the
  article?" — bundled in a single request; returned tags are the ones
  at/above threshold. Code owns vocabulary, threshold, branching.

Failure isolation: one article's judging/write failure never aborts
the run; it becomes a ClassificationFailure.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

from dotenv import load_dotenv

from sigops.catalog import Catalog, CatalogError
from sigops.models import Article, Classification, ClassificationFailure

load_dotenv()

DEFAULT_TAGS_PATH = Path(__file__).resolve().parents[2] / "config" / "tags.json"
"""Default vocabulary file. Owner-populated; changing it needs no code change."""

DEFAULT_MODEL = os.environ.get("TYPESAFE_MODEL", "jev-latest")
"""Jev model used when the caller does not pick one."""


class VocabularyError(ValueError):
    """Raised when the tags file is missing or malformed."""


class JudgeError(RuntimeError):
    """Raised when the judge cannot be reached or returns unusable output."""


class ClassifierError(RuntimeError):
    """Raised when a judgment cannot be recorded in the catalog."""


@runtime_checkable
class Judge(Protocol):
    """Semantic judge. The model supplies the judgment; code owns the rest.

    The Jev implementation asks one Noul per tag in a single request.
    The fake used in tests returns canned judgments with no I/O.
    """

    @property
    def model_name(self) -> str:
        """Judge identifier recorded as Classification.judge_model."""
        ...

    def judge(
        self, article: Article, vocabulary: list[str]
    ) -> Classification:
        """Return a judgment for `article` over `vocabulary`.

        Input signal is title + description only. Tags must be drawn from
        `vocabulary`. Raises JudgeError on transport or decoding failure.
        Must not raise on an empty vocabulary (returns ineligible).
        """
        ...


def load_tags(path: Path | str | None = None) -> list[str]:
    """Load the tag vocabulary from a 1-D JSON array file.

    Owner-populated; changing it needs no code change. Raises
    VocabularyError when missing, invalid JSON, not a list, or any
    entry is not a non-empty string.
    """
    p = Path(path) if path is not None else DEFAULT_TAGS_PATH
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        raise VocabularyError(f"tags file {p} cannot be read: {e}") from e
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise VocabularyError(f"tags file {p} is not valid JSON: {e}") from e
    if isinstance(data, dict):
        if "tags" not in data:
            raise VocabularyError(f"tags file {p} must contain a 'tags' key")
        data = data["tags"]
    if not isinstance(data, list) or any(
        not isinstance(t, str) or not t for t in data
    ):
        raise VocabularyError(
            f"tags file {p} must be a list of non-empty strings"
        )
    return list(data)


def build_noul_questions(
    vocabulary: list[str], per_tag_instructions: str = "Is {tag} a major theme in the article?"
) -> dict:
    """Build one Noul question per tag for a single Jev request.

    `{tag}` in `per_tag_instructions` is filled per tag. Keys are the
    tag strings so answers map back directly. Pure function: no I/O,
    deterministic, vocabulary order preserved.
    """
    return {tag: per_tag_instructions.replace("{tag}", tag) for tag in vocabulary}


class JevJudge:
    """Judge backed by Jev (TypeSafe System One) via `typesafe-sdk`.

    Asks one Noul per tag in a single request over title + summary.
    Credentials come from the environment (`TYPESAFE_API_KEY`,
    `TYPESAFE_BASE_URL`), loaded from `.env` at import time.
    """

    def __init__(self, model: str | None = None) -> None:
        """Bind the Jev model name. Defaults to TYPESAFE_MODEL or jev-latest."""
        self._model = model or DEFAULT_MODEL

    @property
    def model_name(self) -> str:
        """Judge identifier recorded as Classification.judge_model."""
        return self._model

    def judge(
        self, article: Article, vocabulary: list[str]
    ) -> Classification:
        """Return a judgment for `article` over `vocabulary`.

        Input signal is title + description only. Tags must be drawn from
        `vocabulary`. Raises JudgeError on transport or decoding failure.
        Must not raise on an empty vocabulary (returns ineligible).
        """
        if not vocabulary:
            return Classification(
                feed_id=article.feed_id,
                article_id=article.article_id,
                tags=[],
                probabilities={},
                eligible=False,
                judge_model=self._model,
                judged_at=datetime.now(timezone.utc),
            )
        try:
            from typesafe_sdk import Noul, TypeSafeClient, TypeSafeError
        except ImportError as e:
            raise JudgeError(f"typesafe-sdk is not installed: {e}") from e
        state = f"Title: {article.title}\nSummary: {article.summary}"
        questions = {
            tag: Noul(instructions=instruction)
            for tag, instruction in build_noul_questions(vocabulary).items()
        }
        try:
            with TypeSafeClient() as client:
                response = client.system_one(
                    state=state, questions=questions, model=self._model
                )
            probabilities = {
                tag: response.nouls[tag].noul for tag in vocabulary
            }
        except (TypeSafeError, KeyError, AttributeError, ValueError) as e:
            raise JudgeError(f"Jev request failed: {e}") from e
        except Exception as e:
            raise JudgeError(f"Jev request failed: {e}") from e
        model_name = getattr(response, "model", None) or self._model
        return Classification(
            feed_id=article.feed_id,
            article_id=article.article_id,
            tags=list(vocabulary),
            probabilities=probabilities,
            eligible=False,
            judge_model=model_name,
            judged_at=datetime.now(timezone.utc),
        )


def classify(
    article: Article,
    vocabulary: list[str],
    judge: Judge,
    *,
    threshold: float = 0.7,
) -> Classification:
    """Judge one article. Tags at/above `threshold` are kept.

    eligible = at least one tag kept. eligible=False on empty
    vocabulary. Raises JudgeError when the judge fails.
    """
    if not vocabulary:
        return Classification(
            feed_id=article.feed_id,
            article_id=article.article_id,
            tags=[],
            probabilities={},
            eligible=False,
            judge_model=judge.model_name,
            judged_at=datetime.now(timezone.utc),
        )
    judgment = judge.judge(article, vocabulary)
    kept = [
        tag
        for tag in judgment.tags
        if tag in vocabulary and judgment.probabilities.get(tag, 0) >= threshold
    ]
    return Classification(
        feed_id=article.feed_id,
        article_id=article.article_id,
        tags=kept,
        probabilities={
            tag: prob
            for tag, prob in judgment.probabilities.items()
            if tag in vocabulary
        },
        eligible=bool(kept),
        judge_model=judgment.judge_model or judge.model_name,
        judged_at=judgment.judged_at or datetime.now(timezone.utc),
    )


def classify_unjudged(
    catalog: Catalog,
    articles: list[Article],
    vocabulary: list[str],
    judge: Judge,
    *,
    threshold: float = 0.7,
) -> tuple[list[Classification], list[object]]:
    """Judge every article with no catalog judgment yet; write each back.

    Skips already-judged articles (re-judge is explicit, not default).
    Returns (judgments, failures): one ClassificationFailure per article
    whose judge call or catalog write failed. Never aborts the run.
    `Article` gains no tag fields; judgments live in the catalog only.
    """
    judgments: list[Classification] = []
    failures: list[ClassificationFailure] = []
    for article in articles:
        try:
            if (
                catalog.get_classification(article.feed_id, article.article_id)
                is not None
            ):
                continue
        except CatalogError as e:
            failures.append(
                ClassificationFailure(
                    feed_id=article.feed_id,
                    article_id=article.article_id,
                    reason=str(e),
                )
            )
            continue
        try:
            judgment = classify(article, vocabulary, judge, threshold=threshold)
        except JudgeError as e:
            failures.append(
                ClassificationFailure(
                    feed_id=article.feed_id,
                    article_id=article.article_id,
                    reason=str(e),
                )
            )
            continue
        try:
            catalog.put_classification(judgment)
        except CatalogError as e:
            failures.append(
                ClassificationFailure(
                    feed_id=article.feed_id,
                    article_id=article.article_id,
                    reason=str(e),
                )
            )
            continue
        judgments.append(judgment)
    return judgments, failures
