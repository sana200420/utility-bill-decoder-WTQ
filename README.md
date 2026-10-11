# Utility Bill Decoder: "Verify-then-Trust"

Reads K-Electric, LESCO and IESCO electricity bills (scans, screenshots, tilted phone photos, partly Urdu),
extracts the Level 1 JSON and answers customer questions (Level 2) in plain English.

**Runtime:** Python 3.13 (tested on 3.13.2; 3.11+ works)

## How it works

```
bill image ─► preprocess (EXIF rotate, deskew, CLAHE contrast, upscale, zoomed top/bottom crops)
           ─► Pass A: Gemini vision on the original image        ┐ JSON-schema-constrained output,
           ─► Pass B: Gemini vision on enhanced image + zooms    ┘ provider-specific layout hints
           ─► field-level vote; if passes disagree ─► Pass C "juror": the other allowed model
              re-reads ONLY the disputed fields
           ─► arithmetic cross-checks pick between competing readings (never overwrite them):
              prev+units=current · Σcharges≈subtotal · Σtaxes≈subtotal · charges+taxes≈current bill
              · current+arrears(+FPA)≈payable · late≥within · date order
           ─► still failing a sum check? targeted re-read of the involved values (told which check failed,
              told NOT to compute), then confusable-digit repair: if flipping ONE visually-confusable digit
              (3↔8, 1↔7, 5↔6 …, e.g. a digit under a fold or pen mark) in one printed total makes every
              check pass, that reading is used and logged in the audit
           ─► deterministic normaliser: ISO dates, "CR" credits → negative, commas, subsidy → negative,
              drop 0-lines, provider from file name (KESC → KE), highest late-payment amount,
              exact field order, no personal identifiers
           ─► level1.csv  +  audit/<bill>.json (checks, votes, disagreements, confidence)

Level 2:   facts sheet (Level 1 + history table, late-payment tiers, printed % split, translated notes,
           pre-computed metrics)
           ─► LLM PLAN: interpretation + calculator expressions per question
           ─► PYTHON COMPUTE: safe AST evaluator (no eval), e.g. count_above(hist_units, 200)
           ─► LLM WRITE: plain-English answers using only computed numbers
           ─► NUMBER VERIFIER: every number in an answer must trace to the facts/results; otherwise one
              targeted repair call ─► level2.csv
```

Design choices:
- **The model reads; code does the maths.** Gemini handles vision, layout understanding, Urdu translation and
  language. Python handles normalisation, arithmetic, validation and CSV writing.
- **Self-consistency + cross-model juror** increases accuracy without a larger model.
- **Never invents values.** Missing, blank, greyed-out or unreadable values are `null`. Checks only choose between
  values a pass actually read from the bill.
- **Layout-aware.** Hints cover KE, IESCO and both LESCO layouts (summary "Bill Charges Breakdown" and the
  older itemised "LESCO CHARGES / GOVT. CHARGES" bill).
- **Cheap and robust.** About 2–3 vision calls per bill plus 2 text calls for the 12 questions. Disk cache, rate
  limiting (free tier is 15 RPM), exponential backoff, automatic fallback to the second allowed model, and a
  schema-valid fallback row if a bill fails, so the CSVs are never incomplete.

## Project layout

| Path | Purpose |
|---|---|
| `run.py` | CLI: `batch` (fills both CSV templates) and `bill` (one bill and questions) |
| `billdecoder/preprocess.py` | orientation, deskew, enhancement, zoom crops |
| `billdecoder/prompts.py` | JSON schema, extraction rules, KE/LESCO/IESCO layout hints |
| `billdecoder/extract.py` | multi-pass extraction, voting, juror, check-guided arbitration |
| `billdecoder/normalize.py` | deterministic conversion to the Level 1 contract |
| `billdecoder/validate.py` | arithmetic cross-checks |
| `billdecoder/facts.py`, `calc.py`, `answer.py` | Level 2 fact sheet, safe calculator, plan → compute → write → verify |
| `billdecoder/llm.py` | Gemini client (schema output, cache, throttle, retries, fallback) |
| `check_outputs.py` | validates generated CSVs against templates and the JSON contract |
| `package.py` | builds the submission ZIP and refuses to include secrets |
| `billdecoder/explain.py` | plain-language "bill in a nutshell" summary (numbers verified like Level 2) |
| `app.py` | "Bijli Buddy" Streamlit UI: upload, plain-words summary, trust meter, chat, usage and charges charts, Level 1 JSON |

## Setup

```bash
cd source
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then put your Gemini API key in API_KEY
```

Get a free Gemini key at https://aistudio.google.com ("Get API key").

## Run

Generate both CSV files from a folder of bill images and the two templates:

```bash
python run.py batch --bills ../test/bills --level1 ../test/csv/level1.csv --level2 ../test/csv/level2.csv --out ../output
python check_outputs.py --templates ../test/csv --out ../output
```

Outputs: `../output/level1.csv`, `../output/level2.csv`, plus one audit file per bill in `../audit/`.
Bills are matched to CSV rows by file name (`KESC_0002` → `bills/KESC_0002.png|.jpg`).

Single bill (with optional questions):

```bash
python run.py bill ../train/bills/KESC_0008.png -q "How much of my bill is taxes?"
```

Demo UI (optional):

```bash
pip install -r requirements-demo.txt
python -m streamlit run app.py
```

Use `python -m streamlit` (not the bare `streamlit` command) so the UI runs in the same Python environment where
`requirements.txt` was installed. Otherwise you may see `cannot import name 'genai' from 'google'`.

## Environment variables

| Variable | Value used |
|---|---|
| `MODEL_PROVIDER` | `google` |
| `MODEL_NAME` | `gemini-3.5-flash-lite` (primary) |
| `FALLBACK_MODEL_NAME` | `gemini-3.1-flash-lite` (fallback and juror) |
| `API_KEY` | your Gemini key (placeholder in `.env.example`) |
| `REQUEST_TIMEOUT_S`, `MIN_REQUEST_INTERVAL_S`, `MAX_RETRIES`, `CACHE_DIR` | optional tuning |

The code refuses to run with any model outside the competition allow-list.

## AI usage

| Category | Used |
|---|---|
| **Models** | Gemini 3.5 Flash-Lite (`gemini-3.5-flash-lite`) as the primary model for vision extraction, Level 2 planning, answer writing and repair, and the UI's plain-language summary. Gemini 3.1 Flash-Lite (`gemini-3.1-flash-lite`) as the juror for disputed fields and fallback when the primary is unavailable. |
| **APIs** | Google Gemini API (Google AI Studio) via the `google-genai` Python SDK |
| **Services** | none (no OCR or invoice-extraction services, no Azure, Textract or Mindee) |
| **Local OCR libraries** | none (OpenCV/Pillow are used only for image preprocessing) |
| **AI tools used to write the code** | Claude Code (Anthropic) as a coding assistant |
