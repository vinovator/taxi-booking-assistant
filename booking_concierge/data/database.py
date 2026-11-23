import json
import sqlite3
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent
DB_PATH = DATA_DIR / "taxi.db"

def get_db_connection() -> sqlite3.Connection:
    """Get a connection to the SQLite database."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # Enable named column access
    conn.execute("PRAGMA foreign_keys = ON")  # Enable foreign key support
    return conn


def init_db() -> None:
    """Create any missing tables and seed rows if they don't exist. Safe to run on every start"""
    conn = get_db_connection()

    try:
        conn.executescript((DATA_DIR / "schema.sql").read_text())
        conn.executescript((DATA_DIR / "seed.sql").read_text())
    finally:
        conn.commit()
        conn.close()


def fetch_one(sql:str, params:tuple=()) -> dict | None:
    """Fetch a single row from the database."""
    conn = get_db_connection()
    try:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def fetch_all(sql: str, params: tuple = ()) -> list[dict]:
    """Fetch all matching rows from the database."""
    conn = get_db_connection()
    try:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def execute(sql:str, params:tuple=()) -> sqlite3.Cursor:
    """Run an INSERT or UPDATE and save it.

    Returns the cursor: .lastrowid is the new row's id after an INSERT,
    .rowcount is the number of rows changed by an UPDATE.
    """
    conn = get_db_connection()
    try:
        with conn: # commit automatically on success, rollback on failure
            cursor = conn.execute(sql, params)
        return cursor
    finally:
        conn.close()


# --------- Functions that tools call ---------

def find_business_customer(company_reference_number: str) -> str | None:
    """Return the company name for this company reference number, or None if there's no such company."""
    row = fetch_one("SELECT company_name FROM companies WHERE company_reference_number = ?",
                    (company_reference_number,))
    return row["company_name"] if row else None


def find_personal_customer(customer_email: str) -> dict | None:
    """Return the personal customer with this email, or None."""
    return fetch_one("SELECT * FROM customers "
                     "WHERE customer_type = 'personal' AND customer_email = ?",
                     (customer_email.strip().lower(),))


def add_customer(customer_type: str, customer_name: str, customer_email: str,
                 customer_phone_number: str, company_reference_number: str | None = None) -> dict:
    """Add a customer unless the email is already registered, then return the stored customer.

    If the email is already registered, nothing changes and the existing customer is
    returned, so check its customer_type and company_reference_number.
    """
    customer_email = customer_email.strip().lower()
    execute(
        "INSERT INTO customers (customer_type, company_reference_number, customer_name, "
        "customer_email, customer_phone_number) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT (customer_email) DO NOTHING",
        (customer_type, company_reference_number, customer_name,
         customer_email, customer_phone_number),
    )
    return fetch_one("SELECT * FROM customers WHERE customer_email = ?", (customer_email,))


def count_upcoming_bookings(customer_id: int) -> int:
    """How many of this customer's bookings are confirmed and not yet picked up."""
    row = fetch_one(
        "SELECT COUNT(*) AS upcoming FROM bookings "
        "WHERE customer_id = ? AND status = 'confirmed' AND pickup_datetime > ?",
        (customer_id, datetime.now().strftime("%Y-%m-%d %H:%M")),  # pickups are saved as YYYY-MM-DD HH:MM
    )
    return row["upcoming"]


def list_taxi_types(customer_type: str) -> list[dict]:
    """The taxi catalog with this customer type's price per mile, cheapest first (smaller first at the same price).

    Each taxi type comes with its rates row for this customer type (a JOIN), so a taxi type with no price
    for this customer type is left out.
    """
    return fetch_all(
        "SELECT t.taxi_type, t.max_passengers, r.rate_per_mile "
        "FROM taxi_types t JOIN rates r ON r.taxi_type = t.taxi_type "
        "WHERE r.customer_type = ? ORDER BY r.rate_per_mile, t.max_passengers",
        (customer_type,),
    )


def get_taxi_type(taxi_type: str, customer_type: str) -> dict | None:
    """One taxi type with this customer type's price per mile, or None if there's no such taxi type.

    Upper and lower case don't matter ('suv' finds 'SUV'), because taxi_types.taxi_type is COLLATE NOCASE.
    The row has the catalog's spelling, which is what bookings save.
    """
    return fetch_one(
        "SELECT t.taxi_type, t.max_passengers, r.rate_per_mile "
        "FROM taxi_types t JOIN rates r ON r.taxi_type = t.taxi_type "
        "WHERE t.taxi_type = ? AND r.customer_type = ?",
        (str(taxi_type).strip(), customer_type),
    )


