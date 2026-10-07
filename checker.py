from db import db, init_db, DATABASE_URL
import os
import smtplib
from email.message import EmailMessage
from datetime import datetime, timezone
from dotenv import load_dotenv
from services.flight_api import search_flights

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
    conn = db()

    tracked = conn.execute(
        "SELECT * FROM tracked_flights"
    ).fetchall()

    for flight in tracked:

        try:
            print(
                f"\nChecking {flight['departure']} → "
                f"{flight['arrival']}..."
            )

            results = search_flights(
                flight["departure"],
                flight["arrival"],
                flight["outbound_date"],
                flight["return_date"]
            )

            if not results:
                print("No flight results found.")
                continue

            # Find cheapest current flight
            current_price = min(
                r["price"]
                for r in results
                if r["price"] is not None
            )

            old_lowest = flight["lowest_price"]

            print(f"Current lowest price: ₹{current_price:,.0f}")

            # Save price history
            if DATABASE_URL:
                conn.execute(
                    """
                    INSERT INTO price_history
                    (tracked_id, price, checked_at)
                    VALUES (%s, %s, %s)
                    """,
                    (
                        flight["id"],
                        current_price,
                        datetime.now(timezone.utc).isoformat()
                    )
                )

            else:
                conn.execute(
                    """
                    INSERT INTO price_history
                    (tracked_id, price, checked_at)
                    VALUES (?, ?, ?)
                    """,
                    (
                        flight["id"],
                        current_price,
                        datetime.now(timezone.utc).isoformat()
                    )
                )

            # Determine whether this is a new record low
            is_new_low = (
                old_lowest is None
                or current_price < old_lowest
            )

            # Determine whether target has been reached
            reached_target = (
                current_price <= flight["target_price"]
                and flight["target_alert_sent"] == 0
            )

            # Update lowest price
            if old_lowest is None:
                new_lowest = current_price
            else:
                new_lowest = min(
                    current_price,
                    old_lowest
                )

            if DATABASE_URL:
                conn.execute(
                    """
                    UPDATE tracked_flights
                    SET lowest_price = %s,
                        last_price = %s
                    WHERE id = %s
                    """,
                    (
                        new_lowest,
                        current_price,
                        flight["id"]
                    )
                )

            else:
                conn.execute(
                    """
                    UPDATE tracked_flights
                    SET lowest_price = ?,
                        last_price = ?
                    WHERE id = ?
                    """,
                    (
                        new_lowest,
                        current_price,
                        flight["id"]
                    )
                )

            # -----------------------------------------
            # SEND ALERT
            # -----------------------------------------

            if is_new_low or reached_target:

                if reached_target:
                    alert_reason = "🎯 TARGET PRICE REACHED"
                else:
                    alert_reason = "📉 NEW LOWEST PRICE"

                subject = (
                    f"✈️ Flight Price Alert: "
                    f"{flight['departure']} → "
                    f"{flight['arrival']}"
                )

                if old_lowest is not None:
                    body = f"""
✈️ FLIGHT PRICE ALERT

{alert_reason}

Route:
{flight['departure']} → {flight['arrival']}

Departure:
{flight['outbound_date']}

Current lowest price:
₹{current_price:,.0f}

Your target price:
₹{flight['target_price']:,.0f}

Previous lowest price:
₹{old_lowest:,.0f}
"""
                else:
                    body = f"""
✈️ FLIGHT PRICE ALERT

📉 FIRST PRICE RECORDED

Route:
{flight['departure']} → {flight['arrival']}

Departure:
{flight['outbound_date']}

Current lowest price:
₹{current_price:,.0f}

Your target price:
₹{flight['target_price']:,.0f}

This is the first price recorded for this tracked flight.
"""

                body += """

Book soon if this price works for you.

— Flight Price Tracker
"""

                send_email(
                    flight["email"],
                    subject,
                    body
                )

                print("🔔 Alert email sent!")

            else:
                print("No alert needed.")

            # Once target has been reached,
            # never send another TARGET alert.
            if reached_target:

                if DATABASE_URL:
                    conn.execute(
                        """
                        UPDATE tracked_flights
                        SET target_alert_sent = 1
                        WHERE id = %s
                        """,
                        (flight["id"],)
                    )

                else:
                    conn.execute(
                        """
                        UPDATE tracked_flights
                        SET target_alert_sent = 1
                        WHERE id = ?
                        """,
                        (flight["id"],)
                    )

        except Exception as exc:
            print(
                f"❌ Tracker {flight['id']} failed: {exc}"
            )

    conn.commit()
    conn.close()

    print("\n✅ Price check completed.")


if __name__ == "__main__":
    run()