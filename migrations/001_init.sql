-- Sourced — Postgres schema (doc 02 Database schema).
--
-- The SQLAlchemy models in sourced/store/models.py express the same schema
-- portably so the stack also runs on SQLite without a container. This file is
-- the Postgres form, with the JSONB and GIN specifics the document calls for.
--
--   psql "$SOURCED_DB_URL" -f migrations/001_init.sql

CREATE TABLE IF NOT EXISTS products (
    id                    UUID PRIMARY KEY,
    mpn                   TEXT NOT NULL,
    mpn_normalised        TEXT NOT NULL,
    mpn_family            TEXT,
    manufacturer          TEXT,
    manufacturer_resolved TEXT,
    source_status         TEXT NOT NULL,
    category_taxonomy     TEXT,
    category_code         TEXT,
    category_label        TEXT,
    attributes            JSONB NOT NULL DEFAULT '{}',
    commerce              JSONB,
    completeness          JSONB,
    abstention            JSONB,
    telemetry             JSONB,
    source_content_hash   TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- idempotency: reprocessing must update, never duplicate
    CONSTRAINT uq_product UNIQUE (manufacturer_resolved, mpn_normalised)
);

CREATE INDEX IF NOT EXISTS idx_products_attributes ON products USING GIN (attributes);

-- family index supports Level 3 coherence checks without a full scan.
-- The left-prefix is materialised into mpn_family on write so the same index
-- exists on SQLite; the expression index below is the Postgres-native form.
CREATE INDEX IF NOT EXISTS idx_products_family
    ON products (manufacturer_resolved, left(mpn_normalised, 8));


CREATE TABLE IF NOT EXISTS provenance (
    id                  UUID PRIMARY KEY,
    product_id          UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    canonical_key       TEXT NOT NULL,
    value_text          TEXT,
    unit                TEXT,
    raw_text            TEXT,
    tier                TEXT,
    resolution          TEXT NOT NULL,
    confidence          REAL,
    criticality         TEXT,
    source_id           TEXT,
    source_type         TEXT,
    page                INT,
    bbox                JSONB,
    span                TEXT,
    locator             TEXT,
    checks              JSONB,
    abstention_code     TEXT,
    abstention_detail   TEXT,
    resolution_hint     TEXT,
    model_id            TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_prov_lookup ON provenance (product_id, canonical_key);


CREATE TABLE IF NOT EXISTS sources (
    id                  TEXT PRIMARY KEY,
    source_type         TEXT NOT NULL,
    authority_rank      INT  NOT NULL,
    uri                 TEXT,
    content_hash        TEXT NOT NULL,
    fetched_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    page_count          INT
);


CREATE TABLE IF NOT EXISTS product_sources (
    product_id          UUID REFERENCES products(id) ON DELETE CASCADE,
    source_id           TEXT REFERENCES sources(id),
    match_confidence    REAL NOT NULL,
    match_evidence      TEXT,
    PRIMARY KEY (product_id, source_id)
);


-- Labels live here and nowhere the pipeline can read them (doc 05).
CREATE TABLE IF NOT EXISTS eval_labels (
    mpn_normalised        TEXT NOT NULL,
    manufacturer_resolved TEXT NOT NULL,
    canonical_key         TEXT NOT NULL,
    value_text            TEXT,
    unit                  TEXT,
    label_source          TEXT NOT NULL,   -- 'distributor_api' | 'hand_audit' | 'synthetic_generator_ground_truth'
    audited               BOOLEAN NOT NULL DEFAULT false,
    audit_verdict         TEXT,            -- 'correct' | 'incorrect' | 'ambiguous'
    split                 TEXT,
    PRIMARY KEY (manufacturer_resolved, mpn_normalised, canonical_key)
);
