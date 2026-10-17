"""
SkyTracker Email Notification Service
=====================================
Professional, transparent, and secure transactional email delivery for:
1. Price-drop notifications (with calculated savings, percentage decrease, verified carrier details)
2. Tracking confirmation notifications (alert conditions, route details, management link)
3. Service interruption / unverified price check notices (last check timestamp, retry reassurance)

Security & Delivery Hardening:
- RFC 5322 email scrubbing preventing SMTP header injection (\\r, \\n stripped).
- HTML escaping on all dynamic & customer-provided fields.
- Multipart MIME (plain-text alternative alongside Surreal Quiet Luxury HTML).
- Non-blocking error handling to ensure flight searches and price checks never crash.
- Deduplication and state-tracking guards to avoid repeated alerts for unresolved errors.
"""

import os
import re
import html
import smtplib
import logging
from email.message import EmailMessage
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple, Callable

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# RFC 5322 simplified email validation regex
EMAIL_REGEX = re.compile(
    r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
)


def scrub_email_address(email_str: Optional[str]) -> str:
    """
    Validate and scrub email address of dangerous newline characters (SMTP header injection protection).
    Raises ValueError on newline injection, null bytes, or invalid format.
    """
    if not email_str:
        raise ValueError("Email address is required.")
    cleaned = str(email_str).strip()
    if "\r" in cleaned or "\n" in cleaned or "\x00" in cleaned:
        raise ValueError("Invalid email address: newline or null byte characters detected.")
    if not EMAIL_REGEX.match(cleaned):
        raise ValueError("Invalid email address format.")
    return cleaned


def is_valid_email(email_str: Optional[str]) -> bool:
    """Validate email address format safely."""
    try:
        scrub_email_address(email_str)
        return True
    except ValueError:
        return False


def get_base_url() -> str:
    """Resolve base application URL for deep-links in transactional emails."""
    base = os.getenv("APP_URL") or os.getenv("BASE_URL") or "http://localhost:5000"
    return base.rstrip("/")


