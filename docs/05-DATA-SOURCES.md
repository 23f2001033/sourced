# 05 — Data Sources

Every source below was checked for real availability. Access status and blockers are recorded because two of them have lead times that affect the build order.

---

## Verified access status

| Source | What it gives | Access | Lead time | Verified |
|---|---|---|---|---|
| **Digi-Key API** | Parametric attributes, datasheet URLs, category tree | Free developer account, self-service key generation, sandbox available | **Immediate** | Confirmed — developer portal at `developer.digikey.com`, OAuth 2.0, Product Information API |
| **Mouser API** | Specifications key-value map, datasheet and app-note URLs, pricing | Free, requires My Mouser account | **1–2 business days for key approval** | Confirmed — `mouser.com/en/api-hub/`, `get_product_detail` returns a specifications map plus documents |
| **UNSPSC** | Commodity classification codes | Published standard | Immediate | Confirmed — UN Standard Products and Services Code |
| **ETIM** | Industrial class definitions and features | Registration required at `etim-international.com` | Registration delay | Confirmed — European industrial classification standard, used by real PIM systems |
| **schema.org / JSON-LD** | Structured product markup embedded in pages | Public page source, parse with `extruct` | Immediate | Standard web markup |
| **Manufacturer datasheets** | The primary extraction corpus | Public PDFs | Immediate | — |

### The scheduling consequence

**Register for Digi-Key and Mouser on day one, before writing any code.** Mouser's 1–2 business day approval is the longest lead time in the project and it is entirely outside your control. Digi-Key is self-service and therefore the primary; Mouser is the backup that may or may not arrive in time.

If neither key materialises, the fallback is JSON-LD scraping plus a larger hand-audited set, which costs roughly a day. Plan the vertical choice accordingly.

---

## Why the vertical choice matters more than it looks

### Electronic components (Digi-Key / Mouser)

**For:** the labelling problem largely disappears. The APIs return structured parametric attributes *and* a link to the manufacturer datasheet, which is exactly the input/label pair the evaluation needs. Thousands of examples, free, no hand-labelling at volume.

**Against:** attribute names are already fairly standardised in this vertical, so the normalisation story is weaker. Descriptions are less abbreviation-dense, so the rules tier contributes less.

### Pipe fittings, valves, fasteners

**For:** the richest abbreviation soup (`1/2IN X 3/4IN BRS 90 ELL FIP 150#`), so the deterministic rules tier does real work and the normalisation story is strongest. MPN structure decoding lands hardest here.

**Against:** no free parametric API. Labels must come from JSON-LD scraping or hand work, costing roughly a day.

### Recommendation given three days

**Electronic components.** Free labels at volume is worth more than a richer normalisation narrative, because without labels every accuracy claim in the submission is an assertion. The rules tier still contributes through unit parsing, tolerance notation and package-code decoding.

---

## Corpus construction

### Target
300–500 SKUs in one category, with 40–80 linked manufacturer datasheet PDFs.

### Procedure
1. Query the distributor API for a single parametric category
2. For each result, capture: MPN, manufacturer, description, structured attributes, datasheet URL
3. Download datasheets, deduplicate by content hash
4. **Store the API attributes as silver labels, and the PDFs as the extraction corpus — never let the pipeline see the labels**
5. Deliberately construct the sparse input by discarding everything except MPN, manufacturer and a truncated description

Step 5 is essential. The system must be evaluated on the *sparse* input that reflects the real problem, not on the rich API record.

### Leakage discipline
The API record is the label. If any part of the pipeline reads it at inference time, the evaluation is void. Keep labels in a separate table (`eval_labels`) with no code path from the extraction pipeline.

---

## Legal and ethical position

- **APIs:** used within their documented terms, with a registered key. This is the intended use.
- **Datasheets:** manufacturer-published technical documents, downloaded at low volume for a non-commercial prototype. Respect `robots.txt`, rate-limit politely, identify the client honestly.
- **No aggressive scraping.** If a source requires circumvention to access, it is out of scope.
- **Attribution:** every source retained in the provenance record, which is both the ethical position and the product feature.

---

## What a corpus record looks like

```json
{
  "sku_input": {
    "mpn": "1546302-1",
    "manufacturer": "TE Connectivity",
    "description_fragment": "CONN HEADER VERT 4POS 3.96MM"
  },
  "sources": [
    {
      "source_id": "te_1546302_ds",
      "source_type": "manufacturer_datasheet",
      "authority_rank": 1,
      "uri": "https://.../datasheet.pdf",
      "content_hash": "sha256:..."
    }
  ],
  "silver_labels": {
    "pole_count": {"value": 4, "unit": null},
    "pitch": {"value": 3.96, "unit": "mm"},
    "contact_material": {"value": "phosphor_bronze", "unit": null},
    "current_rating": {"value": 7.0, "unit": "A"}
  },
  "label_provenance": "digikey_parametric_api",
  "hand_audited": false
}
```

The `sku_input` block is what the pipeline sees. The `silver_labels` block is what it is scored against and must never reach it.
