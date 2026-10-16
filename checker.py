from db import db, init_db, DATABASE_URL
import os
import smtplib
from email.message import EmailMessage
from datetime import datetime, timezone
from dotenv import load_dotenv
from services.flight_api import search_flights
from services.api_guardian import (
    guardian,
    PROVIDER_SERPAPI,
    CALLER_BACKGROUND_CHECK,
    STATE_OPEN,
    get_guardian_config,
)

load_dotenv()


def send_email(to_email, subject, body):
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USERNAME")
    password = os.getenv("SMTP_PASSWORD")

    if not all([host, username, password]):
        print("Email is not configured.")
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = username
    msg["To"] = to_email
    msg.set_content(body)

    with smtplib.SMTP(host, port) as server:
        server.starttls()
        server.login(username, password)
        server.send_message(msg)


def run():
    init_db()

    # 1. Distributed Cron Lock: Prevent overlapping scheduled checker executions
    if not guardian.acquire_cron_lock("price_checker", lock_ttl_seconds=900):
        print("Price check skipped: Another instance is currently active (distributed cron lock held).")
        return

    try:
        # 2. Check Circuit Breaker State: Stop before sending requests if provider is down
        circuit_state, cooldown_until = guardian.get_circuit_state(PROVIDER_SERPAPI)
        if circuit_state == STATE_OPEN:
            print(f"Price check skipped: SerpAPI circuit breaker is OPEN until {cooldown_until} due to rate limits or errors.")
            return

        # 3. Check Background Quota Budget: Stop if monthly/daily limit or reserved quota reached
        allowed, reason, stats = guardian.check_budget(PROVIDER_SERPAPI, CALLER_BACKGROUND_CHECK)
        if not allowed:
            print(f"Price check skipped: Background quota budget limit reached ({reason}).")
            return

        conn = db()
        tracked = conn.execute(
            "SELECT * FROM tracked_flights"
        ).fetchall()

        if not tracked:
            print("No tracked flights in database.")
            conn.close()
            return

        # 4. Deduplicate Itineraries: Group tracked flights by (departure, arrival, outbound_date, return_date)
        # Prevents redundant identical API calls across multiple users tracking the same flight
        groups = {}
        for flight in tracked:
            ret_date_val = flight["return_date"] if "return_date" in flight.keys() else None
            key = (
                str(flight["departure"]).strip().upper(),
                str(flight["arrival"]).strip().upper(),
                str(flight["outbound_date"]).strip(),
                str(ret_date_val).strip() if ret_date_val else None
            )
            if key not in groups:
                groups[key] = []
            groups[key].append(flight)

        cfg = get_guardian_config()
        max_calls_per_run = cfg.get("background_check_max_calls_per_run", 10)
        calls_made = 0

        for (dep, arr, out_date, ret_date), flight_list in groups.items():
            # Check per-run calls limit
            if calls_made >= max_calls_per_run:
                print(f"Reached background-check call budget limit ({max_calls_per_run} calls) for this run. Deferring remaining {len(groups) - calls_made} itineraries.")
                break

            # Re-check circuit breaker state
            c_state, _ = guardian.get_circuit_state(PROVIDER_SERPAPI)
            if c_state == STATE_OPEN:
                print("SerpAPI circuit breaker tripped during run. Halting remaining checks.")
                break

            # Re-check budget
            budget_ok, b_reason, _ = guardian.check_budget(PROVIDER_SERPAPI, CALLER_BACKGROUND_CHECK)
            if not budget_ok:
                print(f"Background budget exhausted during run ({b_reason}). Halting remaining checks.")
                break

            now_iso = datetime.now(timezone.utc).isoformat()

            # Record attempt timestamp for every tracker in this group
            for flight in flight_list:
                try:
                    if DATABASE_URL:
                        conn.execute(
                            "UPDATE tracked_flights SET last_attempted_at = %s WHERE id = %s",
                            (now_iso, flight["id"])
                        )
                    else:
                        conn.execute(
                            "UPDATE tracked_flights SET last_attempted_at = ? WHERE id = ?",
                            (now_iso, flight["id"])
                        )
                except Exception:
                    pass

            try:
                print(f"\nChecking {dep} → {arr} ({out_date}{f' / {ret_date}' if ret_date else ''}) for {len(flight_list)} tracker(s)...")

                try:
                    results = search_flights(
                        dep,
                        arr,
                        out_date,
                        ret_date,
                        caller_type=CALLER_BACKGROUND_CHECK
                    )
                except TypeError:
                    results = search_flights(dep, arr, out_date, ret_date)

                calls_made += 1

                if not results:
                    print(f"No flight results found for {dep} → {arr}.")
                    continue

                # Ensure simulated fares and unconfigured states do NOT trigger price alerts or record false history
                if any(r.get("is_simulated") for r in results) or not os.getenv("SERPAPI_KEY"):
                    print(
                        f"Notice: Live SerpAPI pricing unavailable for {dep} → {arr}. "
                        f"Skipping price alert and history recording to prevent misleading notifications."
                    )
                    continue

                valid_prices = [r["price"] for r in results if r.get("price") is not None]
                if not valid_prices:
                    print(f"No valid prices in results for {dep} → {arr}.")
                    continue

                current_price = min(valid_prices)
                print(f"Current lowest price: ₹{current_price:,.0f}")

                # Process all trackers for this route
                for flight in flight_list:
                    # Save price history
                    if DATABASE_URL:
                        conn.execute(
                            """
                            INSERT INTO price_history
                            (tracked_id, price, checked_at)
                            VALUES (%s, %s, %s)
                            """,
                            (flight["id"], current_price, now_iso)
                        )
                    else:
                        conn.execute(
                            """
                            INSERT INTO price_history
                            (tracked_id, price, checked_at)
                            VALUES (?, ?, ?)
                            """,
                            (flight["id"], current_price, now_iso)
                        )

                    old_lowest = flight["lowest_price"]
                    new_lowest = current_price if old_lowest is None else min(current_price, old_lowest)

                    # Update lowest price, last price, and last_successful_at
                    if DATABASE_URL:
                        conn.execute(
                            """
                            UPDATE tracked_flights
                            SET lowest_price = %s,
                                last_price = %s,
                                last_successful_at = %s
                            WHERE id = %s
                            """,
                            (new_lowest, current_price, now_iso, flight["id"])
                        )
                    else:
                        conn.execute(
                            """
                            UPDATE tracked_flights
                            SET lowest_price = ?,
                                last_price = ?,
                                last_successful_at = ?
                            WHERE id = ?
                            """,
                            (new_lowest, current_price, now_iso, flight["id"])
                        )

                    # Determine alert conditions
                    is_price_drop = (
                        old_lowest is not None
                        and current_price < old_lowest
                    )

                    reached_target = (
                        current_price <= flight["target_price"]
                        and flight["target_alert_sent"] == 0
                    )

                    if reached_target or is_price_drop:
                        alert_reason = "TARGET PRICE REACHED" if reached_target else "CONFIRMED FARE DROP"
                        subject = f"Flight Price Alert: {flight['departure']} → {flight['arrival']}"
                        body = f"""FLIGHT PRICE ALERT

{alert_reason}

Route:
{flight['departure']} → {flight['arrival']}

Departure:
{flight['outbound_date']}

Current lowest price:
₹{current_price:,.0f}

Your target price:
₹{flight['target_price']:,.0f}
"""
                        if old_lowest is not None:
                            body += f"""
Previous lowest price:
₹{old_lowest:,.0f}

Net fare decrease:
₹{(old_lowest - current_price):,.0f}
"""
                        body += """
Book soon if this price works for you.

— SkyTrack Flight Intelligence
"""
                        send_email(flight["email"], subject, body)
                        print(f"Alert email sent to {flight['email']}!")

                        if reached_target:
                            if DATABASE_URL:
                                conn.execute(
                                    "UPDATE tracked_flights SET target_alert_sent = 1 WHERE id = %s",
                                    (flight["id"],)
                                )
                            else:
                                conn.execute(
                                    "UPDATE tracked_flights SET target_alert_sent = 1 WHERE id = ?",
                                    (flight["id"],)
                                )
                    else:
                        if old_lowest is None:
                            print("Initial baseline price recorded. No alert dispatched.")
                        else:
                            print("No alert needed.")

            except Exception as exc:
                print(f"Error checking itinerary {dep} → {arr}: {exc}")

        conn.commit()
        conn.close()
        print("\nPrice check completed.")

    finally:
        guardian.release_cron_lock("price_checker")


if __name__ == "__main__":
    run()