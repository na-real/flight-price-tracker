# ✈️ SkyTrack — Flight Price Tracker
[![Live Demo](https://img.shields.io/badge/Live-Demo-success?style=for-the-badge)](https://flight-price-tracker-30xk.onrender.com)
[![GitHub](https://img.shields.io/badge/GitHub-Repository-black?style=for-the-badge&logo=github)](https://github.com/na-real/flight-price-tracker)

A full-stack flight price tracking application built with Flask and JavaScript.

SkyTrack lets users search flights, compare prices, track specific routes, view price history, and receive email alerts when prices drop or reach a target price.

## ✨ Features

- ✈️ Search flights using airport codes
- 🔄 One-way and round-trip searches
- 🛫 Connecting flight and layover information
- 📍 Nearby airport search
- 💰 Sort and compare flight prices
- 🔔 Track flights with target price alerts
- 📉 New lowest-price alerts
- 📊 Historical price charts
- 📧 Email notifications
- ⏰ Automated price checking with Render Cron
- 🔐 Environment-based API key management
- 🧪 Automated tests with pytest
- ⚙️ GitHub Actions CI

## 🛠️ Tech Stack

### Backend

- Python
- Flask
- SQLite
- SerpAPI

### Frontend

- HTML
- CSS
- JavaScript
- Chart.js

### Testing & Deployment

- pytest
- GitHub Actions
- Render
- Render Cron

## 📂 Project Structure

```text
flight-price-tracker/
│
├── app.py
├── checker.py
├── db.py
├── validation.py
├── requirements.txt
├── render.yaml
├── README.md
│
├── data/
│   └── airports.csv
│
├── services/
│   ├── airports.py
│   └── flight_api.py
│
├── static/
│   ├── app.js
│   └── style.css
│
├── templates/
│   └── index.html
│
├── tests/
│   ├── test_airports.py
│   ├── test_db.py
│   ├── test_flight_api.py
│   ├── test_routes.py
│   └── test_validation.py
│
└── .github/
    └── workflows/
        └── ci.yml
```md
## 🏗️ Architecture

```mermaid
flowchart TD
    U[User] --> F[SkyTrack Frontend]
    F --> A[Flask Backend]

    A --> S[SerpAPI<br/>Google Flights]
    A --> DB[(Supabase<br/>PostgreSQL)]

    G[GitHub Actions<br/>Hourly Checker] --> A
    G --> DB
    G --> S
    G --> E[Gmail SMTP<br/>Price Alerts]
    R[Render<br/>Gunicorn] --> A
