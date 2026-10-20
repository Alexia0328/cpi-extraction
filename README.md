# Statistics Canada CPI Extraction Harness

This harness turns monthly English Statistics Canada *The Daily* CPI releases in the supported modern HTML layout into eight-field, as-published JSON records. It supports scheduled discovery, caches each source vintage, and sends invalid or late results to quarantine instead of publishing them.

```text
Atom discovery → resilient fetch/cache → deterministic HTML cleaning
              → strict deterministic extraction → deterministic validation
                               ↓ ambiguous or unfamiliar wording
                         local LLM structured fallback
              → accepted record or quarantine → vintage-pinned evaluation
```

Known, unambiguous source wording takes a millisecond-scale deterministic path. If a required value cannot be tied confidently to the reference month, or a methodology note contains change and timing signals that the strict rules cannot classify, the path returns no candidate and the local model proposes all eight business fields and exact evidence. Both paths pass through the same deterministic validation gate before output.

Python 3.12 keeps the CLI and scheduler integration portable. `httpx` provides explicit timeout and retry control, `lxml` handles both Atom and irregular HTML, and Pydantic supplies one schema for Ollama structured output, runtime validation, and saved JSON. This keeps the batch job small and avoids an unnecessary application framework.

## Prerequisites

- Python 3.12
- [Ollama](https://ollama.com/download), installed separately from the Python dependencies
- Internet access for the first model download and for commands that fetch current Statistics Canada releases

The fixed [Qwen model](https://ollama.com/library/qwen2.5:1.5b-instruct-q4_K_M) is a small, speed-first fallback that needs no paid API key. Ollama accepts a JSON schema in its chat API, so generation and validation use the same Pydantic contract: [Structured Outputs documentation](https://docs.ollama.com/capabilities/structured-outputs).

## Install

Extract the ZIP and open a terminal in the project root. Python, Python packages, and Ollama are installed separately.

### 1. Create and activate the Python environment

Python 3.12 must already be installed; `requirements.txt` does not install Python. On Windows, first check the available versions:

```powershell
py -0p
```

If Python 3.12 is listed, create and activate the environment:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python --version
```

If it is not listed, install Python 3.12 from [python.org](https://www.python.org/downloads/) before continuing. If `python --version` already reports Python 3.12 but the `py` launcher is unavailable, use `python -m venv .venv`.

On macOS or Linux:

```bash
python3.12 --version
python3.12 -m venv .venv
source .venv/bin/activate
python --version
```

### 2. Install the Python packages

With the virtual environment active:

```bash
python -m pip install -r requirements.txt
```

### 3. Install Ollama and download the fallback model

On Windows 10 or later, use the [official Windows installer](https://ollama.com/download/windows) or run:

```powershell
irm https://ollama.com/install.ps1 | iex
```

On macOS or Linux, follow the [official Ollama instructions](https://ollama.com/download). Then download the model on any platform:

```bash
ollama pull qwen2.5:1.5b-instruct-q4_K_M
```

The final command downloads the 986 MB fallback model once. Start Ollama before running a command that may need model fallback; it normally serves at `http://localhost:11434`. Set `OLLAMA_BASE_URL` only when using a different local endpoint. `eval --offline` needs neither Ollama nor network access.

## Commands

```bash
python main.py run-latest
python main.py run --month 2024-09
python main.py run --url https://www.statcan.gc.ca/daily-quotidien/260817/dq260817a-eng.htm
python main.py eval --offline
python main.py eval --live
```

- `run-latest` refreshes the official Prices and price indexes Atom feed, selects the newest exact `Consumer Price Index, <Month> <Year>` entry, and then runs the pipeline.
- `run --month YYYY-MM` finds that reference month's published CPI URL in Statistics Canada's official [major-economic-indicators schedule JSON](https://www.statcan.gc.ca/en/developers/schedule-key_indicators-eng-json), then runs the same pipeline. An invalid, future, unavailable, or unsupported-layout month fails clearly.
- `run --url` accepts English pages in the modern Daily CPI layout covered by the fixtures. Legacy layouts, PDF, French, non-Daily, and non-StatsCan sources fail before model invocation.
- `--refresh` refetches discovery metadata and the source page. If the page content hash is unchanged, the content-addressed model response remains reusable.
- `eval --live` executes the full extraction pipeline on all 10 committed, hash-verified HTML fixtures and invokes Ollama only when fallback is needed. It does not refetch historical pages from Statistics Canada. It writes `eval/predictions.live.jsonl`, preserves the first model run once as `eval/predictions.baseline.jsonl`, and updates `eval/predictions.jsonl` as the current result for offline scoring.
- `eval --offline` reads only `eval/cases.jsonl` and `eval/predictions.jsonl`. It performs no fetch or API call.

## Eight-field record

| Field | Type | Meaning |
|---|---|---|
| `reference_month` | `YYYY-MM` | Month measured by the release |
| `release_date` | date | Date this release was published |
| `headline_yoy_pct` | Decimal | All-items CPI, year over year |
| `headline_yoy_change_pp` | Decimal | Current headline YoY minus the preceding month's headline YoY stated in the same release, in percentage points |
| `headline_mom_pct` | Decimal | All-items CPI, unadjusted month over month |
| `headline_mom_sa_pct` | Decimal | All-items CPI, seasonally adjusted month over month |
| `cpi_ex_gas_yoy_pct` | Decimal or null | CPI excluding gasoline, year over year |
| `methodology_change_status` | enum | `effective_now`, `future_announced`, both, or `none` for qualifying basket/collection/calculation/revision changes |

Percentages are normalized to one decimal place. `headline_yoy_change_pp` is calculated from the two displayed as-published YoY rates: positive means accelerating, negative means decelerating, and zero means unchanged. An explicit unchanged monthly value maps to `0.0`; `null` means the target measure is absent, and similarly named measures are not substitutes.

## Reliability

- Source requests use 5-second connect and 20-second read timeouts. HTTP 408, 429, and 5xx responses receive up to three attempts with backoff, jitter, and `Retry-After`; other 4xx responses are not retried.
- Raw HTML is cached by canonical URL hash, and an accepted URL skips before source-page or model work. The source SHA-256 and fetch timestamp record the exact vintage processed.
- Known wording takes the strict path. Unfamiliar methodology wording is not accepted as an automatic `none`: it falls back to Ollama, and the result must still include exact source evidence with independently checkable timing language.
- The fallback prompt includes only title/date, target CPI sentences, and methodology passages; unrelated narrative is not sent to the model.
- Every candidate passes the same deterministic validator. An invalid candidate, or processing beyond the 10-second SLO, is quarantined immediately instead of triggering another slow inference.
- Atomic writes, JSON-line logs, and stable exit codes let a scheduler detect failures without parsing prose. Saved and skipped events include the extraction method and validation status.

Exit codes:

| Code | Meaning |
|---:|---|
| 0 | success or idempotent skip |
| 2 | usage/configuration error, including unavailable Ollama or a missing model |
| 3 | unsupported or unavailable source |
| 4 | extraction/model failure before a candidate can be validated |
| 5 | invalid candidate quarantined |
| 6 | evaluation data/configuration error |

## Scheduled run

Run `run-latest` from cron, Windows Task Scheduler, or an equivalent scheduler. This example checks twice on working days just after *The Daily*'s [8:30 a.m. Eastern release time](https://www150.statcan.gc.ca/n1/dai-quo/info3-eng.htm); caching makes the second check harmless if the first succeeds.

```cron
CRON_TZ=America/Toronto
31,33 8 * * 1-5 cd /opt/statcan-cpi-harness && .venv/bin/python main.py run-latest
```

Keep Ollama running for fallback extraction and alert on nonzero exit codes or new files under `outputs/quarantine/`.

## Evaluation

The frozen challenge set contains 10 consecutive releases from December 2023 through September 2024 (80 fields). It includes negative and zero monthly values, opposite SA/NSA signs, missing optional values, all four methodology timing states, and a changed DOM layout. Gold values were checked against the exact HTML bytes under `eval/fixtures/`, not a current database. `manifest.json` binds each case to its URL, retrieval timestamp, and SHA-256; evaluation fails if a fixture changes.

Primary metric: trustworthy-and-timely utility accuracy. A case passes only when all eight fields exactly match, deterministic validation passes, and page-to-validated-record processing finishes within 10 seconds. Five seconds is the target; 10 seconds is the hard failure threshold. Discovery lag is reported separately so scheduler delay cannot be hidden inside model latency.

Secondary metrics: strict record accuracy without the time threshold, and field-level exact accuracy. Dates and enum values must match exactly, decimals are compared at one decimal place, and `null` is distinct from a missing or different measure.

All 10 gold cases were manually verified field by field against their pinned HTML pages. This review found and corrected one methodology label for June 2024 before final scoring. Against the verified labels, the preserved all-Qwen baseline scores 7/10 cases and 77/80 fields (96.2%); its three errors were two current-versus-comparison-month selections and one missed future methodology announcement. After addressing those two root causes, the same frozen set scores 10/10 cases and 80/80 fields (100.0%). Processing took 5 to 9 ms per case (6 ms median), excluding network discovery and download time. All final cases used the deterministic fast path, so the 100% result does not measure fallback-model accuracy. The original model predictions and a bad sample remain under `eval/`.

```bash
python main.py eval --live
python main.py eval --offline
```

Results under `eval/results/` include pass/fail per case plus utility, strict-record, and field-level accuracy. The offline command reproduces the current score without starting Ollama.

## Tests

```bash
python -m pytest src/tests -q
```

The 16 light tests cover the main discovery, retry/cache, parsing, extraction, validation, idempotency, quarantine, pinned-vintage, and offline-evaluation paths.

## Project layout

```text
.
├── README.md          setup, commands, schema, and operations
├── requirements.txt  pinned Python dependencies
├── main.py            CLI entry point
├── src/               harness code and tests
├── eval/              gold set, pinned fixtures, results, and samples
├── write-up.md        design, accuracy, failures, decisions, and time spent
└── ai-log.md          representative AI use and decisions
```

Runtime caches, generated records, quarantine files, virtual environments, development plans, and the assignment PDF are excluded. Pinned fixtures remain under `eval/` because they are part of the evaluation contract.
