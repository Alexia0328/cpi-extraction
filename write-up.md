# CPI extraction harness: write-up

## What I built

I chose Statistics Canada's monthly *Daily* CPI release. It is recurring and public but still messy: cards and prose coexist, current and comparison months share sentences, adjusted and unadjusted values can have opposite signs, and notes may describe current or future methodology changes.

The Python 3.12 job runs `discover -> fetch -> clean -> extract -> validate -> save/quarantine`. It supports the official Atom feed, a requested reference month, and a direct URL. Fetching has bounded retries, `Retry-After`, and local caching; accepted URLs skip repeated work. The eight-field record covers dates, headline YoY and MoM measures, CPI excluding gasoline, and methodology timing. Source URL, fetch time, SHA-256, and field evidence preserve the processed vintage.

Known wording takes a deterministic fast path; ambiguous wording falls back to local `qwen2.5:1.5b-instruct-q4_K_M` through Ollama. Both routes use the same deterministic validator. Invalid or late results go to quarantine with a nonzero exit code, while structured logs support scheduled operation.

## Accuracy and limits

I defined correctness field by field before scoring, using only the pinned release processed by the harness:

| Field | Correctness rule |
|---|---|
| `reference_month` | Exact month named in the CPI release title, normalized to `YYYY-MM`; never the publication month or a comparison month. |
| `release_date` | Exact date printed in the release header. |
| `headline_yoy_pct` | Current reference month's all-items CPI year-over-year rate, cross-checked against the first official headline card. |
| `headline_yoy_change_pp` | Current headline YoY minus the immediately preceding month's YoY stated in the same release, using the displayed one-decimal values. |
| `headline_mom_pct` | Current reference month's unadjusted all-items CPI month-over-month rate. |
| `headline_mom_sa_pct` | Current reference month's seasonally adjusted all-items CPI month-over-month rate. |
| `cpi_ex_gas_yoy_pct` | Current CPI excluding gasoline year-over-year rate, or `null` only when that exact measure is absent. |
| `methodology_change_status` | Exact four-state classification for basket, collection, calculation, or revision-policy changes: effective with this release, announced for later, both, or neither. |

Every non-null value needs verbatim evidence in the same pinned HTML. Dates and enums use exact equality; percentages are normalized to one decimal place. Field accuracy scores 80 labels independently. Strict record accuracy requires all eight fields and valid output; utility accuracy also requires processing within 10 seconds. One wrong field fails the case.

The 10 consecutive cases, December 2023 through September 2024, cover comparison-month leakage, written zeroes, opposite adjusted and unadjusted signs, missing optional data, all four methodology states, and a DOM variation. I manually verified every gold field against its pinned HTML; a manifest binds each fixture to its URL, retrieval time, and SHA-256. That review corrected one June 2024 methodology label before final scoring.

The preserved all-Qwen baseline scored 7/10 records and 77/80 fields (96.2%): two comparison-month errors and one missed future methodology announcement. After fixing those root causes, the same frozen set scored 10/10 records and 80/80 fields (100.0%). Cases took 5–9 ms, with a 6 ms median and zero model tokens. A separate pinned March 2025 case exercised the fallback in 3,975 ms with 621 input tokens.

The result measures the supported modern English *Daily* CPI family against exact cached vintages; legacy layouts fail closed. Final eval cases all used the fast path, so 100% is not a fallback-model accuracy estimate. Frozen-eval latency excludes network time because it reads local fixtures; runtime processing latency includes source fetch, extraction, and validation. `python main.py eval --offline` reproduces the score without network or model access.

## Sample outputs

Good, 2024-09:

```json
{"reference_month":"2024-09","headline_yoy_pct":"1.6","headline_yoy_change_pp":"-0.4","headline_mom_pct":"-0.4","headline_mom_sa_pct":"0.0","validation":"valid"}
```

Historical bad output, 2024-01:

```json
{"headline_mom_pct":"0.3","gold":"0.0","validation_pass":true,"eval_pass":false}
```

Full accepted, historical bad, and quarantine examples are under `eval/samples/`.

## Failure mode: comparison-month leakage

The baseline predicted January 2024 unadjusted MoM as `0.3%` instead of `0.0%`. The source said the CPI was unchanged in January, following a 0.3% December decline. The extractor selected the only written percentage, and the original validator accepted it because it appeared in relevant evidence. Presence did not prove that the value belonged to the reference month.

I fixed the extraction contract rather than adding a January-specific rule: each value must bind to its measure and reference month. Unfamiliar wording goes to the model and then through the same validator. In production I would monitor fallback rate, validation failures, and quarantine volume, investigate repeated clusters, and promote only stable recurring patterns into code.

## Where I did not use the LLM: validation

The final gate is deterministic: it checks title-derived dates, independently compares the headline card, enforces ranges, confirms exact source evidence, distinguishes adjusted wording, and verifies methodology timing. These checks have exact outcomes, so code is faster and reproducible. The LLM interprets unfamiliar prose upstream but does not judge its own output.

## Another week

I would add a separate analysis layer for headline acceleration, short-term momentum, core-versus-headline inflation, and narrative drivers. A timestamped consensus source could support release-time surprise without look-ahead bias. Derived results would retain their inputs or evidence; subjective policy labels would need a separate evaluation.

## Time spent

| Date | Activity | Active duration |
|---|---|---:|
| 2026-08-31 | Read and interpret the brief | 20 min |
| 2026-08-31 | Research and select the source | 15 min |
| 2026-08-31 | Analyze requirements and design the schema | 45 min |
| 2026-09-01 | Implement and test the harness | 2 hr 45 min |
| 2026-09-01 | Review and label the eval set | 30 min |
| 2026-09-02 | Run eval and investigate failures | 50 min |
| 2026-09-02 | Prepare documentation and samples | 25 min |
| 2026-09-02 | Profile and optimize inference latency | 25 min |
| 2026-09-03 | Simplify the code and complete the final submission review | 30 min |

**Total active time: 6 hr 45 min.**
