# Payments Reconciliation Engine

An AI-powered month-end payment reconciliation tool. Upload platform transactions and bank settlements, detect gaps automatically, and get Gemini AI root-cause analysis from a clean browser UI.

The backend serves the frontend directly — one process, one URL, no separate deployment needed.

---

## Quick Start

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env          # add your GEMINI_API_KEY
python main.py
```

Open `http://localhost:8000` in your browser. The dashboard auto-loads sample data.

---

## Configuration

Edit `backend/.env`:

```env
GEMINI_API_KEY=your_key_here   # get one at aistudio.google.com
APP_ENV=production
LOG_LEVEL=INFO
HOST=0.0.0.0
PORT=8000
CORS_ORIGINS=*
```

---

## Deployment

### Render.com (free tier)

1. Push the repo to GitHub.
2. Create a new **Web Service** on [render.com](https://render.com), connect your repo.
3. Render detects `render.yaml` automatically.
4. Set `GEMINI_API_KEY` in the Render environment variables dashboard.
5. Deploy — the service URL serves both the API and the frontend UI.

### Docker (self-hosted)

```bash
cd backend
docker build -t payments-recon .
docker run -p 8000:8000 --env-file .env payments-recon
```

Open `http://localhost:8000`.

---

## API Reference

Interactive docs at `http://localhost:8000/docs`.

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/health` | Server health and Gemini status |
| POST | `/api/v1/reconcile` | Reconcile custom data |
| POST | `/api/v1/reconcile/sample` | Generate and reconcile sample data |
| POST | `/api/v1/ai/analyse` | Gemini analysis of a recon result |
| POST | `/api/v1/ai/chat` | Conversational Q&A |
| GET | `/api/v1/logs/recent` | Last N log lines |

---

## Running Tests

```bash
cd backend
pytest tests.py -v
```

---

## Project Structure

```
payments-recon/
├── backend/
│   ├── main.py                  # FastAPI app, routes, and frontend serving
│   ├── reconciliation_engine.py # Core gap detection logic
│   ├── gemini_analyst.py        # Gemini AI integration
│   ├── data_generator.py        # Synthetic data with planted gaps
│   ├── log_config.py            # Structured logging
│   ├── tests.py                 # Test suite (25 cases)
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .env.example
├── frontend/
│   ├── index.html               # Single-file UI (served by FastAPI)
│   └── config.js                # Auto-detects API URL from window.location.origin
└── render.yaml                  # Render.com deployment config
```

---

## Gap Types Detected

| Gap Type | Severity | Description |
|----------|----------|-------------|
| `DUPLICATE_PLATFORM_TXN` | CRITICAL | Same txn_id posted twice on platform |
| `ORPHAN_REFUND` | CRITICAL | Refund references a txn_id not in platform ledger |
| `NEXT_MONTH_SETTLEMENT` | HIGH | Transaction settled outside the review month |
| `UNMATCHED_PLATFORM_TXN` | HIGH | Platform txn with no matching bank settlement |
| `UNMATCHED_BANK_SETTLEMENT` | HIGH | Bank record with no matching platform txn |
| `AMOUNT_MISMATCH` | CRITICAL/MEDIUM | Settled amount differs from platform amount |
| `ROUNDING_BATCH_DELTA` | MEDIUM | Cumulative sub-cent rounding across a merchant batch |

---

## Production Blind Spots

1. **FX conversion mismatches** — multi-currency settlements may reconcile incorrectly when exchange rates differ between booking date and settlement date; the engine assumes a single currency per run.
2. **Chargeback and dispute lifecycle** — a transaction later reversed via chargeback appears as an orphan bank debit with no platform counterpart; the engine has no chargeback state machine to correlate these records.
3. **Real-time intra-day settlement drift** — the engine operates on a static month-end snapshot, so partial-day batches that straddle the cut-off window are not flagged until the following scheduled run.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Page not loading | Start the backend: `cd backend && python main.py` |
| AI analysis returns mock data | Add `GEMINI_API_KEY` to `backend/.env` |
| Chat returns an error | Refresh the page to reset chat history |
| Port conflict | Change `PORT=8001` in `.env` and open `http://localhost:8001` |
