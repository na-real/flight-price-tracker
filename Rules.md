# SkyTracker — AI Engineering & Coding Rulebook (`Rules.md`)

**Document Version:** 1.0.0 (Permanent Governance)
**Applies To:** Google Antigravity, Cursor, Claude, GitHub Copilot, and Human Engineers
**System:** SkyTracker (Haute Flight Intelligence & Price Tracking)
**Companion Documents:** [PRD.md](file:///c:/Users/Vandita%20Mamgain/Downloads/flight-price-tracker/PRD.md) · [Architecture.md](file:///c:/Users/Vandita%20Mamgain/Downloads/flight-price-tracker/Architecture.md) · [Design.md](file:///c:/Users/Vandita%20Mamgain/Downloads/flight-price-tracker/Design.md) · [Task.md](file:///c:/Users/Vandita%20Mamgain/Downloads/flight-price-tracker/Task.md)

---

## 1. Governance & Purpose

This document establishes the **permanent, non-negotiable operational contract** for all AI coding agents and developers working in the SkyTracker repository.

SkyTracker is not an experimental demo or a disposable prototype. It is a production-grade, hardened flight search and price surveillance engine featuring a bespoke surrealist luxury design system, defense-in-depth security architecture, and automated regression test coverage.

**Every modification made by an AI agent must strictly conform to the rules set forth in this document.**

---

## 2. Core Repository Rules

### 2.1. Read Before Modifying
Before executing code edits or proposing changes:
1. Read the corresponding section in [PRD.md](file:///c:/Users/Vandita%20Mamgain/Downloads/flight-price-tracker/PRD.md) to understand feature boundaries and acceptance criteria.
2. Review [Architecture.md](file:///c:/Users/Vandita%20Mamgain/Downloads/flight-price-tracker/Architecture.md) to preserve system flows, API contracts, and database models.
3. Inspect [Design.md](file:///c:/Users/Vandita%20Mamgain/Downloads/flight-price-tracker/Design.md) to adhere to the Obsidian Noir / Warm Alabaster aesthetic tokens.
4. Check [Task.md](file:///c:/Users/Vandita%20Mamgain/Downloads/flight-price-tracker/Task.md) for existing task status and definition of done.

### 2.2. Inspect Existing Implementations First
- Always inspect existing files before creating new ones.
- Check [app.py](file:///c:/Users/Vandita%20Mamgain/Downloads/flight-price-tracker/app.py) for existing route definitions and handlers.
- Check [services/](file:///c:/Users/Vandita%20Mamgain/Downloads/flight-price-tracker/services/) for provider aggregators, airport lookups, and notification services.
- Check [static/app.js](file:///c:/Users/Vandita%20Mamgain/Downloads/flight-price-tracker/static/app.js) for frontend state management, DOM event listeners, and drawer controllers.
- Check [static/style.css](file:///c:/Users/Vandita%20Mamgain/Downloads/flight-price-tracker/static/style.css) for existing design tokens and utility classes.

### 2.3. Reuse, Do Not Reinvent
- **Airport Search:** Use `services/airports.py` (`search_airports()`, `get_airport()`, `find_nearby_airports()`). Do not write custom airport search logic.
- **Flight Providers:** Implement or extend `FlightProvider` in `services/providers/base.py` and register within `services/providers/aggregator.py`.
- **Database:** Use `db.get_db()` and `db.init_db()` in [db.py](file:///c:/Users/Vandita%20Mamgain/Downloads/flight-price-tracker/db.py). Never introduce parallel connection pools.
- **Security & Validation:** Use [security.py](file:///c:/Users/Vandita%20Mamgain/Downloads/flight-price-tracker/security.py) (`@rate_limit`, `generate_delete_token()`, `verify_delete_token()`) and [validation.py](file:///c:/Users/Vandita%20Mamgain/Downloads/flight-price-tracker/validation.py).
- **Icons:** Use Lucide icons via `<i data-lucide="..."></i>` with `lucide.createIcons()`. Do not add custom icon packs or inline raster images.

### 2.4. Surgical, Non-Destructive Modularity
- Never rewrite entire files when a targeted replacement chunk is sufficient.
- Never replace working code with placeholders (`// ... existing code ...` or `/* TODO */`).
- Preserve all existing user modifications and non-conflicting logic.

---

## 3. Coding Standards

### 3.1. Python & Backend Standards
- **Runtime:** Python 3.10+.
- **Style Guide:** Adhere to PEP 8 standards (4-space indentation, clear variable names, snake_case functions/methods, PascalCase classes).
- **Type Annotations:** Use Python type hints (`typing.Optional`, `typing.List`, `typing.Dict`, `typing.Tuple`) for all public function signatures.
- **Docstrings:** Provide concise docstrings explaining intent, arguments, return types, and potential exceptions.
- **Defensive Error Handling:** Catch specific exceptions (`ValueError`, `psycopg2.Error`, `sqlite3.Error`, `requests.RequestException`). Never use bare `except:` clauses.
- **Connection Hygiene:** All database cursors and connections must be cleanly disposed using `try ... finally: conn.close()` or context managers.
- **Logging vs. Print:** Use Python's standard `logging` module (`logger = logging.getLogger(__name__)`). Never leave raw `print()` statements in production services.

### 3.2. JavaScript & Frontend Standards
- **Runtime:** Vanilla Modern ES6+ (no heavy frontend framework bundles like React/Angular unless explicitly requested by user).
- **DOM Access Safety:** Always verify DOM nodes exist before querying or attaching listeners:
  ```javascript
  const targetEl = document.getElementById('targetId');
  if (!targetEl) return;
  ```
- **Event Delegation:** Use event delegation on dynamic containers (`#flightResults`, `#trackedFlightsList`, `#multiCityLegsContainer`).
- **No Global Scope Pollution:** Encapsulate modules or scope state cleanly within `SkyTrackerApp` object or self-executing modules.
- **Strict Linting Hygiene:** Remove unused imports, dead variables, and lingering `console.log()` debugging statements.

### 3.3. Asynchronous Operations
- Always handle rejected Promises or API network failures:
  ```javascript
  try {
      const res = await fetch(endpoint, options);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      return data;
  } catch (err) {
      console.error('Request failed:', err);
      showToast(err.message || 'Operation failed', 'error');
  }
  ```
- Implement request timeouts (10 seconds default for external API calls, 30 seconds for background cron checks).
- Provide visual feedback (spinners, skeletons, button disabled states) during async operations.

---

## 4. API & Data Integrity Rules

### 4.1. Secret Separation & Environmental Safety
- **Zero Frontend Secrets:** Never expose API keys (`SERPAPI_KEY`, `AMADEUS_CLIENT_SECRET`, `SECRET_KEY`, `SMTP_PASS`) in HTML templates, JavaScript files, client logs, or Git commits.
- **Configuration Loading:** Load configuration strictly from OS environment variables or local `.env` via `python-dotenv` in [config.py](file:///c:/Users/Vandita%20Mamgain/Downloads/flight-price-tracker/config.py).
- **Default Fallbacks:** Provide safe local development defaults for non-secret values, but strictly require explicit configuration for production secrets.

### 4.2. Input Validation & Scrubbing
- **Server-Side Primacy:** Client-side HTML5 validation is purely UX; every input must be strictly validated on the server via [validation.py](file:///c:/Users/Vandita%20Mamgain/Downloads/flight-price-tracker/validation.py).
- **Airport Codes:** Must be validated as 3-letter uppercase ASCII strings (`^[A-Z]{3}$`).
- **Dates:** Must be valid ISO format (`YYYY-MM-DD`). Departure date must be on or after current date; return date must be on or after departure date.
- **Passenger Constraints:** Adults must be 1–9; total passengers cannot exceed 9; infants cannot exceed adults.
- **Email Scrubbing:** Emails must conform to RFC 5322 regex and be scrubbed of `\r` and `\n` to prevent SMTP header injection attacks.

### 4.3. Data Integrity & Aggregation Realism
- **No Fictional Pricing:** Never generate fake or deceptive prices.
- **Graceful Fallbacks:** When external live providers (SerpApi) timeout or exceed quota, the system falls back to deterministic multi-provider heuristic estimates based on route distance and carrier tiers. These must be clearly indicated as aggregated market estimates.
- **Standardized Response Envelope:**
  ```json
  {
    "success": true,
    "departure": "LHR",
    "arrival": "HND",
    "results_count": 12,
    "flights": [...]
  }
  ```
  On failure:
  ```json
  {
    "success": false,
    "error": "Detailed validation or operational error message"
  }
  ```

---

## 5. Security Architecture Rules

### 5.1. Authentication & IDOR Protection
- **Email Privacy:** User emails must never be serialized in public JSON responses (e.g., `/api/tracked` returns masked or scrubbed payloads).
- **Cryptographic Deletion Tokens:** Route tracking deletions (`DELETE /api/tracked/<id>`) require a valid HMAC-SHA256 deletion token passed via the `X-Delete-Token` header.
- **Zero Unauthenticated Deletions:** Never delete a tracked flight record without validating its existence and verifying the cryptographic token or admin session.

### 5.2. Injection & Exploit Defense
- **Parameterized SQL:** Every database query in SQLite and PostgreSQL must use parameterized bindings (`?` for SQLite, `%s` for PostgreSQL). String interpolation or f-strings in SQL are strictly forbidden.
- **Rate Limiting:** Protect all sensitive endpoints (`/api/search`, `/api/track`, `/api/tracked`, `/api/airports`) with the thread-safe sliding-window `@rate_limit` decorator from [security.py](file:///c:/Users/Vandita%20Mamgain/Downloads/flight-price-tracker/security.py).
- **Bot Defense (Honeypot):** The tracking form must include a hidden honeypot field (`website_url`). Requests with populated honeypot values must be silently discarded with `200 OK` to sinkhole bots.
- **Payload Limits:** Clamp all request payloads to 1MB (`MAX_CONTENT_LENGTH = 1024 * 1024`).

### 5.3. HTTP Security Headers
All HTTP responses must carry the defense-in-depth headers enforced in `security.py`:
- `Content-Security-Policy`: Restricts scripts, styles, fonts, objects, and framing to trusted self and CDNs.
- `Strict-Transport-Security`: `max-age=31536000; includeSubDomains; preload` (on HTTPS).
- `X-Content-Type-Options`: `nosniff`.
- `X-Frame-Options`: `SAMEORIGIN`.
- `Referrer-Policy`: `strict-origin-when-cross-origin`.
- `Permissions-Policy`: Disables microphone, camera, geolocation.

---

## 6. UI & Design System Rules

### 6.1. Aesthetic Philosophy: Surreal Quiet Luxury
- **Visual Tone:** Minimalist surrealism, understated luxury, cinematic 3D motion design, ethereal lighting, editorial typography.
- **Prohibited Patterns:**
  - ❌ Do NOT use generic SaaS templates, garish neon colors, or bright purple gradients.
  - ❌ Do NOT use cartoon illustrations, stock vector clip-art, or decorative clutter.
  - ❌ Do NOT use arbitrary unicode emojis in UI copy (e.g., ✈️, 🚀, 🔥). Use sleek Lucide SVG icons instead.
  - ❌ Do NOT use harsh rectangular cards without border blends or glassmorphic blur.

### 6.2. Design Token Conformance
All styles must utilize CSS custom properties defined in [static/style.css](file:///c:/Users/Vandita%20Mamgain/Downloads/flight-price-tracker/static/style.css):
- **Obsidian Noir Backgrounds:** `var(--bg-void)` (`#04060b`), `var(--bg-base)` (`#060912`), `var(--bg-surface)` (`rgba(10, 15, 27, 0.68)`).
- **Warm Alabaster (Light Mode):** `var(--bg-void)` (`#f4f3ee`), `var(--bg-base)` (`#f7f6f1`).
- **Accents:** Liquid Champagne Gold `var(--accent-gold)` (`#dfc99e`), dim `var(--accent-gold-dim)`, border `var(--accent-gold-border)`.
- **Typography:**
  - Headings & Editorial Display: `var(--font-serif)` (`'Cormorant Garamond', 'Cinzel', serif`).
  - Body & UI Controls: `var(--font-sans)` (`'Plus Jakarta Sans', sans-serif`).
  - Flight Numbers, IATA Codes, Timestamps: `var(--font-mono)` (`'Space Mono', monospace`).

### 6.3. Responsive & Viewport Integrity
- **No Horizontal Overflow:** The page must never exhibit horizontal scroll (`overflow-x: clip; max-width: 100%`).
- **Supported Viewports:** 320px (iPhone SE), 375px/390px/430px (modern smartphones), 768px–1024px (tablets), 1280px+ (desktop).
- **Touch Targets:** All interactive buttons, inputs, pills, and toggles must maintain minimum **44x44px touch targets** on mobile devices.

### 6.4. State Completeness
Every interactive and data-driven component must explicitly provide:
1. **Loading State:** Smooth skeleton shimmer or ethereal orbital pulse.
2. **Empty State:** Restrained editorial typography with a subtle Lucide icon and helpful guidance.
3. **Error State:** Subtle crimson border (`var(--danger)` / `#f87171`), clear human-readable explanation, and retry action.
4. **Success State:** Instant visual confirmation with Champagne Gold accent feedback.

---

## 7. Testing & Quality Assurance Rules

### 7.1. Test Execution Mandate
- **Rule:** Never declare a feature or bug fix completed without running the test suite:
  ```powershell
  python -m pytest tests/
  ```
- **Passing Threshold:** 100% of existing tests must pass. Zero regressions permitted.
- **Reporting:** In every turn, explicitly cite test execution output. Never invent test results.

### 7.2. Adding Regression Tests
- Any bug fix must be accompanied by an automated regression test in `tests/` reproducing the edge case.
- Any new endpoint must be covered in `tests/test_routes.py`.
- Any new provider must be tested in `tests/test_price_comparison.py`.

---

## 8. Git & Workflow Rules

### 8.1. Commit Hygiene & Clean Diffs
- Do not make automatic git commits unless explicitly directed by the user.
- Always review the git diff (`git diff --stat`) before concluding a task to verify no unintended files were modified.
- Never commit `.env`, local SQLite database files (`flights.db`), bytecode (`__pycache__`), or virtual environment directories.

### 8.2. Ten-Step AI Agent Execution Workflow

When tasked with any modification in SkyTracker, follow these sequential steps:

1. **Understand:** Read and decompose the user's prompt into atomic requirements.
2. **Consult Rulebook:** Review relevant sections of `PRD.md`, `Architecture.md`, `Rules.md`, `Design.md`, and `Task.md`.
3. **Inspect Codebase:** Search existing code, inspect routes, check models, and identify reusable functions.
4. **Isolate Root Cause:** For bugs, verify the exact failure mechanism before modifying code.
5. **Formulate Minimal Plan:** Design the smallest, safest surgical change that solves the requirement.
6. **Execute Change:** Apply code modifications cleanly, respecting existing formatting and naming conventions.
7. **Verify Tests:** Run `python -m pytest tests/` to confirm zero regressions.
8. **Verify UI/Browser:** If modifying styles or frontend JS, verify visual alignment and responsive layout.
9. **Update Documentation:** If new endpoints, schemas, or design tokens were introduced, update the corresponding documentation files.
10. **Report Results:** Present a clear, structured summary citing files modified, test outputs, and verification status.

---

*This rulebook is permanent. Adhere to it in every session.*