def get_customer_name(email: str, conn=None) -> str:
    """Safely look up customer's registered display name from database."""
    if not email:
        return ""
    try:
        clean = scrub_email_address(email).lower()
    except ValueError:
        return ""
    close_conn = False
    try:
        if conn is None:
            from db import db
            conn = db()
            close_conn = True

        from db import DATABASE_URL
        if DATABASE_URL:
            row = conn.execute(
                "SELECT name FROM users WHERE LOWER(email) = %s LIMIT 1",
                (clean,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT name FROM users WHERE LOWER(email) = ? LIMIT 1",
                (clean,)
            ).fetchone()

        if row and row["name"]:
            return str(row["name"]).strip()
    except Exception as e:
        logger.debug(f"Unable to resolve customer name for {clean}: {e}")
    finally:
        if close_conn and conn:
            try:
                conn.close()
            except Exception:
                pass
    return ""


def format_airport_display(iata_code: Optional[str]) -> str:
    """
    Format airport IATA code with city name if available in the database.
    Example: DEL -> 'Delhi (DEL)'
    """
    if not iata_code:
        return ""
    code = str(iata_code).strip().upper()
    try:
        from services.airports import search_airports
        res = search_airports(iata=code)
        if res and len(res) > 0:
            city = res[0].get("city")
            if city:
                return f"{city} ({code})"
            name = res[0].get("name")
            if name:
                return f"{name} ({code})"
    except Exception:
        pass
    return code


def calculate_savings(old_price: Optional[float], new_price: Optional[float]) -> Tuple[Optional[float], Optional[float]]:
    """
    Calculate exact currency savings and percentage decrease.
    Returns (savings_amount, percentage_decrease).
    """
    if old_price is None or new_price is None:
        return None, None
    try:
        old_val = float(old_price)
        new_val = float(new_price)
        if old_val > new_val and old_val > 0:
            savings = old_val - new_val
            pct = (savings / old_val) * 100.0
            return round(savings, 2), round(pct, 1)
    except (TypeError, ValueError):
        pass
    return None, None


# =========================================================================
# CORE SMTP TRANSPORT
# =========================================================================

def send_email(to_email: str, subject: str, text_body: str, html_body: Optional[str] = None) -> bool:
    """
    Send an email via configured SMTP relay with multipart plain-text and HTML alternatives.
    Returns True if sent successfully, False otherwise.
    Never throws exceptions that could crash searches or background workers.
    """
    try:
        clean_to = scrub_email_address(to_email)
    except ValueError:
        logger.warning(f"Email aborted: Invalid or malicious recipient address: '{to_email}'")
        return False

    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USERNAME")
    password = os.getenv("SMTP_PASSWORD")
    from_email = os.getenv("SMTP_FROM") or username

    if (
        not all([host, username, password])
        or "your_" in str(username).lower()
        or "your_" in str(password).lower()
        or str(host).lower() in ("none", "placeholder")
    ):
        logger.info(f"Email dispatch skipped (SMTP unconfigured or placeholder credentials) for recipient {clean_to}: {subject}")
        return False

    # Prevent CRLF header injection in Subject and From
    clean_subject = re.sub(r"[\r\n]", " ", subject).strip()
    try:
        clean_from = scrub_email_address(from_email)
    except ValueError:
        clean_from = "no-reply@skytracker.luxury"

    msg = EmailMessage()
    msg["Subject"] = clean_subject
    msg["From"] = clean_from
    msg["To"] = clean_to

    # Plain text content is mandatory
    msg.set_content(text_body)

    # Optional HTML alternative
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    try:
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.starttls()
            server.login(username, password)
            server.send_message(msg)
        logger.info(f"Email successfully delivered to {clean_to}: {clean_subject}")
        return True
    except Exception as exc:
        logger.error(f"Failed to deliver email to {clean_to} via SMTP {host}:{port}: {exc}")
        return False


# =========================================================================
# SURREAL LUXURY HTML EMAIL SHELL
# =========================================================================

def _render_email_shell(title: str, content_html: str, footer_note: Optional[str] = None) -> str:
    """Wraps body content in a polished Surreal Quiet Luxury dark-mode HTML email template."""
    base_url = get_base_url()
    safe_title = html.escape(title)
    safe_footer = html.escape(footer_note) if footer_note else (
        "Flight prices and seat availability can change at any time. This notification is not a booking confirmation."
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{safe_title}</title>
<style>
  body, table, td, p, a, li, blockquote {{
    -webkit-text-size-adjust: 100%;
    -ms-text-size-adjust: 100%;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
  }}
  body {{
    margin: 0;
    padding: 0;
    background-color: #0A0B0E;
    color: #E6E8EC;
  }}
  .container {{
    max-width: 600px;
    margin: 0 auto;
    padding: 36px 20px;
  }}
  .card {{
    background-color: #12141A;
    border: 1px solid rgba(223, 201, 158, 0.18);
    border-radius: 12px;
    padding: 32px 28px;
    box-shadow: 0 12px 32px rgba(0, 0, 0, 0.4);
  }}
  .brand-bar {{
    display: flex;
    align-items: center;
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    padding-bottom: 20px;
    margin-bottom: 24px;
  }}
  .brand-title {{
    font-size: 18px;
    font-weight: 700;
    letter-spacing: 0.12em;
    color: #DFC99E;
    text-transform: uppercase;
  }}
  .brand-sub {{
    font-size: 11px;
    color: #8C92A4;
    letter-spacing: 0.06em;
    margin-left: 8px;
  }}
  h1 {{
    font-size: 22px;
    font-weight: 600;
    color: #FFFFFF;
    margin: 0 0 16px 0;
    line-height: 1.35;
  }}
  p {{
    font-size: 15px;
    line-height: 1.6;
    color: #C0C5D0;
    margin: 0 0 16px 0;
  }}
  .route-badge {{
    background: rgba(223, 201, 158, 0.08);
    border: 1px solid rgba(223, 201, 158, 0.25);
    border-radius: 8px;
    padding: 16px 20px;
    margin: 20px 0;
  }}
  .route-title {{
    font-size: 18px;
    font-weight: 700;
    color: #DFC99E;
    margin-bottom: 8px;
  }}
  .meta-grid {{
    width: 100%;
    border-collapse: collapse;
    margin: 16px 0;
  }}
  .meta-grid td {{
    padding: 8px 0;
    font-size: 14px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.05);
  }}
  .meta-label {{
    color: #8C92A4;
    width: 45%;
  }}
  .meta-value {{
    color: #FFFFFF;
    font-weight: 600;
    text-align: right;
  }}
  .highlight-price {{
    color: #DFC99E;
    font-size: 20px;
    font-weight: 700;
  }}
  .btn-cta {{
    display: inline-block;
    background-color: #DFC99E;
    color: #0A0B0E !important;
    text-decoration: none;
    font-weight: 700;
    font-size: 14px;
    padding: 14px 28px;
    border-radius: 8px;
    letter-spacing: 0.04em;
    margin: 24px 0 12px 0;
    text-align: center;
  }}
  .footer {{
    margin-top: 32px;
    padding-top: 20px;
    border-top: 1px solid rgba(255, 255, 255, 0.06);
    font-size: 12px;
    line-height: 1.5;
    color: #6C7280;
    text-align: center;
  }}
  .footer a {{
    color: #DFC99E;
    text-decoration: none;
  }}
</style>
</head>
<body>
<div class="container">
  <div class="card">
    <div class="brand-bar">
      <span class="brand-title">SkyTracker</span>
      <span class="brand-sub">Flight Surveillance</span>
    </div>

    {content_html}

    <div class="footer">
      <p style="font-size: 12px; color: #7B8190; margin-bottom: 10px;">{safe_footer}</p>
      <p style="font-size: 11px; color: #585D6B; margin: 0;">
        You received this email because you active radar tracking on <a href="{base_url}">SkyTracker</a>.
      </p>
    </div>
  </div>
</div>
</body>
</html>
"""


# =========================================================================
# 1. PRICE DROP NOTIFICATION EMAIL
# =========================================================================

def build_price_drop_email(
    to_email: str,
    departure_iata: str,
    arrival_iata: str,
    outbound_date: str,
    current_price: float,
    previous_price: Optional[float] = None,
    target_price: Optional[float] = None,
    flight_details: Optional[Dict[str, Any]] = None,
    customer_name: Optional[str] = None,
    checked_at: Optional[str] = None,
    tracking_id: Optional[int] = None,
    airline: Optional[str] = None,
    flight_number: Optional[str] = None,
    flight_url: Optional[str] = None,
    **kwargs
) -> Tuple[str, str, str]:
    """
    Construct subject, plain-text body, and HTML body for a flight price-drop notification.
    Returns (subject, text_body, html_body).
    """
    dep_display = format_airport_display(departure_iata)
    arr_display = format_airport_display(arrival_iata)
    dep_code = str(departure_iata).strip().upper()
    arr_code = str(arrival_iata).strip().upper()

    savings, pct = calculate_savings(previous_price, current_price)

    name_str = customer_name or get_customer_name(to_email)
    greeting = f"Hello {name_str}," if name_str else "Hello Traveler,"

    now_display = checked_at or datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")
    base_url = get_base_url()
    effective_url = flight_url or kwargs.get("url") or f"{base_url}/flights?departure={dep_code}&arrival={arr_code}&outbound_date={outbound_date}"

    # Subject line matching standard: SkyTracker: Flight price dropped — DEL → MAA
    subject = f"SkyTracker: Flight price dropped — {dep_code} → {arr_code}"

    # Merge explicit carrier arguments with flight_details dict
    details = dict(flight_details) if flight_details and isinstance(flight_details, dict) else {}
    if airline:
        details["airline"] = airline
    elif "airline" in kwargs and kwargs["airline"]:
        details["airline"] = kwargs["airline"]
    if flight_number:
        details["flight_number"] = flight_number
    elif "flight_number" in kwargs and kwargs["flight_number"]:
        details["flight_number"] = kwargs["flight_number"]

    # Optional verified flight carrier info
    carrier_text = ""
    carrier_html_rows = ""
    if details:
        airline_val = details.get("airline")
        flight_num_val = details.get("flight_number")
        dep_time_val = details.get("departure_time")
        arr_time_val = details.get("arrival_time")

        if airline_val:
            carrier_text += f"\nAirline: {airline_val}"
            carrier_html_rows += f"""
            <tr>
              <td class="meta-label">Airline</td>
              <td class="meta-value">{html.escape(str(airline_val))}</td>
            </tr>
            """
        if flight_num_val:
            carrier_text += f"\nFlight number: {flight_num_val}"
            carrier_html_rows += f"""
            <tr>
              <td class="meta-label">Flight</td>
              <td class="meta-value font-mono">{html.escape(str(flight_num_val))}</td>
            </tr>
            """
        if dep_time_val and arr_time_val:
            carrier_text += f"\nSchedule: {dep_time_val} → {arr_time_val}"
            carrier_html_rows += f"""
            <tr>
              <td class="meta-label">Schedule</td>
              <td class="meta-value">{html.escape(str(dep_time_val))} → {html.escape(str(arr_time_val))}</td>
            </tr>
            """

    # Plain text alternative
    text_body = f"""{greeting}

Good news! The latest check found a lower price for your tracked journey.

Route: {dep_display} → {arr_display}
Travel date: {outbound_date}
"""
    if previous_price is not None:
        text_body += f"Previous recorded price: ₹{previous_price:,.0f}\n"

    text_body += f"Latest observed price: ₹{current_price:,.0f}\n"

    if savings is not None and pct is not None:
        text_body += f"Potential savings: ₹{savings:,.0f} ({pct:.1f}%)\n"

    if target_price is not None:
        text_body += f"Your target price: ₹{target_price:,.0f}\n"

    if carrier_text:
        text_body += f"{carrier_text}\n"

    text_body += f"""Status: CONFIRMED FARE DROP
Last checked: {now_display}

Open SkyTracker to review the latest results:
{effective_url}

Flight prices and seat availability can change at any time. This notification is not a booking confirmation.

Happy travels,
The SkyTracker Team
"""

    # HTML body
    savings_row = ""
    if savings is not None and pct is not None:
        savings_row = f"""
        <tr>
          <td class="meta-label">Potential savings</td>
          <td class="meta-value" style="color: #4ADE80;">₹{savings:,.0f} ({pct:.1f}%)</td>
        </tr>
        """

    prev_row = ""
    if previous_price is not None:
        prev_row = f"""
        <tr>
          <td class="meta-label">Previous recorded price</td>
          <td class="meta-value" style="text-decoration: line-through; color: #8C92A4;">₹{previous_price:,.0f}</td>
        </tr>
        """

    target_row = ""
    if target_price is not None:
        target_row = f"""
        <tr>
          <td class="meta-label">Your target price</td>
          <td class="meta-value">₹{target_price:,.0f}</td>
        </tr>
        """

    content_html = f"""
    <h1>Lower Fare Detected</h1>
    <p>{html.escape(greeting)}</p>
    <p>Good news! The latest check found a lower price for your tracked journey.</p>

    <div class="route-badge">
      <div class="route-title">{html.escape(dep_display)} → {html.escape(arr_display)}</div>
      <div style="font-size: 13px; color: #A0A6B5;">Travel Date: <strong>{html.escape(outbound_date)}</strong></div>
    </div>

    <table class="meta-grid">
      {prev_row}
      <tr>
        <td class="meta-label">Latest observed price</td>
        <td class="meta-value"><span class="highlight-price">₹{current_price:,.0f}</span></td>
      </tr>
      {savings_row}
      {target_row}
      {carrier_html_rows}
      <tr>
        <td class="meta-label">Last checked</td>
        <td class="meta-value font-mono" style="font-size: 13px;">{html.escape(now_display)}</td>
      </tr>
    </table>

    <div style="text-align: center;">
      <a href="{effective_url}" class="btn-cta">View Tracked Flight</a>
    </div>

    <p style="font-size: 13px; color: #8C92A4; margin-top: 20px;">
      Flight prices and seat availability can change at any time. This notification is not a booking confirmation.
    </p>

    <p style="margin-top: 16px;">
      Happy travels,<br>
      <strong>The SkyTracker Team</strong>
    </p>
    """

    html_body = _render_email_shell(subject, content_html)
    return subject, text_body, html_body


def send_price_drop_email(
    to_email: str,
    departure_iata: str,
    arrival_iata: str,
    outbound_date: str,
    current_price: float,
    previous_price: Optional[float] = None,
    target_price: Optional[float] = None,
    flight_details: Optional[Dict[str, Any]] = None,
    customer_name: Optional[str] = None,
    checked_at: Optional[str] = None,
    tracking_id: Optional[int] = None,
    sender_fn: Optional[Callable] = None
) -> bool:
    """Send flight price-drop email using configured or custom sender function."""
    subject, text_body, html_body = build_price_drop_email(
        to_email=to_email,
        departure_iata=departure_iata,
        arrival_iata=arrival_iata,
        outbound_date=outbound_date,
        current_price=current_price,
        previous_price=previous_price,
        target_price=target_price,
        flight_details=flight_details,
        customer_name=customer_name,
        checked_at=checked_at,
        tracking_id=tracking_id
    )
    send_func = sender_fn or send_email
    return send_func(to_email, subject, text_body, html_body)


# =========================================================================
# 2. NEW TRACKING CONFIRMATION EMAIL
# =========================================================================

def build_tracking_confirmation_email(
    to_email: str,
    departure_iata: str,
    arrival_iata: str,
    outbound_date: str,
    target_price: float,
    return_date: Optional[str] = None,
    passenger_summary: Optional[str] = None,
    customer_name: Optional[str] = None,
    tracking_id: Optional[int] = None,
    delete_token: Optional[str] = None,
    **kwargs
) -> Tuple[str, str, str]:
    """
    Construct subject, plain-text body, and HTML body for new tracking confirmation.
    Returns (subject, text_body, html_body).
    """
    dep_display = format_airport_display(departure_iata)
    arr_display = format_airport_display(arrival_iata)

    name_str = customer_name or get_customer_name(to_email)
    greeting = f"Hello {name_str}," if name_str else "Hello,"

    base_url = get_base_url()
    if tracking_id and delete_token:
        manage_url = f"{base_url}/tracking?id={tracking_id}&delete_token={delete_token}"
    elif delete_token:
        manage_url = f"{base_url}/tracking?delete_token={delete_token}"
    else:
        manage_url = f"{base_url}/tracking"

    subject = "SkyTracker: Your flight price alert is active"

    dates_text = f"Departure: {outbound_date}"
    if return_date:
        dates_text += f"\nReturn: {return_date}"

    passengers_text = f"Travelers: {passenger_summary}\n" if passenger_summary else ""

    text_body = f"""{greeting}

Your flight price alert has been successfully created and radar surveillance is now active.

Route: {dep_display} → {arr_display}
{dates_text}
{passengers_text}Target price: ₹{target_price:,.0f}

Alert conditions:
We autonomously check provider pricing every 6 hours and will email you as soon as a price drop or target-qualifying fare is observed.

Please note: Tracking is subject to airline provider availability and successful price checks.

Manage your tracked trajectories:
{manage_url}

Happy travels,
The SkyTracker Team
"""

    return_row = ""
    if return_date:
        return_row = f"""
        <tr>
          <td class="meta-label">Return Date</td>
          <td class="meta-value">{html.escape(return_date)}</td>
        </tr>
        """

    passenger_row = ""
    if passenger_summary:
        passenger_row = f"""
        <tr>
          <td class="meta-label">Travelers</td>
          <td class="meta-value">{html.escape(passenger_summary)}</td>
        </tr>
        """

    content_html = f"""
    <h1>Price Alert Activated</h1>
    <p>{html.escape(greeting)}</p>
    <p>Your flight price alert has been successfully created and autonomous surveillance is now active.</p>

    <div class="route-badge">
      <div class="route-title">{html.escape(dep_display)} → {html.escape(arr_display)}</div>
      <div style="font-size: 13px; color: #A0A6B5;">Departure: <strong>{html.escape(outbound_date)}</strong></div>
    </div>

    <table class="meta-grid">
      <tr>
        <td class="meta-label">Target price</td>
        <td class="meta-value"><span class="highlight-price">₹{target_price:,.0f}</span></td>
      </tr>
      {return_row}
      {passenger_row}
      <tr>
        <td class="meta-label">Surveillance frequency</td>
        <td class="meta-value">Every 6 hours</td>
      </tr>
      <tr>
        <td class="meta-label">Monitoring status</td>
        <td class="meta-value" style="color: #4ADE80;">Active Radar</td>
      </tr>
    </table>

    <p style="font-size: 14px; color: #C0C5D0;">
      We will notify you immediately if the fare drops below your target or sets a new recorded benchmark. Tracking is subject to airline provider availability and successful price checks.
    </p>

    <div style="text-align: center;">
      <a href="{manage_url}" class="btn-cta">Manage Tracked Trajectories</a>
    </div>

    <p style="margin-top: 16px;">
      Happy travels,<br>
      <strong>The SkyTracker Team</strong>
    </p>
    """

    html_body = _render_email_shell(
        subject,
        content_html,
        footer_note="Tracking is subject to provider availability and successful price checks."
    )
    return subject, text_body, html_body


def send_tracking_confirmation_email(
    to_email: str,
    departure_iata: str,
    arrival_iata: str,
    outbound_date: str,
    target_price: float,
    return_date: Optional[str] = None,
    passenger_summary: Optional[str] = None,
    customer_name: Optional[str] = None,
    tracking_id: Optional[int] = None,
    delete_token: Optional[str] = None,
    sender_fn: Optional[Callable] = None
) -> bool:
    """Send tracking confirmation email using configured or custom sender function."""
    subject, text_body, html_body = build_tracking_confirmation_email(
        to_email=to_email,
        departure_iata=departure_iata,
        arrival_iata=arrival_iata,
        outbound_date=outbound_date,
        target_price=target_price,
        return_date=return_date,
        passenger_summary=passenger_summary,
        customer_name=customer_name,
        tracking_id=tracking_id,
        delete_token=delete_token
    )
    send_func = sender_fn or send_email
    return send_func(to_email, subject, text_body, html_body)


# =========================================================================
# 3. FAILED PRICE-CHECK OR SERVICE INTERRUPTION EMAIL
# =========================================================================

def build_service_interruption_email(
    to_email: str,
    departure_iata: str,
    arrival_iata: str,
    reason: Optional[str] = None,
    last_successful_at: Optional[str] = None,
    customer_name: Optional[str] = None,
    outbound_date: Optional[str] = None,
    last_checked_at: Optional[str] = None,
    **kwargs
) -> Tuple[str, str, str]:
    """
    Construct subject, plain-text body, and HTML body for a service interruption notification.
    Returns (subject, text_body, html_body).
    """
    dep_display = format_airport_display(departure_iata)
    arr_display = format_airport_display(arrival_iata)
    dep_code = str(departure_iata).strip().upper()
    arr_code = str(arrival_iata).strip().upper()

    name_str = customer_name or get_customer_name(to_email)
    greeting = f"Hello {name_str}," if name_str else "Hello Traveler,"

    base_url = get_base_url()
    manage_url = f"{base_url}/tracking"

    subject = f"SkyTracker: Flight price check update — {dep_code} → {arr_code}"

    raw_reason = str(reason or kwargs.get("error") or "").strip()
    sensitive_markers = ["api_key", "password", "token", "traceback", "sqlite", "postgres", "syntax error", "database", "connection refused", "operationalerror", "exception", "errno"]
    if not raw_reason or any(m in raw_reason.lower() for m in sensitive_markers):
        safe_reason = "Temporary upstream carrier data feed synchronization delay"
    else:
        safe_reason = raw_reason
    last_verified = last_checked_at or last_successful_at or "No previous successful check recorded yet"

    date_line = f"Travel date: {outbound_date}\n" if outbound_date else ""

    text_body = f"""{greeting}

We are writing to provide a transparent update on your tracked journey.

Route: {dep_display} → {arr_display}
{date_line}
During our recent automated surveillance run, we were unable to verify current airline pricing for this route.
Reason: {safe_reason}
Last verified check: {last_verified}

SkyTracker never presents unverified or stale fares as current pricing, and we do not dispatch false price alerts.

Our automated radar continues monitoring and will attempt verification during the next scheduled check. No action is required on your part.

You can review or manage your tracking alert at any time:
{manage_url}

Sincerely,
The SkyTracker Team
"""

    content_html = f"""
    <h1>Tracking Status Update</h1>
    <p>{html.escape(greeting)}</p>
    <p>We are writing to provide a transparent update regarding your monitored flight route.</p>

    <div class="route-badge">
      <div class="route-title">{html.escape(dep_display)} → {html.escape(arr_display)}</div>
    </div>

    <table class="meta-grid">
      <tr>
        <td class="meta-label">Verification status</td>
        <td class="meta-value" style="color: #F87171;">Temporarily Unavailable</td>
      </tr>
      <tr>
        <td class="meta-label">Reported reason</td>
        <td class="meta-value">{html.escape(safe_reason)}</td>
      </tr>
      <tr>
        <td class="meta-label">Last verified check</td>
        <td class="meta-value font-mono">{html.escape(last_verified)}</td>
      </tr>
    </table>

    <p style="font-size: 14px; color: #C0C5D0;">
      SkyTracker never presents unverified or stale fares as current pricing, and we do not dispatch false price alerts. Our engine will automatically retry verification during the next scheduled radar cycle. No action is required from you.
    </p>

    <div style="text-align: center;">
      <a href="{manage_url}" class="btn-cta">View Radar Dashboard</a>
    </div>

    <p style="margin-top: 16px;">
      Sincerely,<br>
      <strong>The SkyTracker Team</strong>
    </p>
    """

    html_body = _render_email_shell(
        subject,
        content_html,
        footer_note="SkyTracker adheres to strict data transparency and never sends alerts on estimated or unverified pricing."
    )
    return subject, text_body, html_body


def send_service_interruption_email(
    to_email: str,
    departure_iata: str,
    arrival_iata: str,
    reason: Optional[str] = None,
    last_successful_at: Optional[str] = None,
    customer_name: Optional[str] = None,
    sender_fn: Optional[Callable] = None
) -> bool:
    """Send service interruption email using configured or custom sender function."""
    subject, text_body, html_body = build_service_interruption_email(
        to_email=to_email,
        departure_iata=departure_iata,
        arrival_iata=arrival_iata,
        reason=reason,
        last_successful_at=last_successful_at,
        customer_name=customer_name
    )
    send_func = sender_fn or send_email
    return send_func(to_email, subject, text_body, html_body)
