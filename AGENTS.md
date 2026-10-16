# SkyTracker — AI Agent Operating Instructions (`AGENTS.md`)

Welcome, AI Agent (Google Antigravity, Cursor, Claude, GitHub Copilot).

Before analyzing, planning, generating code, or executing any changes in this repository, you **MUST** load and follow the **Five Permanent Foundational Rulebooks** established in the root directory:

1. **[PRD.md](file:///c:/Users/Vandita%20Mamgain/Downloads/flight-price-tracker/PRD.md)** — **What must be built.** Defines product vision, core flight search and tracking features, functional requirements, user journeys, and roadmap boundaries.
2. **[Architecture.md](file:///c:/Users/Vandita%20Mamgain/Downloads/flight-price-tracker/Architecture.md)** — **How the system is built.** Documents technology stack, directory organization, request flows, API contracts, database schemas, resilience patterns, and security architecture.
3. **[Rules.md](file:///c:/Users/Vandita%20Mamgain/Downloads/flight-price-tracker/Rules.md)** — **How AI agents must work.** Enforces strict repository rules, coding standards (Python/JS), secret protection, parameterized SQL queries, rate limiting, and the 10-step execution workflow.
4. **[Design.md](file:///c:/Users/Vandita%20Mamgain/Downloads/flight-price-tracker/Design.md)** — **How the product looks and behaves.** Mandates the Surreal Quiet Luxury aesthetic, Obsidian Noir & Warm Alabaster design tokens, typography, 3D WebGL motion, and responsive standards.
5. **[Task.md](file:///c:/Users/Vandita%20Mamgain/Downloads/flight-price-tracker/Task.md)** — **What to work on next and how to verify completion.** Provides current project health (78/78 tests passing), prioritized backlog (P0–P3), task execution template, and Definition of Done.

---

## Mandatory AI Agent Workflow

Every session and code change must follow this sequence:

1. **Pre-Flight Inspection:**
   - Review relevant sections of `PRD.md`, `Architecture.md`, `Rules.md`, `Design.md`, and `Task.md`.
   - Inspect existing implementation files before creating or modifying code. Never guess routes, schemas, or CSS classes.
2. **Surgical Implementation:**
   - Preserve existing functionality, user changes, and non-conflicting logic.
   - Use parameterized SQL queries for all database interactions.
   - Reuse existing utilities: `services/airports.py` for airports, `security.py` for rate limits and deletion tokens, `validation.py` for inputs.
   - Adhere to the Surreal Quiet Luxury tokens in `Design.md` (no generic SaaS styling, no arbitrary emojis).
3. **Automated Verification:**
   - Always run the automated test suite before reporting completion:
     ```powershell
     python -m pytest tests/
     ```
   - 100% of tests must pass. Never declare completion if tests fail or have not been executed.
4. **Git Diff & Clean Sign-Off:**
   - Review `git status` and `git diff` to ensure no sensitive credentials, `.env` files, or unintended files were modified.
   - Report what was modified, how it was verified, and any next steps matching `Task.md`.
