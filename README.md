# ✈️ Flight Price Tracker

A Flask-based flight price tracker that searches Google Flights data, stores tracked routes, records price history, and sends email alerts when a new lowest price or target price is reached.

## Features

- Flight search by IATA airport code
- One-way and round-trip search
- Price sorting
- Track a route
- Target price alerts
- New-lowest-price alerts
- SQLite price history
- Scheduled checking with Render Cron
- Safe API key handling with environment variables

## Local setup

```bash
python -m venv .venv
```

Windows:

```bash
.venv\Scripts\activate
```

Install:

```bash
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and add your API/email credentials.

Run:

```bash
python app.py
```

Open `http://127.0.0.1:5000`.

## Price checker

Run manually:

```bash
python checker.py
```

In production, Render Cron runs this automatically every 6 hours.

## Important

Never commit `.env` or your API keys to GitHub.
