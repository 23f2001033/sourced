"""MPN normalisation and variant generation (doc 03 0.1).

Normalised is for matching and the uniqueness constraint; raw is what a human
recognises, so both are kept.
"""
from __future__ import annotations

import re


def normalise_mpn(raw: str) -> str:
    s = (raw or "").upper()
    s = re.sub(r"[\s\-_./,]", "", s)      # strip separators
    s = re.sub(r"[^A-Z0-9]", "", s)       # strip everything else
    return s


def mpn_variants(raw: str) -> list[str]:
    """Search variants — retrieval needs the punctuated forms too."""
    raw = (raw or "").strip()
    n = normalise_mpn(raw)
    return sorted({raw, raw.upper(), n,
                   re.sub(r"[\s_./]", "-", raw.upper()),
                   re.sub(r"[\s\-_./]", " ", raw.upper()).strip()} - {""})


def mpn_pattern(raw: str) -> re.Pattern:
    """A separator-tolerant, boundary-anchored pattern for one part number.

    Matching on fully normalised text (all separators stripped) makes a
    truncated part number a substring of the full one: `154630200-3` would
    match a document describing `154630200-3RT`. That is the sibling-part
    failure of ADR-002 arriving by a different route, so the boundary
    assertions are load-bearing rather than cosmetic.
    """
    chars = [c for c in (raw or "").upper() if c.isalnum()]
    if not chars:
        return re.compile(r"(?!x)x")            # matches nothing
    body = r"[\s\-_./,]*".join(re.escape(c) for c in chars)
    # The boundaries must reject a match that continues across a separator:
    # `B10B-XH02-A` is a prefix of `B10B-XH02-A-GV`, and a bare `(?![A-Z0-9])`
    # is satisfied by the hyphen, which lets the prefix through.
    lead = r"(?<![A-Z0-9])(?<![A-Z0-9][\-_./,])"
    trail = r"(?![\-_./,]*[A-Z0-9])"
    return re.compile(lead + body + trail)


def mpn_present(raw: str, text: str) -> bool:
    """Does this part number appear in this text, as a whole identifier."""
    return bool(mpn_pattern(raw).search((text or "").upper()))


def family_prefix(mpn_normalised: str, length: int = 8) -> str:
    """Left-prefix used to group sibling SKUs for Level 3 coherence."""
    return mpn_normalised[:length]
