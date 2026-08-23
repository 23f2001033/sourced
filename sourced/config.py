"""Runtime configuration. Everything overridable by environment variable."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SCHEMAS = ROOT / "schemas"
DOCS = ROOT / "docs"


def _load_dotenv(path: Path) -> None:
    """Read `.env` into the environment without clobbering what is already set.

    A real environment variable always wins, so a container or CI can override
    the file. Kept to a few lines rather than a dependency: the file format we
    actually use is `KEY=value`.
    """
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


_load_dotenv(ROOT / ".env")

PDF_DIR = DATA / "pdfs"
PAGE_DIR = DATA / "pages"          # distributor / marketplace HTML-ish pages
FIG_DIR = DOCS / "figures"

DB_URL = os.getenv("SOURCED_DB_URL", f"sqlite:///{(DATA / 'sourced.db').as_posix()}")

# --- LLM tier -------------------------------------------------------------
# Doc 03 specifies an Anthropic tool call. A second, OpenAI-compatible path
# exists so the tier can be exercised against open-weight models; which one is
# in use is recorded in the results rather than left implicit.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY") or None
FEATHERLESS_API_KEY = os.getenv("FEATHERLESS_API_KEY") or None
FEATHERLESS_BASE_URL = os.getenv("FEATHERLESS_BASE_URL",
                                 "https://api.featherless.ai/v1")

DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5",
    "featherless": "mistralai/Mistral-Nemo-Instruct-2407",
}


def _resolve_provider() -> str:
    declared = (os.getenv("SOURCED_LLM_PROVIDER") or "").strip().lower()
    if declared:
        return declared
    if ANTHROPIC_API_KEY:
        return "anthropic"
    if FEATHERLESS_API_KEY:
        return "featherless"
    return ""


LLM_PROVIDER = _resolve_provider()
LLM_API_KEY = {"anthropic": ANTHROPIC_API_KEY,
               "featherless": FEATHERLESS_API_KEY,
               "openai_compatible": FEATHERLESS_API_KEY}.get(LLM_PROVIDER)
LLM_BASE_URL = FEATHERLESS_BASE_URL
LLM_MODEL = (os.getenv("SOURCED_LLM_MODEL")
             or DEFAULT_MODELS.get(LLM_PROVIDER, "")) or ""
LLM_ENABLED = bool(LLM_PROVIDER and LLM_API_KEY and LLM_MODEL)


# Stage 0 thresholds (doc 03 §0.2, §0.4)
MANUFACTURER_FUZZ_FLOOR = 88
MATCH_SCORE_THRESHOLD = 0.70

# Stage 3 (doc 03 §3)
ADJUDICATION_MARGIN = 0.15

# Stage 5 publish thresholds by criticality (doc 03 §5)
PUBLISH_THRESHOLDS = {"safety": 0.99, "functional": 0.95, "cosmetic": 0.85}
SAFETY_REQUIRES_DUAL_SOURCE = True

# Source authority ranks (doc 02 SourceLink.authority_rank; 1 == strongest)
AUTHORITY_RANK = {
    "manufacturer_datasheet": 1,
    "manufacturer_page": 2,
    "distributor_api": 3,
    "distributor_page": 4,
    "marketplace": 5,
    "internal_record": 6,
}
AUTHORITY_DATASHEET = 1

for _d in (DATA, PDF_DIR, PAGE_DIR, FIG_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def store_uri(path) -> str:
    """How a source file is recorded in sources.jsonl.

    Relative to the data directory, so a corpus stays valid when it moves --
    into a container, onto another machine, out of a clean clone. Absolute host
    paths were baked in until a container tried to open one.
    """
    path = Path(path)
    try:
        return path.resolve().relative_to(DATA.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def resolve_uri(uri: str) -> Path:
    """The inverse. Absolute paths are honoured so older corpora still load."""
    path = Path(uri)
    return path if path.is_absolute() else (DATA / path)
