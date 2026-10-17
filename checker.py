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
    STATE_QUOTA_EXHAUSTED,
    get_guardian_config,
)

from services.email_service import (
    build_price_drop_email,
    build_service_interruption_email,
    send_email as svc_send_email,
)
from services.self_healing_agent import agent as self_healing_agent, SUBSYSTEM_CHECKER

load_dotenv()


def send_email(to_email, subject, body, html_body=None):
    """SMTP email sender delegating to professional email_service."""
    return svc_send_email(to_email, subject, body, html_body)


def _dispatch_email(to_email, subject, body, html_body=None):
    """Safely invoke send_email even if monkeypatched with legacy 3-argument signature."""
    try:
        import inspect
        sig = inspect.signature(send_email)
        params = list(sig.parameters.values())
        if len(params) == 3 and all(p.default == inspect.Parameter.empty for p in params):
            return send_email(to_email, subject, body)
    except Exception:
        pass
    try:
        return send_email(to_email, subject, body, html_body=html_body)
    except TypeError:
        return send_email(to_email, subject, body)


def run():
    init_db()

    # 1. Distributed Cron Lock: Prevent overlapping scheduled checker executions
    if not guardian.acquire_cron_lock("price_checker", lock_ttl_seconds=900):
        print("Price check skipped: Another instance is currently active (distributed cron lock held).")
        return

    try:
        from services.providers import get_primary_provider
        primary_provider = get_primary_provider()
    except Exception as e:
        print(f"Price check skipped: Unable to resolve primary provider: {e}")
        return

    provider_id = primary_provider.provider_id
    provider_name = primary_provider.provider_name

    try:
        # 2. Check Circuit Breaker State: Stop before sending requests if provider is down or quota exhausted
        circuit_state, cooldown_until = guardian.get_circuit_state(provider_id)
        if circuit_state in (STATE_OPEN, STATE_QUOTA_EXHAUSTED):
            cool_desc = f" until {cooldown_until}" if cooldown_until else " (reset time unknown)"
            print(f"Price check skipped: {provider_name} quota exhausted / circuit is {circuit_state}{cool_desc}.")
            return

        # 3. Check Background Quota Budget: Stop if monthly/daily limit or reserved quota reached
        allowed, reason, stats = guardian.check_budget(provider_id, CALLER_BACKGROUND_CHECK)
        if not allowed:
            print(f"Price check skipped: Background quota budget limit reached for {provider_name} ({reason}).")
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
            c_state, _ = guardian.get_circuit_state(provider_id)
            if c_state in (STATE_OPEN, STATE_QUOTA_EXHAUSTED):
                print(f"{provider_name} quota exhausted or circuit breaker open during run. Halting remaining checks.")
                break

            # Re-check budget
            budget_ok, b_reason, _ = guardian.check_budget(provider_id, CALLER_BACKGROUND_CHECK)
            if not budget_ok:
                print(f"Background budget exhausted for {provider_name} during run ({b_reason}). Halting remaining checks.")
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
                conn.commit()
            except Exception:
                pass

            try:
                print(f"\nChecking {dep} -> {arr} ({out_date}{f' / {ret_date}' if ret_date else ''}) for {len(flight_list)} tracker(s)...")

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
                except Exception as search_exc:
                    print(f"Search failed for {dep} -> {arr}: {search_exc}. Skipping price alert and history recording.")
                    continue

                calls_made += 1

                if not results:
                    print(f"No flight results found for {dep} -> {arr}.")
                    continue

                # Ensure simulated fares, stale cached data, and unconfigured states do NOT trigger price alerts or record false history
                if (
                    any(r.get("is_simulated") for r in results)
                    or all(r.get("is_cached") for r in results)
                    or not primary_provider.is_configured()
                ):
                    print(
                        f"Notice: Fresh live pricing unavailable for {dep} -> {arr} (cached or unconfigured). "
                        f"Skipping price alert and history recording to prevent false notifications."
                    )
                    continue

                valid_prices = [r["price"] for r in results if r.get("price") is not None]
                if not valid_prices:
                    print(f"No valid prices in results for {dep} -> {arr}.")
                    continue

                current_price = min(valid_prices)
                print(f"Current lowest price: INR {current_price:,.0f}")

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

                    # Update lowest price, last price, last_successful_at, and clear last_interrupted_at
                    if DATABASE_URL:
                        conn.execute(
                            """
                            UPDATE tracked_flights
                            SET lowest_price = %s,
                                last_price = %s,
                                last_successful_at = %s,
                                last_interrupted_at = NULL
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
                                last_successful_at = ?,
                                last_interrupted_at = NULL
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
                        best_offer = None
                        matching_offers = [r for r in results if r.get("price") == current_price]
                        if matching_offers:
                            best_offer = matching_offers[0]

                        flight_details = None
                        if best_offer and not best_offer.get("is_simulated") and best_offer.get("data_status") == "live":
                            flight_details = {
                                "airline": best_offer.get("airline"),
                                "flight_number": best_offer.get("flight_number"),
                                "departure_time": best_offer.get("departure_time"),
                                "arrival_time": best_offer.get("arrival_time")
                            }

                        subject, body, html_body = build_price_drop_email(
                            to_email=flight["email"],
                            departure_iata=flight["departure"],
                            arrival_iata=flight["arrival"],
                            outbound_date=flight["outbound_date"],
                            current_price=current_price,
                            previous_price=old_lowest,
                            target_price=flight["target_price"],
                            flight_details=flight_details,
                            checked_at=now_iso,
                            tracking_id=flight["id"]
                        )
                        _dispatch_email(flight["email"], subject, body, html_body)
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
                print(f"Error checking itinerary {dep} -> {arr}: {exc}")
                try:
                    import traceback
                    self_healing_agent.detect_incident(
                        subsystem=SUBSYSTEM_CHECKER,
                        error=exc,
                        status_code=500,
                        traceback_str=traceback.format_exc(),
                        context={"departure": dep, "arrival": arr, "outbound_date": out_date}
                    )
                except Exception:
                    pass
                for flight in flight_list:
                    last_interrupted = flight["last_interrupted_at"] if "last_interrupted_at" in flight.keys() else None
                    last_successful = flight["last_successful_at"] if "last_successful_at" in flight.keys() else None
                    if not last_interrupted or (last_successful and last_successful > last_interrupted):
                        try:
                            i_subj, i_body, i_html = build_service_interruption_email(
                                to_email=flight["email"],
                                departure_iata=dep,
                                arrival_iata=arr,
                                reason="Temporary upstream carrier data feed synchronization delay",
                                last_successful_at=last_successful
                            )
                            _dispatch_email(flight["email"], i_subj, i_body, i_html)
                            if DATABASE_URL:
                                conn.execute("UPDATE tracked_flights SET last_interrupted_at = %s WHERE id = %s", (now_iso, flight["id"]))
                            else:
                                conn.execute("UPDATE tracked_flights SET last_interrupted_at = ? WHERE id = ?", (now_iso, flight["id"]))
                            try:
                                conn.commit()
                            except Exception:
                                pass
                        except Exception as notif_exc:
                            print(f"Notice: Failed to dispatch interruption notice: {notif_exc}")

            try:
                conn.commit()
            except Exception:
                pass

        conn.commit()
        conn.close()
        print("\nPrice check completed.")

    finally:
        guardian.release_cron_lock("price_checker")


if __name__ == "__main__":
    run()