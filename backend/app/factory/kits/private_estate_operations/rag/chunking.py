"""Deterministic structural text chunking for estate RAG documents."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import List, Optional

DEFAULT_CHUNKING_VERSION = "structural-character-v1"
DEFAULT_TARGET = 3200
DEFAULT_MAXIMUM = 4000
DEFAULT_OVERLAP = 400
DEFAULT_MINIMUM = 200

_HEADING_RE = re.compile(r"^(#{1,6}\s+.*|<h[1-6][^>]*>.*)$", re.MULTILINE | re.IGNORECASE)


@dataclass
class Chunk:
    text: str
    ordinal: int
    character_start: int
    character_end: int
    overlap_from_previous: int
    structural_type: Optional[str] = None


@dataclass
class ChunkConfig:
    algorithm: str
    version: str
    target_characters: int
    maximum_characters: int
    overlap_characters: int
    minimum_characters: int

    def validate(self) -> None:
        if self.target_characters <= 0:
            raise ValueError("target_characters must be > 0")
        if self.maximum_characters < self.target_characters:
            raise ValueError("maximum_characters must be >= target_characters")
        if self.overlap_characters < 0:
            raise ValueError("overlap_characters must be >= 0")
        if self.overlap_characters >= self.target_characters:
            raise ValueError("overlap_characters must be < target_characters")
        if self.minimum_characters <= 0 or self.minimum_characters > self.target_characters:
            raise ValueError("minimum_characters must be > 0 and <= target_characters")


def _find_split_point(text: str, max_len: int) -> int:
    if len(text) <= max_len:
        return len(text)
    candidate = text[: max_len + 1]
    for match in _HEADING_RE.finditer(candidate):
        end = match.end()
        if 0 < end <= max_len:
            return end
    idx = candidate.rfind("\n\n")
    if idx > 0:
        return idx + 2
    idx = candidate.rfind("\n")
    if idx > 0:
        return idx + 1
    for match in re.finditer(r"[.!?][ \t]+", candidate):
        end = match.end()
        if 0 < end <= max_len:
            return end
    idx = candidate.rfind(" ")
    if idx > 0:
        return idx + 1
    return max_len


def _find_overlap_start(text: str, overlap_chars: int) -> int:
    if len(text) <= overlap_chars:
        return 0
    candidate = text[-overlap_chars:]
    idx = candidate.find("\n\n")
    if idx != -1:
        return len(text) - overlap_chars + idx + 2
    for match in re.finditer(r"[.!?][ \t]+", candidate):
        start = match.start()
        return len(text) - overlap_chars + start + 2
    idx = candidate.find(" ")
    if idx != -1:
        return len(text) - overlap_chars + idx + 1
    return len(text) - overlap_chars


def chunk_text(text: str, config: Optional[ChunkConfig] = None) -> List[Chunk]:
    if config is None:
        config = ChunkConfig(
            algorithm="structural-character",
            version=DEFAULT_CHUNKING_VERSION,
            target_characters=DEFAULT_TARGET,
            maximum_characters=DEFAULT_MAXIMUM,
            overlap_characters=DEFAULT_OVERLAP,
            minimum_characters=DEFAULT_MINIMUM,
        )
    config.validate()
    if not text:
        raise ValueError("CANONICAL_TEXT_EMPTY")

    chunks: List[Chunk] = []
    pos = 0
    ordinal = 0
    prev_overlap_text = ""

    while pos < len(text):
        target_end = pos + config.target_characters
        if prev_overlap_text:
            target_end -= len(prev_overlap_text)
        target_end = min(target_end, len(text))
        max_end = min(pos + config.maximum_characters - len(prev_overlap_text), len(text))
        split_at = _find_split_point(text[:max_end], target_end)
        if split_at <= pos:
            split_at = max_end
        if split_at <= pos:
            split_at = len(text)

        chunk_text_content = prev_overlap_text + text[pos:split_at]
        remaining = len(text) - split_at
        if (
            0 < remaining < config.minimum_characters
            and len(chunk_text_content) + remaining <= config.maximum_characters
        ):
            chunk_text_content += text[split_at:]
            split_at = len(text)

        structural_type = "fragment"
        if chunk_text_content.lstrip().startswith("#"):
            structural_type = "heading"
        elif "\n\n" in chunk_text_content:
            structural_type = "paragraphs"
        elif "\n" in chunk_text_content:
            structural_type = "lines"

        chunks.append(
            Chunk(
                text=chunk_text_content,
                ordinal=ordinal,
                character_start=pos,
                character_end=split_at,
                overlap_from_previous=len(prev_overlap_text),
                structural_type=structural_type,
            )
        )

        if config.overlap_characters > 0 and split_at < len(text):
            overlap_start = _find_overlap_start(text[:split_at], config.overlap_characters)
            prev_overlap_text = text[overlap_start:split_at]
        else:
            prev_overlap_text = ""

        pos = split_at
        ordinal += 1

    return chunks


def chunk_hash(
    document_id: str,
    chunking_version: str,
    ordinal: int,
    character_start: int,
    character_end: int,
    text_hash: str,
) -> str:
    data = (
        f"{document_id}:{chunking_version}:{ordinal}:{character_start}:"
        f"{character_end}:{text_hash}"
    )
    return hashlib.sha256(data.encode("utf-8")).hexdigest()
