"""Chunk types shared by every extraction tier."""
from __future__ import annotations

from pydantic import BaseModel, Field

from sourced.models import Locator, SourceType


class Cell(BaseModel):
    header: str
    value: str
    bbox: tuple[float, float, float, float] | None = None


class Chunk(BaseModel):
    chunk_id: str
    source_id: str
    source_type: SourceType
    page: int | None = None
    text: str
    bbox: tuple[float, float, float, float] | None = None
    locator: Locator = "prose"
    cells: list[Cell] = Field(default_factory=list)


class Document(BaseModel):
    source_id: str
    source_type: SourceType
    uri: str | None = None
    content_hash: str = ""
    page_count: int = 0
    full_text: str = ""
    chunks: list[Chunk] = Field(default_factory=list)

    @property
    def authority_rank(self) -> int:
        from sourced.config import AUTHORITY_RANK

        return AUTHORITY_RANK.get(self.source_type, 9)

    def chunk(self, chunk_id: str) -> Chunk | None:
        for c in self.chunks:
            if c.chunk_id == chunk_id:
                return c
        return None
