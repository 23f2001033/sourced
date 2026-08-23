"""Digi-Key corpus construction (doc 05).

This is the real path doc 05 specifies: pull parametric attributes and the
linked datasheet URL for one category, download the PDFs, and construct the
sparse input by discarding everything except MPN, manufacturer and a truncated
description. The API record is the label and is written to a separate file with
no code path from the pipeline.

It requires credentials, which need interactive registration at
developer.digikey.com. Without them nothing here runs and the synthetic
generator is used instead — the module is present so the real corpus is a
credential away, not a rewrite away.

    DIGIKEY_CLIENT_ID=... DIGIKEY_CLIENT_SECRET=... \\
        python -m sourced.corpus.digikey --category 437 --limit 400
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

from sourced import config

TOKEN_URL = "https://api.digikey.com/v1/oauth2/token"
SEARCH_URL = "https://api.digikey.com/products/v4/search/keyword"
USER_AGENT = "sourced/0.1 (UniHack prototype; contact via repository)"
RATE_LIMIT_SECONDS = 1.0


class MissingCredentials(RuntimeError):
    pass


def _credentials() -> tuple[str, str]:
    client_id = os.getenv("DIGIKEY_CLIENT_ID")
    client_secret = os.getenv("DIGIKEY_CLIENT_SECRET")
    if not (client_id and client_secret):
        raise MissingCredentials(
            "DIGIKEY_CLIENT_ID and DIGIKEY_CLIENT_SECRET are not set. Register at "
            "developer.digikey.com (self-service, immediate) and re-run, or use "
            "`python -m sourced.corpus.generate` for the synthetic corpus.")
    return client_id, client_secret


def access_token() -> str:
    client_id, client_secret = _credentials()
    body = urllib.parse.urlencode({
        "client_id": client_id, "client_secret": client_secret,
        "grant_type": "client_credentials"}).encode()
    request = urllib.request.Request(TOKEN_URL, data=body, method="POST",
                                     headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())["access_token"]


def search(token: str, keyword: str, limit: int, offset: int = 0) -> dict:
    client_id, _ = _credentials()
    payload = json.dumps({"Keywords": keyword,
                          "Limit": min(limit, 50), "Offset": offset}).encode()
    request = urllib.request.Request(
        SEARCH_URL, data=payload, method="POST",
        headers={"Authorization": f"Bearer {token}", "X-DIGIKEY-Client-Id": client_id,
                 "Content-Type": "application/json", "User-Agent": USER_AGENT,
                 "X-DIGIKEY-Locale-Site": "US", "X-DIGIKEY-Locale-Currency": "USD"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())


def download(url: str, target: Path) -> str | None:
    """Politely fetch a datasheet. Low volume, honest client identification."""
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = response.read()
    except Exception:
        return None
    if not payload.startswith(b"%PDF"):
        return None
    digest = hashlib.sha256(payload).hexdigest()
    target.write_bytes(payload)
    return "sha256:" + digest


def truncate(description: str, width: int = 32) -> str:
    """Construct the sparse input: the distributor's row, not the API record."""
    return (description or "")[:width].rstrip()


def build(keyword: str, limit: int, out_dir: Path | None = None) -> dict:
    data = Path(out_dir or config.DATA)
    pdf_dir = data / "pdfs"
    token = access_token()

    records, sources, seen_hashes = [], [], {}
    offset = 0
    while len(records) < limit:
        page = search(token, keyword, limit - len(records), offset)
        products = page.get("Products") or []
        if not products:
            break
        for product in products:
            mpn = product.get("ManufacturerProductNumber")
            if not mpn:
                continue
            manufacturer = (product.get("Manufacturer") or {}).get("Name")
            description = (product.get("Description") or {}).get("ProductDescription", "")

            labels = {}
            for parameter in product.get("Parameters") or []:
                key = str(parameter.get("ParameterText", "")).strip()
                value = str(parameter.get("ValueText", "")).strip()
                if key and value and value not in {"-", "*"}:
                    labels[key] = {"value": value, "unit": None}

            linked = []
            datasheet_url = product.get("DatasheetUrl")
            if datasheet_url:
                source_id = hashlib.sha1(datasheet_url.encode()).hexdigest()[:16]
                path = pdf_dir / f"{source_id}.pdf"
                content_hash = (seen_hashes.get(datasheet_url)
                                or (download(datasheet_url, path) if not path.exists()
                                    else "sha256:cached"))
                if content_hash:
                    seen_hashes[datasheet_url] = content_hash
                    linked.append({"source_id": source_id,
                                   "source_type": "manufacturer_datasheet"})
                    sources.append({"source_id": source_id,
                                    "source_type": "manufacturer_datasheet",
                                    "authority_rank": 1, "uri": config.store_uri(path),
                                    "family_id": mpn[:8]})
                time.sleep(RATE_LIMIT_SECONDS)

            records.append({
                # what the pipeline sees
                "sku_input": {"mpn": mpn, "manufacturer": manufacturer,
                              "description_fragment": truncate(description),
                              "internal_sku": None},
                "category": "electrical_connector",
                "family_id": mpn[:8],
                "cohort": "normal",
                "expected_sources": linked,
                "split": _split_for(mpn),
                # what it is scored against, and must never reach it
                "labels": labels,
                "label_provenance": "digikey_parametric_api",
                "hand_audited": False,
            })
        offset += len(products)
        time.sleep(RATE_LIMIT_SECONDS)

    (data / "corpus.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    (data / "sources.jsonl").write_text(
        "\n".join(json.dumps(s) for s in sources) + "\n", encoding="utf-8")
    summary = {"skus": len(records), "datasheets": len({s["source_id"] for s in sources}),
               "keyword": keyword, "label_provenance": "digikey_parametric_api"}
    (data / "corpus_summary.json").write_text(json.dumps(summary, indent=2),
                                              encoding="utf-8")
    return summary


def _split_for(mpn: str) -> str:
    h = int(hashlib.sha256(f"split:{mpn}".encode()).hexdigest(), 16) % 100
    return "dev" if h < 40 else ("calibration" if h < 70 else "test")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--keyword", default="wire to board connector header")
    parser.add_argument("--limit", type=int, default=300)
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.keyword, args.limit), indent=2))
    except MissingCredentials as error:
        raise SystemExit(str(error))
