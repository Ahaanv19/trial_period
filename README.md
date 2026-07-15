# 💬 TextBack Backend

The **TextBack Backend** is the server-side system behind **TextBack — Think
Before You Send**, an AI message advisor. It analyzes a message you're about to
send and returns a structured verdict: whether to **send / edit / don't send**,
a Safe-to-Send score, reply-likelihood, tone read, turn-offs, and cleaner
rewrites.

It powers the React frontend (`../send-wise-helper`) over a single endpoint:
**`POST /api/analyze`** on port **8275**.

---

## 💡 How It Works

1. **Receives a message from the frontend** — `POST /api/analyze` with
   `{ message, context, toneBias }`.
2. **Analyzes it with Google Gemini** — if a real `GEMINI_API_KEY` is set. The
   call retries briefly on transient errors.
3. **Falls back to a local engine** — a deterministic heuristic analyzer runs if
   Gemini is unavailable, so the API **never errors out** and works fully
   offline.
4. **Normalizes the result** — every response is coerced into the exact JSON
   schema the frontend renders (all four rewrite styles, insights, etc.).
5. **Stores it in SQLite** — each analysis is persisted via SQLAlchemy for a
   durable server-side history (`GET /api/analyze/history`).

---

## ⚙️ Tech Used

- **Flask** – REST API
- **SQLAlchemy + SQLite** – analysis history
- **Google Gemini** – AI analysis engine (with local fallback)
- **Flask-CORS / Flask-Limiter** – security + cross-origin support

---

## 🔌 Endpoints

| Method   | Route                   | Purpose                                  |
| -------- | ----------------------- | ---------------------------------------- |
| `POST`   | `/api/analyze`          | Analyze a message → full structured JSON |
| `GET`    | `/api/analyze/history`  | Recent analyses (from SQLite)            |
| `DELETE` | `/api/analyze/history`  | Clear server-side history                |
| `GET`    | `/api/analyze/health`   | Health check (reports active engine)     |

---

## 🧪 How to Run

```bash
# First time only
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Run the server (port 8275)
python main.py
```

Then start the frontend (`cd ../send-wise-helper && make`) and open
**http://localhost:8080**.

### Configuration (`.env`)

```
GEMINI_API_KEY=<your Google AI Studio key>   # optional — falls back to local analyzer
FLASK_PORT=8275
```
