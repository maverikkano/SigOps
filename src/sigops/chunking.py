"""Chunk & Index stage (issue #8).

Splits an extracted article (Markdown from the Extraction stage) into
ordered, addressable chunks: text chunks and separate image chunks.

Decisions (from issue #8, confirmed by owner):
- Structure-aware first: split on Markdown headings / paragraph
  boundaries; tables and fenced code blocks stay atomic.
- Fixed-size sliding window with overlap is the fallback only, for a
  very long single paragraph with no structure to split on.
- Splitting is a pure function: deterministic, no I/O, no HTTP, no LLM.
  Same Markdown in -> same chunks out.
- "Index" here is a positional record only (pos per article), not a
  vector search index. Real search waits for Semantic Indexing.
"""

from __future__ import annotations

import hashlib
import re

from sigops.models import Article, Chunk, ChunkKind

MAX_CHARS = 2000
"""Soft cap per text chunk. Paragraphs pack under it; never split mid-paragraph."""

OVERLAP = 150
"""Overlap (chars) for the long-single-paragraph fallback window only."""

_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(\s*(\S+?)(?:\s+\"[^\"]*\")?\s*\)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def _content_hash(text: str) -> str:
    """sha256 of the source extracted_text at chunk time."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _split_long_paragraph(text: str, max_chars: int, overlap: int) -> list[str]:
    """Sliding window with overlap, snapped to word boundaries.

    Deterministic. Never yields empty strings. Overlap is approximate:
    the next window starts `overlap` chars before the previous end,
    advanced to the next word boundary so words are never cut.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    step = max_chars - overlap
    if step <= 0:
        step = max_chars
    out: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = start + max_chars
        if end >= n:
            piece = text[start:].strip()
            if piece:
                out.append(piece)
            break
        # snap end back to a space so we never cut a word
        snap = text.rfind(" ", start, end)
        if snap > start:
            end = snap
        piece = text[start:end].strip()
        if piece:
            out.append(piece)
        nxt = end - overlap
        if nxt <= start:
            nxt = end
        # advance to next word start so the next chunk starts clean
        while nxt < n and text[nxt] == " ":
            nxt += 1
        if nxt <= start:
            nxt = end
        start = nxt
    return out


def chunk_article(
    article: Article,
    max_chars: int = MAX_CHARS,
    overlap: int = OVERLAP,
) -> list[Chunk]:
    """Split `article.extracted_text` into ordered text + image chunks.

    Images (`![alt](src)`) become IMAGE chunks in document order; the
    surrounding prose stays TEXT. Empty body yields []. A single short
    paragraph yields one TEXT chunk. Deterministic: same input always
    yields the same chunk list.

    Pure function: no I/O.
    """
    text = article.extracted_text or ""
    if not text.strip():
        return []
    digest = _content_hash(text)
    lines = text.split("\n")
    # precompute each line's char offset in the original text
    offsets: list[int] = []
    cur = 0
    for i, ln in enumerate(lines):
        offsets.append(cur)
        cur += len(ln) + (1 if i < len(lines) - 1 else 0)

    # heading stack for source_ref: list of (level, title)
    stack: list[tuple[int, str]] = []

    def heading_path() -> str:
        if not stack:
            return "(root)"
        return " > ".join(t for _, t in stack)

    chunks: list[Chunk] = []
    # pending text blocks for the current TEXT chunk: (block_text, start_offset)
    pending: list[tuple[str, int]] = []
    pending_len = 0

    def flush_pending() -> None:
        nonlocal pending, pending_len
        if not pending:
            return
        joined = "\n\n".join(t for t, _ in pending).strip()
        start_off = pending[0][1]
        # pending blocks are already <= max_chars except atomic
        # over-cap tables/code; emit as one chunk to preserve atomicity.
        # (Long plain paragraphs are pre-split before reaching here.)
        if joined:
            chunks.append(
                Chunk(
                    feed_id=article.feed_id,
                    article_id=article.article_id,
                    pos=len(chunks),
                    kind=ChunkKind.TEXT,
                    content=joined,
                    source_ref=f"{heading_path()} @ {start_off}",
                    content_hash=digest,
                )
            )
        pending = []
        pending_len = 0

    def push_text(block: str, start_off: int, atomic: bool = False) -> None:
        """Accumulate a text block, flushing when the cap is reached.

        Plain paragraphs longer than the cap are pre-split with overlap;
        atomic blocks (tables, code) are kept whole even when over cap.
        """
        nonlocal pending, pending_len
        block = block.strip()
        if not block:
            return
        if not atomic and len(block) > max_chars:
            for piece in _split_long_paragraph(block, max_chars, overlap):
                push_text(piece, start_off, atomic=True)
            return
        sep = 2 if pending else 0  # "\n\n" joiner
        if pending and pending_len + sep + len(block) > max_chars:
            flush_pending()
        pending.append((block, start_off))
        pending_len = (pending_len + sep + len(block)) if pending else len(block)
        # recompute cheaply: sum not needed; track incrementally
        # (pending_len already updated above)

    def push_image(src: str, start_off: int) -> None:
        flush_pending()
        chunks.append(
            Chunk(
                feed_id=article.feed_id,
                article_id=article.article_id,
                pos=len(chunks),
                kind=ChunkKind.IMAGE,
                content=src,
                source_ref=f"{heading_path()} @ {start_off}",
                content_hash=digest,
            )
        )

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        start_off = offsets[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        # fenced code block: atomic
        if stripped.startswith("```"):
            fence = [line]
            i += 1
            while i < n:
                fence.append(lines[i])
                closing = lines[i].strip().startswith("```")
                i += 1
                if closing:
                    break
            push_text("\n".join(fence), start_off, atomic=True)
            continue
        # heading: flush, update stack, start new chunk with the heading
        m = _HEADING_RE.match(stripped)
        if m:
            flush_pending()
            level = len(m.group(1))
            title = m.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            push_text(stripped, start_off)
            i += 1
            continue
        # table: consecutive | lines, atomic
        if stripped.startswith("|"):
            tbl = [line]
            i += 1
            while i < n and lines[i].strip().startswith("|"):
                tbl.append(lines[i])
                i += 1
            push_text("\n".join(tbl), start_off, atomic=True)
            continue
        # mixed text / images on this line: split in document order
        matches = list(_IMAGE_RE.finditer(line))
        if matches:
            pos_cursor = 0
            for mm in matches:
                before = line[pos_cursor : mm.start()].strip()
                if before:
                    push_text(before, start_off + pos_cursor)
                src = mm.group(2).strip()
                if src:
                    push_image(src, start_off + mm.start())
                pos_cursor = mm.end()
            after = line[pos_cursor:].strip()
            if after:
                push_text(after, start_off + pos_cursor)
            i += 1
            continue
        # plain paragraph line: gather consecutive plain lines into one
        # paragraph block (single newlines join, blank line ends it)
        para = [line]
        i += 1
        while i < n:
            nxt = lines[i]
            nxt_stripped = nxt.strip()
            if not nxt_stripped:
                break
            if nxt_stripped.startswith("```"):
                break
            if _HEADING_RE.match(nxt_stripped):
                break
            if nxt_stripped.startswith("|"):
                break
            if _IMAGE_RE.search(nxt):
                break
            para.append(nxt)
            i += 1
        block = " ".join(s.strip() for s in para).strip()
        push_text(block, start_off)
    flush_pending()
    # re-assign pos defensively in document order (already ordered)
    for idx, c in enumerate(chunks):
        c.pos = idx
    return chunks
