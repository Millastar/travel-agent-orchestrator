"""Typed contracts for routing, handoffs, orders, and execution traces."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class AgentRole(StrEnum):
    SUPERVISOR = "supervisor"
    DISCOVERY = "discovery"
    BOOKING = "booking"
    CUSTOMER_SERVICE = "customer_service"
    DIRECT = "direct"


class Intent(StrEnum):
    DISCOVER_HOTEL = "discover_hotel"
    BOOK_HOTEL = "book_hotel"
    MANAGE_BOOKING = "manage_booking"
    COMPLAINT = "complaint"
    GENERAL = "general"
    CLARIFY = "clarify"


class RiskLevel(StrEnum):
    READ_ONLY = "read_only"
    TRANSACTION = "transaction"
    DESTRUCTIVE = "destructive"


class OrderStatus(StrEnum):
    PAYMENT_PENDING = "payment_pending"
    CONFIRMED = "confirmed"
    CHANGE_PENDING = "change_pending"
    CANCEL_PENDING = "cancel_pending"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"
    PAYMENT_FAILED = "payment_failed"
    FAILED = "failed"


class PaymentStatus(StrEnum):
    PENDING = "pending"
    AUTHORIZED = "authorized"
    CAPTURED = "captured"
    DECLINED = "declined"
    REFUNDED = "refunded"


class QuoteStatus(StrEnum):
    ACTIVE = "active"
    ACCEPTED = "accepted"
    EXPIRED = "expired"


class RouteDecision(BaseModel):
    intent: Intent
    target_agent: AgentRole
    confidence: float = Field(ge=0, le=1)
    reason: str


class HandoffEnvelope(BaseModel):
    source: AgentRole
    target: AgentRole
    objective: str
    constraints: dict[str, Any] = Field(default_factory=dict)
    quote_id: str | None = None
    order_id: str | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    reason: str


class Hotel(BaseModel):
    hotel_id: str
    name: str
    city: str
    address: str
    location: str | None = None
    stars: int = Field(ge=1, le=5)
    source: str = "sandbox"
    amenities: list[str] = Field(default_factory=list)


class RoomOffer(BaseModel):
    room_id: str
    hotel_id: str
    room_type: str
    nightly_rate: float = Field(ge=0)
    capacity: int = Field(ge=1)
    available_rooms: int = Field(ge=0)
    cancellation_policy: str


class Quote(BaseModel):
    quote_id: str
    user_id: str
    hotel: Hotel
    room: RoomOffer
    check_in: date
    check_out: date
    guests: int
    total_amount: float
    status: QuoteStatus = QuoteStatus.ACTIVE
    expires_at: datetime
    sandbox: bool = True


class Order(BaseModel):
    order_id: str
    user_id: str
    quote_id: str
    hotel_name: str
    room_type: str
    check_in: date
    check_out: date
    guests: int
    total_amount: float
    status: OrderStatus
    payment_status: PaymentStatus
    created_at: datetime
    updated_at: datetime
    sandbox: bool = True


class TraceEvent(BaseModel):
    trace_id: str
    sequence: int
    event_type: str
    actor: AgentRole | str
    status: str
    timestamp: datetime
    duration_ms: float | None = None
    token_usage: int | None = None
    input_summary: str | None = None
    output_summary: str | None = None
    error_code: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
