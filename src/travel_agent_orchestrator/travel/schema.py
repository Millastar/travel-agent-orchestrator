"""Versioned PostgreSQL schema for the hotel sandbox and local traces."""

DOMAIN_SCHEMA_VERSION = 2

DOMAIN_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS travel_schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS travel_hotels (
    hotel_id TEXT PRIMARY KEY,
    external_ref TEXT UNIQUE,
    name TEXT NOT NULL,
    city TEXT NOT NULL,
    address TEXT NOT NULL,
    location TEXT,
    stars INTEGER NOT NULL CHECK (stars BETWEEN 1 AND 5),
    source TEXT NOT NULL DEFAULT 'sandbox',
    amenities JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS travel_room_inventory (
    room_id TEXT PRIMARY KEY,
    hotel_id TEXT NOT NULL REFERENCES travel_hotels(hotel_id) ON DELETE CASCADE,
    room_type TEXT NOT NULL,
    nightly_rate NUMERIC(12, 2) NOT NULL CHECK (nightly_rate >= 0),
    capacity INTEGER NOT NULL CHECK (capacity > 0),
    available_rooms INTEGER NOT NULL CHECK (available_rooms >= 0),
    cancellation_policy TEXT NOT NULL,
    UNIQUE (hotel_id, room_type)
);

CREATE TABLE IF NOT EXISTS travel_quotes (
    quote_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    hotel_id TEXT NOT NULL REFERENCES travel_hotels(hotel_id),
    room_id TEXT NOT NULL REFERENCES travel_room_inventory(room_id),
    check_in DATE NOT NULL,
    check_out DATE NOT NULL,
    guests INTEGER NOT NULL CHECK (guests > 0),
    total_amount NUMERIC(12, 2) NOT NULL CHECK (total_amount >= 0),
    status TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS travel_daily_inventory (
    room_id TEXT NOT NULL REFERENCES travel_room_inventory(room_id) ON DELETE CASCADE,
    stay_date DATE NOT NULL,
    available_rooms INTEGER NOT NULL CHECK (available_rooms >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (room_id, stay_date)
);

CREATE TABLE IF NOT EXISTS travel_orders (
    order_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    quote_id TEXT NOT NULL REFERENCES travel_quotes(quote_id),
    idempotency_key TEXT NOT NULL,
    hotel_name TEXT NOT NULL,
    room_type TEXT NOT NULL,
    check_in DATE NOT NULL,
    check_out DATE NOT NULL,
    guests INTEGER NOT NULL,
    total_amount NUMERIC(12, 2) NOT NULL,
    status TEXT NOT NULL,
    payment_status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS travel_payments (
    payment_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES travel_orders(order_id) ON DELETE CASCADE,
    amount NUMERIC(12, 2) NOT NULL,
    method TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS travel_refunds (
    refund_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES travel_orders(order_id) ON DELETE CASCADE,
    payment_id TEXT REFERENCES travel_payments(payment_id),
    amount NUMERIC(12, 2) NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS travel_complaints (
    complaint_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    order_id TEXT REFERENCES travel_orders(order_id),
    category TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agent_trace_events (
    event_id BIGSERIAL PRIMARY KEY,
    trace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    status TEXT NOT NULL,
    duration_ms DOUBLE PRECISION,
    token_usage INTEGER,
    input_summary TEXT,
    output_summary TEXT,
    error_code TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (trace_id, sequence)
);

CREATE INDEX IF NOT EXISTS ix_travel_orders_owner ON travel_orders(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS ix_daily_inventory_date
ON travel_daily_inventory(stay_date, room_id);
CREATE INDEX IF NOT EXISTS ix_trace_task
ON agent_trace_events(user_id, session_id, task_id, sequence);
"""
