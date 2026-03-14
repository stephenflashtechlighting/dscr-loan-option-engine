# DSCR Loan Option Engine — v2.8.0

A Streamlit app for comparing DSCR loan scenarios across multiple lenders.

---

## Supported intake types (in order of reliability)

| Method | Reliability | Tab |
|---|---|---|
| Manual entry | ✅ Highest — no extraction | Manual entry |
| CSV upload | ✅ High — structured, validated | Upload CSV |
| Structured key:value paste | 🔶 Good — parsed strictly | Paste structured text |
| Single-scenario quote text | 🔶 Good with AI enabled | Paste structured text |
| PDF quote (text-layer) | 🔶 Medium — review required | Upload PDF |
| Multi-scenario summary | ⚠️ Lower — use picker | Paste structured text |

---

## CSV template usage

Two templates are available from the **Upload CSV** tab:

### Simple lender template (`dscr_lender_template_simple.csv`)
Send this to lenders. Minimal columns, easy to fill.

**Rules for lenders filling the template:**
- One row per loan option — do **not** combine multiple options in one row
- Use plain numbers (`7.25` not `7.25%`) — importer strips `$` and `%` if present
- Leave cells blank if a value does not apply
- Required fields: `lender_name`, `program_name`, `purchase_price`, `loan_amount`, `note_rate_percent`, `points_percent`, `loan_term_months`, `amortization_months`

### Full template (`dscr_full_template.csv`)
All supported fields. Use internally for bulk scenario entry.

---

## Structured key:value paste format

The most reliable text intake path. Paste one field per line in `Label: value` format.

Parenthetical unit suffixes like `($)`, `(%)`, `(months)` are automatically stripped — no need to remove them before pasting.

**Example:**
```
Lender name: My Community Mortgage LLC
Program name: 30 Year NON-QM Fixed
Note rate (%): 6.500
Points (%): 1.500
Loan term (months): 360
Amortization (months): 360
Interest-only period (months): 0
Prepay type: declining
Prepay window (months): 60
Underwriting fee ($): 1395
Processing fee ($): 0
Appraisal fee ($): 700
Title fee ($): 1922.56
Lender credit ($): 0
```

---

## Multi-scenario summary handling

If you paste a summary that contains multiple rate/point options (e.g., `20% down / 6.25% / 0 pts`), the app detects this automatically and shows a **scenario picker** instead of attempting to auto-fill one quote.

Select one row from the picker, then review and save.

---

## PDF extraction limitations

PDF extraction uses the text layer only — no raw byte parsing. Results vary by document quality.

- Clean, text-based lender PDFs: good results with targeted parser
- Scanned or image PDFs: will fail cleanly with an error
- Multi-scenario PDFs: routed to scenario picker
- Always verify every extracted field against the original document before saving

---

## Save-time validation gate

No scenario can be saved with critical errors. The validation gate blocks save if:

- Note rate is outside 0–20%
- Loan amount exceeds purchase price
- Amortization is shorter than loan term (unless override)
- Lender name looks like a fee label (parsing error detection)
- Cash to close exceeds 125% of purchase price

Warnings (review recommended but not blocking):
- Missing program name or prepay type
- Unusually high fees
- Multiple fields set to default values

---

## Listing import

The listing import URL tab supports **Trulia only**. Other listing sites may work via the paste-text tab but are not guaranteed. Manual property entry is always available as a first-class option.

---

## Requirements

```
streamlit
pydantic
pandas
pdfminer.six
anthropic
requests
beautifulsoup4
```

Install: `pip install -r requirements.txt`

---

## Running locally

```bash
streamlit run app.py
```
