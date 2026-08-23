FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /app

# pdfplumber renders pages through pypdfium2; no system PDF tooling required
COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install ".[postgres]"

COPY sourced ./sourced
COPY schemas ./schemas
COPY web ./web
COPY migrations ./migrations
COPY docs ./docs
COPY README.md ./

# The corpus is generated, not shipped: a clean clone builds its own.
RUN python -m sourced.corpus.build

EXPOSE 8000
CMD ["uvicorn", "sourced.api.routes:app", "--host", "0.0.0.0", "--port", "8000"]