def create_booking(customer_id: int, source: str, destination: str, pickup_datetime: str,
                   taxi_type: str, passengers: int, distance_miles: float, fare: float) -> int:
    """INSERT into bookings and return the new booking's confirmation number.

    The database assigns confirmation numbers (1, 2, 3, ...). booking_time is this computer's
    local time, the same clock used for pickup times; status comes from the schema default.
    """
    cursor = execute(
        "INSERT INTO bookings (customer_id, source, destination, pickup_datetime, taxi_type, passengers, "
        "distance_miles, fare, booking_time) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (customer_id, source, destination, pickup_datetime, taxi_type, passengers, distance_miles, fare,
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    return cursor.lastrowid


def get_booking(confirmation_number: int, customer_id: int) -> dict | None:
    """SELECT the booking WHERE confirmation_number = ? AND customer_id = ? (owner only)."""
    return fetch_one(
        "SELECT * FROM bookings WHERE confirmation_number = ? AND customer_id = ?",
        (confirmation_number, customer_id),
    )


def change_booking(confirmation_number: int, customer_id: int, pickup_datetime: str,
                   taxi_type: str, passengers: int, fare: float) -> bool:
    """UPDATE the pickup, taxi type, passengers and fare of that customer's confirmed booking.

    Pass the current value for anything that isn't changing. Returns True if the booking changed.
    """
    cursor = execute(
        "UPDATE bookings SET pickup_datetime = ?, taxi_type = ?, passengers = ?, fare = ? "
        "WHERE confirmation_number = ? AND customer_id = ? AND status = 'confirmed'",
        (pickup_datetime, taxi_type, passengers, fare, confirmation_number, customer_id),
    )
    return cursor.rowcount == 1


def cancel_booking(confirmation_number: int, customer_id: int) -> bool:
    """UPDATE status to 'cancelled' on that customer's confirmed booking. Returns True if it changed."""
    cursor = execute(
        "UPDATE bookings SET status = 'cancelled' "
        "WHERE confirmation_number = ? AND customer_id = ? AND status = 'confirmed'",
        (confirmation_number, customer_id),
    )
    return cursor.rowcount == 1


# --------- Functions the web UI calls (streamlit_app.py) ---------

def add_approval_request(session_id: str, user_id: str, confirmation_call_id: str,
                         action: str, summary: str, details: dict) -> None:
    """Add a request to the representative's list (ignored if it's already there)."""
    execute(
        "INSERT INTO approval_requests (session_id, user_id, confirmation_call_id, action, summary, details) "
        "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (confirmation_call_id) DO NOTHING",
        (session_id, user_id, confirmation_call_id, action, summary, json.dumps(details)),
    )


def list_approval_requests(status: str | None = None) -> list[dict]:
    """Approval requests with this status ('waiting', 'approved' or 'rejected'), or all of them, newest first.

    `details` comes back as a dict.
    """
    if status:
        rows = fetch_all("SELECT * FROM approval_requests WHERE status = ? ORDER BY request_id DESC", (status,))
    else:
        rows = fetch_all("SELECT * FROM approval_requests ORDER BY request_id DESC")
    for row in rows:
        row["details"] = json.loads(row["details"]) if row["details"] else {}
    return rows


def decide_approval_request(request_id: int, approved: bool, message: str) -> bool:
    """Record the representative's decision on a waiting request.

    Returns False if the request was already decided: each request can only be decided once.
    """
    cursor = execute(
        "UPDATE approval_requests SET status = ?, representative_message = ?, decided_time = ? "
        "WHERE request_id = ? AND status = 'waiting'",
        ("approved" if approved else "rejected", message or None,
         datetime.now().strftime("%Y-%m-%d %H:%M:%S"), request_id),
    )
    return cursor.rowcount == 1


def list_customers() -> list[dict]:
    """All customers, with the company name for business customers."""
    return fetch_all(
        "SELECT c.customer_id, c.customer_type, c.customer_name, c.customer_email, c.customer_phone_number, "
        "c.company_reference_number, co.company_name "
        "FROM customers c LEFT JOIN companies co ON co.company_reference_number = c.company_reference_number "
        "ORDER BY c.customer_id"
    )


def list_taxi_catalog() -> list[dict]:
    """Every taxi type with its business and its personal price per mile side by side, smallest first.

    rates has one row per taxi type and customer type, so it's joined twice: once for each customer type.
    """
    return fetch_all(
        "SELECT t.taxi_type, t.max_passengers, "
        "b.rate_per_mile AS business_rate_per_mile, p.rate_per_mile AS personal_rate_per_mile "
        "FROM taxi_types t "
        "LEFT JOIN rates b ON b.taxi_type = t.taxi_type AND b.customer_type = 'business' "
        "LEFT JOIN rates p ON p.taxi_type = t.taxi_type AND p.customer_type = 'personal' "
        "ORDER BY t.max_passengers, p.rate_per_mile"
    )


def list_bookings() -> list[dict]:
    """All bookings with the customer's name, newest first."""
    return fetch_all(
        "SELECT b.confirmation_number, c.customer_name, c.customer_type, b.source, b.destination, "
        "b.pickup_datetime, b.taxi_type, b.passengers, b.distance_miles, b.fare, b.status, b.booking_time "
        "FROM bookings b JOIN customers c ON c.customer_id = b.customer_id "
        "ORDER BY b.confirmation_number DESC"
    )