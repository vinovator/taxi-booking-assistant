-- The tables needed for taxi database

CREATE TABLE IF NOT EXISTS companies (
    company_reference_number TEXT PRIMARY KEY,
    company_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS customers (
    customer_id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_type TEXT NOT NULL CHECK (customer_type IN ('business', 'personal')),
    company_reference_number TEXT REFERENCES companies(company_reference_number),
    customer_name TEXT NOT NULL,
    customer_email TEXT NOT NULL UNIQUE,
    customer_phone_number TEXT NOT NULL,
    CHECK (
        (customer_type = 'business' AND company_reference_number IS NOT NULL) OR
        (customer_type = 'personal' AND company_reference_number IS NULL)
    )
);

-- The taxi catalog: one row per taxi type. The web UI shows each one as a card, with the picture named after
-- it in images/ (for example images/suv.svg).
CREATE TABLE IF NOT EXISTS taxi_types (
    taxi_type TEXT PRIMARY KEY COLLATE NOCASE,  -- NOCASE: looking up 'suv' finds 'SUV'
    max_passengers INTEGER NOT NULL CHECK (max_passengers > 0)
);

-- The price per mile depends on two things, the taxi type and the customer type, so both make up the key.
CREATE TABLE IF NOT EXISTS rates (
    taxi_type TEXT NOT NULL REFERENCES taxi_types(taxi_type),
    customer_type TEXT NOT NULL CHECK (customer_type IN ('business', 'personal')),
    rate_per_mile REAL NOT NULL CHECK (rate_per_mile > 0),
    PRIMARY KEY (taxi_type, customer_type)
);

CREATE TABLE IF NOT EXISTS bookings (
    confirmation_number INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(customer_id),
    source TEXT NOT NULL,
    destination TEXT NOT NULL,
    pickup_datetime TEXT NOT NULL,
    taxi_type TEXT NOT NULL REFERENCES taxi_types(taxi_type),
    passengers INTEGER NOT NULL CHECK (passengers > 0),
    distance_miles REAL NOT NULL,
    fare REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'confirmed' CHECK (status IN ('confirmed', 'cancelled', 'completed')),
    booking_time TIMESTAMP DEFAULT (datetime('now', 'localtime'))
);

-- Requests waiting for a representative's approval (trips less than 24 hours away).
-- The approval itself is handled by ADK; this table is the representative's to-do list.
CREATE TABLE IF NOT EXISTS approval_requests (
    request_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    confirmation_call_id TEXT NOT NULL UNIQUE,
    action TEXT NOT NULL,                  -- New booking, Change or Cancellation
    summary TEXT NOT NULL,                 -- one line, e.g. for the admin table
    details TEXT,                          -- JSON for the representative's request card
    status TEXT NOT NULL DEFAULT 'waiting' CHECK (status IN ('waiting', 'approved', 'rejected')),
    representative_message TEXT,
    requested_time TIMESTAMP DEFAULT (datetime('now', 'localtime')),
    decided_time TIMESTAMP
);