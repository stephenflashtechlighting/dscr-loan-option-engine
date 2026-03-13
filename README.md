# DSCR Loan Option Engine v2.3

Decision-support software for normalizing investor loan quotes, generating hold-period-aware comparisons, and exporting borrower-ready decision memos.

## Setup

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Pages

| Page | Purpose |
|------|---------|
| 🏠 Home (app.py) | Deal list, load / delete deals |
| 📋 Deal Intake | Create or edit a deal with property economics and borrower intent |
| 🔢 Scenario Builder | Add, edit, copy, and delete loan scenarios |
| 📊 Comparison Dashboard | Ranked scenarios, recommendation cards, breakeven, exports |
| 📥 Import Quote | Paste email/text — regex or AI extraction with review workflow |
| ⚙️ Settings | Persist fee defaults and scoring weight presets |

## AI extraction

The Import Quote page can call the Anthropic API for higher-quality field extraction. Set your API key:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
streamlit run app.py
```

If the key is not set, regex extraction is used automatically.

## Export formats

- **CSV** — full scenario comparison table
- **TXT** — plain-text decision memo
- **PDF** — formatted memo with ranked table, cash-to-close breakdown, and disclaimer

## Notes

- Data is stored in `dscr_engine.db` (SQLite) in the app directory
- Exports are saved to the `exports/` folder
- This tool is decision-support only — not lending, legal, or financial advice
- Source type is tracked per scenario: ✏️ Manual · 📥 Regex · 🤖 AI
