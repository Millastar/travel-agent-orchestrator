"""Persistence ports and PostgreSQL implementation for the hotel sandbox."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

from psycopg.types.json import Jsonb

from travel_agent_orchestrator.travel.models import (
    Hotel,
    Order,
    OrderStatus,
    PaymentStatus,
    Quote,
    QuoteStatus,
    RoomOffer,
)


class TravelRepository(Protocol):
    async def search_hotels(self, city: str, keywords: str, limit: int = 5) -> list[Hotel]: ...

    async def ensure_external_hotel(
        self, *, name: str, city: str, address: str, location: str | None, external_ref: str
    ) -> Hotel: ...

    async def get_hotel(self, hotel_id_or_name: str) -> Hotel | None: ...

    async def list_rooms(
        self,
        hotel_id: str,
        guests: int,
        *,
        check_in: date,
        check_out: date,
    ) -> list[RoomOffer]: ...

    async def save_quote(self, quote: Quote) -> None: ...

    async def get_quote(self, user_id: str, quote_id: str) -> Quote | None: ...

    async def create_order(
        self, quote: Quote, *, idempotency_key: str, payment_method: str
    ) -> Order: ...

    async def get_order(self, user_id: str, order_id: str) -> Order | None: ...

    async def list_orders(self, user_id: str, limit: int = 20) -> list[Order]: ...

    async def update_order(
        self,
        user_id: str,
        order_id: str,
        *,
        status: OrderStatus,
        payment_status: PaymentStatus | None = None,
    ) -> Order: ...

    async def change_order_dates(
        self, user_id: str, order_id: str, *, check_in: date, check_out: date
    ) -> Order: ...

    async def release_inventory(self, order: Order) -> None: ...

    async def record_refund(
        self, user_id: str, order_id: str, *, amount: float, reason: str
    ) -> str: ...

    async def create_complaint(
        self,
        user_id: str,
        *,
        order_id: str | None,
        category: str,
        description: str,
    ) -> str: ...

    async def append_trace(self, event: dict[str, Any]) -> None: ...

    async def get_trace(
        self, user_id: str, session_id: str, task_id: str
    ) -> list[dict[str, Any]]: ...

    async def get_metrics(self) -> dict[str, Any]: ...


def _hotel(row: dict[str, Any]) -> Hotel:
    return Hotel(
        hotel_id=row["hotel_id"],
        name=row["name"],
        city=row["city"],
        address=row["address"],
        location=row.get("location"),
        stars=row["stars"],
        source=row.get("source") or "sandbox",
        amenities=list(row.get("amenities") or []),
    )


def _room(row: dict[str, Any]) -> RoomOffer:
    return RoomOffer(
        room_id=row["room_id"],
        hotel_id=row["hotel_id"],
        room_type=row["room_type"],
        nightly_rate=float(row["nightly_rate"]),
        capacity=row["capacity"],
        available_rooms=row["available_rooms"],
        cancellation_policy=row["cancellation_policy"],
    )


def _stay_dates(check_in: date, check_out: date) -> list[date]:
    return [check_in + timedelta(days=offset) for offset in range((check_out - check_in).days)]


def _order(row: dict[str, Any]) -> Order:
    return Order(
        order_id=row["order_id"],
        user_id=row["user_id"],
        quote_id=row["quote_id"],
        hotel_name=row["hotel_name"],
        room_type=row["room_type"],
        check_in=row["check_in"],
        check_out=row["check_out"],
        guests=row["guests"],
        total_amount=float(row["total_amount"]),
        status=OrderStatus(row["status"]),
        payment_status=PaymentStatus(row["payment_status"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class PostgresTravelRepository:
    """Store domain records in the same pool used by LangGraph persistence."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def search_hotels(self, city: str, keywords: str, limit: int = 5) -> list[Hotel]:
        pattern = f"%{keywords.strip()}%" if keywords.strip() else "%"
        async with self.pool.connection() as connection:
            rows = await connection.execute(
                """
                SELECT * FROM travel_hotels
                WHERE (%s = '' OR city ILIKE %s)
                  AND (name ILIKE %s OR address ILIKE %s OR amenities::text ILIKE %s)
                ORDER BY stars DESC, name
                LIMIT %s
                """,
                (city.strip(), f"%{city.strip()}%", pattern, pattern, pattern, limit),
            )
            return [_hotel(row) for row in await rows.fetchall()]

    async def ensure_external_hotel(
        self, *, name: str, city: str, address: str, location: str | None, external_ref: str
    ) -> Hotel:
        hotel_id = f"amap-{uuid.uuid5(uuid.NAMESPACE_URL, external_ref).hex[:16]}"
        base_rate = 320 + int(uuid.uuid5(uuid.NAMESPACE_DNS, name).hex[:4], 16) % 480
        async with self.pool.connection() as connection, connection.transaction():
            cursor = await connection.execute(
                """
                INSERT INTO travel_hotels
                    (hotel_id, external_ref, name, city, address, location,
                     stars, source, amenities)
                VALUES (%s, %s, %s, %s, %s, %s, 4, 'amap_sandbox', %s)
                ON CONFLICT (external_ref) DO UPDATE SET
                    name = EXCLUDED.name, address = EXCLUDED.address, location = EXCLUDED.location
                RETURNING *
                """,
                (
                    hotel_id,
                    external_ref,
                    name,
                    city or "未指定城市",
                    address or "高德地点结果",
                    location,
                    Jsonb(["Wi-Fi", "行李寄存", "沙箱库存"]),
                ),
            )
            row = await cursor.fetchone()
            actual_id = row["hotel_id"]
            for suffix, room_type, multiplier, capacity in (
                ("standard", "标准大床房", 1.0, 2),
                ("family", "家庭房", 1.45, 4),
            ):
                await connection.execute(
                    """
                    INSERT INTO travel_room_inventory
                        (room_id, hotel_id, room_type, nightly_rate, capacity, available_rooms,
                         cancellation_policy)
                    VALUES (%s, %s, %s, %s, %s, 8, %s)
                    ON CONFLICT (hotel_id, room_type) DO NOTHING
                    """,
                    (
                        f"{actual_id}-{suffix}",
                        actual_id,
                        room_type,
                        round(base_rate * multiplier, 2),
                        capacity,
                        "入住前一天 18:00 前可免费取消；此后收取首晚房费。",
                    ),
                )
            return _hotel(row)

    async def get_hotel(self, hotel_id_or_name: str) -> Hotel | None:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT * FROM travel_hotels
                WHERE hotel_id = %s OR name ILIKE %s
                ORDER BY CASE WHEN hotel_id = %s THEN 0 ELSE 1 END
                LIMIT 1
                """,
                (hotel_id_or_name, f"%{hotel_id_or_name}%", hotel_id_or_name),
            )
            row = await cursor.fetchone()
            return _hotel(row) if row else None

    async def list_rooms(
        self,
        hotel_id: str,
        guests: int,
        *,
        check_in: date,
        check_out: date,
    ) -> list[RoomOffer]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT room.room_id, room.hotel_id, room.room_type, room.nightly_rate,
                       room.capacity,
                       COALESCE(MIN(daily.available_rooms), room.available_rooms)
                           AS available_rooms,
                       room.cancellation_policy
                FROM travel_room_inventory AS room
                LEFT JOIN travel_daily_inventory AS daily
                  ON daily.room_id = room.room_id
                 AND daily.stay_date >= %s AND daily.stay_date < %s
                WHERE room.hotel_id = %s AND room.capacity >= %s
                GROUP BY room.room_id
                HAVING COALESCE(MIN(daily.available_rooms), room.available_rooms) > 0
                ORDER BY room.nightly_rate
                """,
                (check_in, check_out, hotel_id, guests),
            )
            return [_room(row) for row in await cursor.fetchall()]

    async def save_quote(self, quote: Quote) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                """
                INSERT INTO travel_quotes
                    (quote_id, user_id, hotel_id, room_id, check_in, check_out, guests,
                     total_amount, status, expires_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    quote.quote_id,
                    quote.user_id,
                    quote.hotel.hotel_id,
                    quote.room.room_id,
                    quote.check_in,
                    quote.check_out,
                    quote.guests,
                    quote.total_amount,
                    quote.status.value,
                    quote.expires_at,
                ),
            )

    async def get_quote(self, user_id: str, quote_id: str) -> Quote | None:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT q.*, h.*, r.room_type, r.nightly_rate, r.capacity,
                       r.available_rooms, r.cancellation_policy
                FROM travel_quotes q
                JOIN travel_hotels h ON h.hotel_id = q.hotel_id
                JOIN travel_room_inventory r ON r.room_id = q.room_id
                WHERE q.user_id = %s AND q.quote_id = %s
                """,
                (user_id, quote_id),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            hotel = _hotel(row)
            room = _room(row)
            return Quote(
                quote_id=row["quote_id"],
                user_id=row["user_id"],
                hotel=hotel,
                room=room,
                check_in=row["check_in"],
                check_out=row["check_out"],
                guests=row["guests"],
                total_amount=float(row["total_amount"]),
                status=QuoteStatus(row["status"]),
                expires_at=row["expires_at"],
            )

    async def create_order(
        self, quote: Quote, *, idempotency_key: str, payment_method: str
    ) -> Order:
        order_id = f"ord-{uuid.uuid4().hex[:16]}"
        payment_id = f"pay-{uuid.uuid4().hex[:16]}"
        async with self.pool.connection() as connection, connection.transaction():
            existing = await connection.execute(
                "SELECT * FROM travel_orders WHERE user_id = %s AND idempotency_key = %s",
                (quote.user_id, idempotency_key),
            )
            existing_row = await existing.fetchone()
            if existing_row:
                return _order(existing_row)
            for stay_date in _stay_dates(quote.check_in, quote.check_out):
                await connection.execute(
                    """
                    INSERT INTO travel_daily_inventory (room_id, stay_date, available_rooms)
                    SELECT room_id, %s, available_rooms FROM travel_room_inventory
                    WHERE room_id = %s ON CONFLICT (room_id, stay_date) DO NOTHING
                    """,
                    (stay_date, quote.room.room_id),
                )
                locked = await connection.execute(
                    """
                    SELECT available_rooms FROM travel_daily_inventory
                    WHERE room_id = %s AND stay_date = %s FOR UPDATE
                    """,
                    (quote.room.room_id, stay_date),
                )
                inventory = await locked.fetchone()
                if not inventory or inventory["available_rooms"] <= 0:
                    raise ValueError(f"所选房型在 {stay_date} 已无可用库存。")
            await connection.execute(
                """
                UPDATE travel_daily_inventory SET available_rooms = available_rooms - 1,
                       updated_at = NOW()
                WHERE room_id = %s AND stay_date >= %s AND stay_date < %s
                """,
                (quote.room.room_id, quote.check_in, quote.check_out),
            )
            cursor = await connection.execute(
                """
                INSERT INTO travel_orders
                    (order_id, user_id, quote_id, idempotency_key, hotel_name, room_type,
                     check_in, check_out, guests, total_amount, status, payment_status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    order_id,
                    quote.user_id,
                    quote.quote_id,
                    idempotency_key,
                    quote.hotel.name,
                    quote.room.room_type,
                    quote.check_in,
                    quote.check_out,
                    quote.guests,
                    quote.total_amount,
                    OrderStatus.PAYMENT_PENDING.value,
                    PaymentStatus.PENDING.value,
                ),
            )
            row = await cursor.fetchone()
            await connection.execute(
                """
                INSERT INTO travel_payments (payment_id, order_id, amount, method, status)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    payment_id,
                    order_id,
                    quote.total_amount,
                    payment_method,
                    PaymentStatus.PENDING.value,
                ),
            )
            await connection.execute(
                "UPDATE travel_quotes SET status = %s WHERE quote_id = %s",
                (QuoteStatus.ACCEPTED.value, quote.quote_id),
            )
            return _order(row)

    async def get_order(self, user_id: str, order_id: str) -> Order | None:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT * FROM travel_orders WHERE user_id = %s AND order_id = %s",
                (user_id, order_id),
            )
            row = await cursor.fetchone()
            return _order(row) if row else None

    async def list_orders(self, user_id: str, limit: int = 20) -> list[Order]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT * FROM travel_orders WHERE user_id = %s ORDER BY updated_at DESC LIMIT %s",
                (user_id, limit),
            )
            return [_order(row) for row in await cursor.fetchall()]

    async def update_order(
        self,
        user_id: str,
        order_id: str,
        *,
        status: OrderStatus,
        payment_status: PaymentStatus | None = None,
    ) -> Order:
        async with self.pool.connection() as connection, connection.transaction():
            cursor = await connection.execute(
                """
                UPDATE travel_orders
                SET status = %s,
                    payment_status = COALESCE(%s, payment_status),
                    updated_at = NOW()
                WHERE user_id = %s AND order_id = %s
                RETURNING *
                """,
                (
                    status.value,
                    payment_status.value if payment_status else None,
                    user_id,
                    order_id,
                ),
            )
            row = await cursor.fetchone()
            if not row:
                raise ValueError("订单不存在或不属于当前用户。")
            if payment_status:
                await connection.execute(
                    """
                    UPDATE travel_payments SET status = %s, updated_at = NOW()
                    WHERE order_id = %s
                    """,
                    (payment_status.value, order_id),
                )
            return _order(row)

    async def change_order_dates(
        self, user_id: str, order_id: str, *, check_in: date, check_out: date
    ) -> Order:
        async with self.pool.connection() as connection, connection.transaction():
            current_cursor = await connection.execute(
                """
                SELECT orders.*, quote.room_id
                FROM travel_orders AS orders
                JOIN travel_quotes AS quote ON quote.quote_id = orders.quote_id
                WHERE orders.user_id = %s AND orders.order_id = %s FOR UPDATE
                """,
                (user_id, order_id),
            )
            current = await current_cursor.fetchone()
            if not current:
                raise ValueError("订单不存在或不属于当前用户。")
            room_id = current["room_id"]
            await connection.execute(
                """
                UPDATE travel_daily_inventory AS daily
                SET available_rooms = LEAST(daily.available_rooms + 1, room.available_rooms),
                    updated_at = NOW()
                FROM travel_room_inventory AS room
                WHERE daily.room_id = room.room_id AND daily.room_id = %s
                  AND daily.stay_date >= %s AND daily.stay_date < %s
                """,
                (room_id, current["check_in"], current["check_out"]),
            )
            for stay_date in _stay_dates(check_in, check_out):
                await connection.execute(
                    """
                    INSERT INTO travel_daily_inventory (room_id, stay_date, available_rooms)
                    SELECT room_id, %s, available_rooms FROM travel_room_inventory
                    WHERE room_id = %s ON CONFLICT (room_id, stay_date) DO NOTHING
                    """,
                    (stay_date, room_id),
                )
                locked = await connection.execute(
                    """
                    SELECT available_rooms FROM travel_daily_inventory
                    WHERE room_id = %s AND stay_date = %s FOR UPDATE
                    """,
                    (room_id, stay_date),
                )
                inventory = await locked.fetchone()
                if not inventory or inventory["available_rooms"] <= 0:
                    raise ValueError(f"所选房型在 {stay_date} 已无可用库存。")
            await connection.execute(
                """
                UPDATE travel_daily_inventory SET available_rooms = available_rooms - 1,
                       updated_at = NOW()
                WHERE room_id = %s AND stay_date >= %s AND stay_date < %s
                """,
                (room_id, check_in, check_out),
            )
            cursor = await connection.execute(
                """
                UPDATE travel_orders
                SET check_in = %s, check_out = %s, status = %s, updated_at = NOW()
                WHERE user_id = %s AND order_id = %s
                RETURNING *
                """,
                (check_in, check_out, OrderStatus.CONFIRMED.value, user_id, order_id),
            )
            row = await cursor.fetchone()
            if not row:
                raise ValueError("订单不存在或不属于当前用户。")
            return _order(row)

    async def release_inventory(self, order: Order) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                """
                UPDATE travel_daily_inventory AS daily
                SET available_rooms = LEAST(daily.available_rooms + 1, room.available_rooms),
                    updated_at = NOW()
                FROM travel_quotes AS quote, travel_room_inventory AS room
                WHERE quote.quote_id = %s AND room.room_id = quote.room_id
                  AND daily.room_id = quote.room_id
                  AND daily.stay_date >= %s AND daily.stay_date < %s
                """,
                (order.quote_id, order.check_in, order.check_out),
            )

    async def record_refund(
        self, user_id: str, order_id: str, *, amount: float, reason: str
    ) -> str:
        order = await self.get_order(user_id, order_id)
        if order is None:
            raise ValueError("订单不存在或不属于当前用户。")
        refund_id = f"ref-{uuid.uuid4().hex[:16]}"
        async with self.pool.connection() as connection, connection.transaction():
            payment = await connection.execute(
                """
                SELECT payment_id FROM travel_payments
                WHERE order_id = %s ORDER BY created_at DESC LIMIT 1
                """,
                (order_id,),
            )
            payment_row = await payment.fetchone()
            await connection.execute(
                """
                INSERT INTO travel_refunds
                    (refund_id, order_id, payment_id, amount, reason, status)
                VALUES (%s, %s, %s, %s, %s, 'completed')
                """,
                (
                    refund_id,
                    order_id,
                    payment_row["payment_id"] if payment_row else None,
                    amount,
                    reason,
                ),
            )
        return refund_id

    async def create_complaint(
        self,
        user_id: str,
        *,
        order_id: str | None,
        category: str,
        description: str,
    ) -> str:
        if order_id and await self.get_order(user_id, order_id) is None:
            raise ValueError("订单不存在或不属于当前用户。")
        complaint_id = f"cmp-{uuid.uuid4().hex[:16]}"
        async with self.pool.connection() as connection:
            await connection.execute(
                """
                INSERT INTO travel_complaints
                    (complaint_id, user_id, order_id, category, description)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (complaint_id, user_id, order_id, category, description),
            )
        return complaint_id

    async def append_trace(self, event: dict[str, Any]) -> None:
        async with self.pool.connection() as connection:
            await connection.execute(
                """
                INSERT INTO agent_trace_events
                    (trace_id, user_id, session_id, task_id, sequence, event_type, actor,
                     status, duration_ms, token_usage, input_summary, output_summary,
                     error_code, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (trace_id, sequence) DO NOTHING
                """,
                (
                    event["trace_id"],
                    event["user_id"],
                    event["session_id"],
                    event["task_id"],
                    event["sequence"],
                    event["event_type"],
                    event["actor"],
                    event["status"],
                    event.get("duration_ms"),
                    event.get("token_usage"),
                    event.get("input_summary"),
                    event.get("output_summary"),
                    event.get("error_code"),
                    Jsonb(event.get("metadata") or {}),
                ),
            )

    async def get_trace(self, user_id: str, session_id: str, task_id: str) -> list[dict[str, Any]]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT trace_id, sequence, event_type, actor, status, duration_ms,
                       token_usage, input_summary, output_summary, error_code, metadata,
                       created_at AS timestamp
                FROM agent_trace_events
                WHERE user_id = %s AND session_id = %s AND task_id = %s
                ORDER BY sequence
                """,
                (user_id, session_id, task_id),
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def get_metrics(self) -> dict[str, Any]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT
                    COUNT(DISTINCT task_id) AS tasks,
                    COUNT(*) FILTER (
                        WHERE event_type = 'task.completed' AND status = 'success'
                    ) AS completed,
                    COUNT(*) FILTER (WHERE status = 'error') AS errors,
                    COALESCE(
                        AVG(duration_ms) FILTER (WHERE duration_ms IS NOT NULL), 0
                    ) AS avg_duration_ms,
                    COALESCE(SUM(token_usage), 0) AS total_tokens
                FROM agent_trace_events
                """
            )
            aggregate = dict(await cursor.fetchone())
            task_count = int(aggregate.get("tasks") or 0)
            completed = int(aggregate.get("completed") or 0)
            aggregate["success_rate"] = round(completed / task_count, 4) if task_count else 0
            routes = await connection.execute(
                """
                SELECT actor, COUNT(*) AS count FROM agent_trace_events
                WHERE event_type = 'route.selected' GROUP BY actor ORDER BY count DESC
                """
            )
            aggregate["route_distribution"] = {
                row["actor"]: row["count"] for row in await routes.fetchall()
            }
            agent_latency = await connection.execute(
                """
                SELECT actor, AVG(duration_ms) AS avg_ms FROM agent_trace_events
                WHERE event_type = 'agent.exited' AND duration_ms IS NOT NULL
                GROUP BY actor ORDER BY actor
                """
            )
            aggregate["agent_latency_ms"] = {
                row["actor"]: round(float(row["avg_ms"]), 2)
                for row in await agent_latency.fetchall()
            }
            tool_latency = await connection.execute(
                """
                SELECT metadata ->> 'tool' AS tool, AVG(duration_ms) AS avg_ms
                FROM agent_trace_events
                WHERE event_type = 'tool.completed' AND duration_ms IS NOT NULL
                GROUP BY metadata ->> 'tool' ORDER BY metadata ->> 'tool'
                """
            )
            aggregate["tool_latency_ms"] = {
                row["tool"]: round(float(row["avg_ms"]), 2)
                for row in await tool_latency.fetchall()
                if row["tool"]
            }
            failures = await connection.execute(
                """
                SELECT COALESCE(error_code, 'unknown') AS error_code, COUNT(*) AS count
                FROM agent_trace_events WHERE status = 'error'
                GROUP BY COALESCE(error_code, 'unknown') ORDER BY count DESC
                """
            )
            aggregate["failure_types"] = {
                row["error_code"]: row["count"] for row in await failures.fetchall()
            }
            compensations = await connection.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE reason LIKE '%%自动补偿%%') AS attempted,
                    COUNT(*) FILTER (
                        WHERE reason LIKE '%%自动补偿%%' AND status = 'completed'
                    ) AS succeeded
                FROM travel_refunds
                """
            )
            compensation_row = await compensations.fetchone()
            aggregate["compensation_results"] = {
                "attempted": int(compensation_row["attempted"] or 0),
                "succeeded": int(compensation_row["succeeded"] or 0),
            }
            return aggregate


class InMemoryTravelRepository:
    """Deterministic repository used by unit tests and the offline evaluator."""

    def __init__(self, hotels: Sequence[Hotel] | None = None) -> None:
        self.hotels = {item.hotel_id: item for item in hotels or []}
        self.rooms: dict[str, RoomOffer] = {}
        self.quotes: dict[str, Quote] = {}
        self.orders: dict[str, Order] = {}
        self.idempotency: dict[tuple[str, str], str] = {}
        self.daily_inventory: dict[tuple[str, date], int] = {}
        self.traces: list[dict[str, Any]] = []
        self.complaints: list[dict[str, Any]] = []
        self.refunds: list[dict[str, Any]] = []

    async def search_hotels(self, city: str, keywords: str, limit: int = 5) -> list[Hotel]:
        normalized = keywords.lower()
        return [
            hotel
            for hotel in self.hotels.values()
            if (not city or city in hotel.city)
            and (
                not normalized
                or normalized in f"{hotel.name}{hotel.address}{hotel.amenities}".lower()
            )
        ][:limit]

    async def ensure_external_hotel(
        self, *, name: str, city: str, address: str, location: str | None, external_ref: str
    ) -> Hotel:
        hotel_id = f"amap-{uuid.uuid5(uuid.NAMESPACE_URL, external_ref).hex[:16]}"
        hotel = self.hotels.get(hotel_id) or Hotel(
            hotel_id=hotel_id,
            name=name,
            city=city or "未指定城市",
            address=address,
            location=location,
            stars=4,
            source="amap_sandbox",
            amenities=["Wi-Fi", "沙箱库存"],
        )
        self.hotels[hotel_id] = hotel
        room = RoomOffer(
            room_id=f"{hotel_id}-standard",
            hotel_id=hotel_id,
            room_type="标准大床房",
            nightly_rate=520,
            capacity=2,
            available_rooms=8,
            cancellation_policy="入住前一天 18:00 前可免费取消。",
        )
        self.rooms[room.room_id] = room
        return hotel

    async def get_hotel(self, hotel_id_or_name: str) -> Hotel | None:
        if hotel_id_or_name in self.hotels:
            return self.hotels[hotel_id_or_name]
        return next((item for item in self.hotels.values() if hotel_id_or_name in item.name), None)

    async def list_rooms(
        self,
        hotel_id: str,
        guests: int,
        *,
        check_in: date,
        check_out: date,
    ) -> list[RoomOffer]:
        available: list[RoomOffer] = []
        for room in self.rooms.values():
            if room.hotel_id != hotel_id or room.capacity < guests:
                continue
            nightly = [
                self.daily_inventory.get((room.room_id, stay_date), room.available_rooms)
                for stay_date in _stay_dates(check_in, check_out)
            ]
            minimum = min(nightly, default=room.available_rooms)
            if minimum > 0:
                available.append(room.model_copy(update={"available_rooms": minimum}))
        return available

    async def save_quote(self, quote: Quote) -> None:
        self.quotes[quote.quote_id] = quote

    async def get_quote(self, user_id: str, quote_id: str) -> Quote | None:
        quote = self.quotes.get(quote_id)
        return quote if quote and quote.user_id == user_id else None

    async def create_order(
        self, quote: Quote, *, idempotency_key: str, payment_method: str
    ) -> Order:
        idempotency_index = (quote.user_id, idempotency_key)
        if idempotency_index in self.idempotency:
            return self.orders[self.idempotency[idempotency_index]]
        room = self.rooms.get(quote.room.room_id)
        if room is None:
            raise ValueError("所选房型不存在。")
        for stay_date in _stay_dates(quote.check_in, quote.check_out):
            inventory_key = (room.room_id, stay_date)
            available = self.daily_inventory.get(inventory_key, room.available_rooms)
            if available <= 0:
                raise ValueError(f"所选房型在 {stay_date} 已无可用库存。")
        for stay_date in _stay_dates(quote.check_in, quote.check_out):
            inventory_key = (room.room_id, stay_date)
            self.daily_inventory[inventory_key] = (
                self.daily_inventory.get(inventory_key, room.available_rooms) - 1
            )
        now = datetime.now(UTC)
        order = Order(
            order_id=f"ord-{uuid.uuid4().hex[:16]}",
            user_id=quote.user_id,
            quote_id=quote.quote_id,
            hotel_name=quote.hotel.name,
            room_type=quote.room.room_type,
            check_in=quote.check_in,
            check_out=quote.check_out,
            guests=quote.guests,
            total_amount=quote.total_amount,
            status=OrderStatus.PAYMENT_PENDING,
            payment_status=PaymentStatus.PENDING,
            created_at=now,
            updated_at=now,
        )
        self.orders[order.order_id] = order
        self.idempotency[idempotency_index] = order.order_id
        return order

    async def get_order(self, user_id: str, order_id: str) -> Order | None:
        order = self.orders.get(order_id)
        return order if order and order.user_id == user_id else None

    async def list_orders(self, user_id: str, limit: int = 20) -> list[Order]:
        return [item for item in self.orders.values() if item.user_id == user_id][:limit]

    async def update_order(
        self,
        user_id: str,
        order_id: str,
        *,
        status: OrderStatus,
        payment_status: PaymentStatus | None = None,
    ) -> Order:
        order = await self.get_order(user_id, order_id)
        if order is None:
            raise ValueError("订单不存在或不属于当前用户。")
        updated = order.model_copy(
            update={
                "status": status,
                "payment_status": payment_status or order.payment_status,
                "updated_at": datetime.now(UTC),
            }
        )
        self.orders[order_id] = updated
        return updated

    async def change_order_dates(
        self, user_id: str, order_id: str, *, check_in: date, check_out: date
    ) -> Order:
        order = await self.get_order(user_id, order_id)
        if order is None:
            raise ValueError("订单不存在或不属于当前用户。")
        quote = self.quotes.get(order.quote_id)
        if quote is None:
            raise ValueError("订单报价不存在。")
        snapshot = dict(self.daily_inventory)
        await self.release_inventory(order)
        room = self.rooms[quote.room.room_id]
        try:
            for stay_date in _stay_dates(check_in, check_out):
                key = (room.room_id, stay_date)
                available = self.daily_inventory.get(key, room.available_rooms)
                if available <= 0:
                    raise ValueError(f"所选房型在 {stay_date} 已无可用库存。")
                self.daily_inventory[key] = available - 1
        except Exception:
            self.daily_inventory = snapshot
            raise
        updated = order.model_copy(
            update={
                "check_in": check_in,
                "check_out": check_out,
                "status": OrderStatus.CONFIRMED,
                "updated_at": datetime.now(UTC),
            }
        )
        self.orders[order_id] = updated
        return updated

    async def release_inventory(self, order: Order) -> None:
        quote = self.quotes.get(order.quote_id)
        if quote is None:
            return
        room = self.rooms.get(quote.room.room_id)
        if room is None:
            return
        for stay_date in _stay_dates(order.check_in, order.check_out):
            key = (room.room_id, stay_date)
            current = self.daily_inventory.get(key, room.available_rooms)
            self.daily_inventory[key] = min(current + 1, room.available_rooms)

    async def record_refund(
        self, user_id: str, order_id: str, *, amount: float, reason: str
    ) -> str:
        if await self.get_order(user_id, order_id) is None:
            raise ValueError("订单不存在或不属于当前用户。")
        refund_id = f"ref-{uuid.uuid4().hex[:16]}"
        self.refunds.append(
            {
                "refund_id": refund_id,
                "user_id": user_id,
                "order_id": order_id,
                "amount": amount,
                "reason": reason,
                "status": "completed",
            }
        )
        return refund_id

    async def create_complaint(
        self,
        user_id: str,
        *,
        order_id: str | None,
        category: str,
        description: str,
    ) -> str:
        if order_id and await self.get_order(user_id, order_id) is None:
            raise ValueError("订单不存在或不属于当前用户。")
        complaint_id = f"cmp-{uuid.uuid4().hex[:16]}"
        self.complaints.append(
            {"complaint_id": complaint_id, "user_id": user_id, "order_id": order_id}
        )
        return complaint_id

    async def append_trace(self, event: dict[str, Any]) -> None:
        self.traces.append(dict(event, timestamp=datetime.now(UTC)))

    async def get_trace(self, user_id: str, session_id: str, task_id: str) -> list[dict[str, Any]]:
        return [
            item
            for item in self.traces
            if item["user_id"] == user_id
            and item["session_id"] == session_id
            and item["task_id"] == task_id
        ]

    async def get_metrics(self) -> dict[str, Any]:
        task_count = len({item["task_id"] for item in self.traces})
        completed = sum(
            item["event_type"] == "task.completed" and item.get("status") == "success"
            for item in self.traces
        )
        durations = [item["duration_ms"] for item in self.traces if item.get("duration_ms")]
        routes: dict[str, int] = {}
        agent_latency: dict[str, list[float]] = {}
        tool_latency: dict[str, list[float]] = {}
        failures: dict[str, int] = {}
        for item in self.traces:
            if item["event_type"] == "route.selected":
                routes[item["actor"]] = routes.get(item["actor"], 0) + 1
            if item["event_type"] == "agent.exited" and item.get("duration_ms"):
                agent_latency.setdefault(item["actor"], []).append(item["duration_ms"])
            if item["event_type"] == "tool.completed" and item.get("duration_ms"):
                name = str((item.get("metadata") or {}).get("tool") or "unknown")
                tool_latency.setdefault(name, []).append(item["duration_ms"])
            if item.get("status") == "error":
                code = str(item.get("error_code") or "unknown")
                failures[code] = failures.get(code, 0) + 1
        compensation_refunds = [item for item in self.refunds if "自动补偿" in item["reason"]]
        return {
            "tasks": task_count,
            "completed": completed,
            "errors": sum(failures.values()),
            "success_rate": round(completed / task_count, 4) if task_count else 0,
            "avg_duration_ms": sum(durations) / len(durations) if durations else 0,
            "total_tokens": sum(item.get("token_usage") or 0 for item in self.traces),
            "route_distribution": routes,
            "agent_latency_ms": {
                key: round(sum(values) / len(values), 2) for key, values in agent_latency.items()
            },
            "tool_latency_ms": {
                key: round(sum(values) / len(values), 2) for key, values in tool_latency.items()
            },
            "failure_types": failures,
            "compensation_results": {
                "attempted": len(compensation_refunds),
                "succeeded": sum(item["status"] == "completed" for item in compensation_refunds),
            },
        }
