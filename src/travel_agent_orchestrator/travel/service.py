"""Deterministic hotel, booking, after-sales, and payment saga services."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from travel_agent_orchestrator.travel.models import (
    Order,
    OrderStatus,
    PaymentStatus,
    Quote,
    QuoteStatus,
    RoomOffer,
)
from travel_agent_orchestrator.travel.repository import TravelRepository

PaymentMode = Literal["success", "decline", "fail_after_capture"]


class DomainRuleError(ValueError):
    """Raised when an action violates a hotel workflow invariant."""


def _normalize_room_label(value: str) -> str:
    return "".join(value.lower().split()).replace("房型", "房")


def _room_match_score(room: RoomOffer, requested: str) -> int:
    """Score common user-facing aliases without pretending an unavailable type exists."""
    target = _normalize_room_label(requested)
    actual = _normalize_room_label(room.room_type)
    if target in actual or actual in target:
        return 100
    if target in {"标准间", "标间", "标准房"} and "标准" in actual:
        return 90
    if target in {"双人房", "双人间"} and room.capacity >= 2:
        return 80 + int("标准" in actual)
    if "双床" in target and "双床" in actual:
        return 90
    if "大床" in target and "大床" in actual:
        return 90
    if "家庭" in target and "家庭" in actual:
        return 90
    return 0


class HotelSandboxService:
    """Expose repeatable domain operations without pretending to create real bookings."""

    def __init__(
        self,
        repository: TravelRepository,
        *,
        payment_mode: PaymentMode = "success",
        today_provider: Callable[[], date] = date.today,
    ):
        self.repository = repository
        self.payment_mode = payment_mode
        self.today = today_provider

    async def search_hotels(self, city: str, keywords: str = "", limit: int = 5) -> list[dict]:
        hotels = await self.repository.search_hotels(city, keywords, limit)
        return [hotel.model_dump(mode="json") for hotel in hotels]

    async def import_amap_hotel(self, point: dict[str, Any], city: str) -> dict[str, Any]:
        external_ref = str(point.get("id") or point.get("location") or point.get("name") or "")
        if not external_ref:
            raise DomainRuleError("高德地点缺少可识别的名称或位置。")
        hotel = await self.repository.ensure_external_hotel(
            name=str(point.get("name") or "高德地点酒店"),
            city=city or str(point.get("cityname") or "未指定城市"),
            address=str(point.get("address") or "高德地点结果"),
            location=str(point.get("location") or "") or None,
            external_ref=external_ref,
        )
        return hotel.model_dump(mode="json")

    async def create_quote(
        self,
        *,
        user_id: str,
        hotel: str,
        check_in: date,
        check_out: date,
        guests: int = 1,
        room_type: str | None = None,
    ) -> Quote:
        if check_in < self.today():
            raise DomainRuleError("入住日期不能早于今天。")
        if check_out <= check_in:
            raise DomainRuleError("离店日期必须晚于入住日期。")
        if guests < 1 or guests > 8:
            raise DomainRuleError("入住人数必须在 1 到 8 人之间。")
        selected_hotel = await self.repository.get_hotel(hotel)
        if selected_hotel is None:
            selected_hotel = await self.repository.ensure_external_hotel(
                name=hotel,
                city="未指定城市",
                address="由用户选择的沙箱酒店",
                location=None,
                external_ref=f"user:{hotel}",
            )
        rooms = await self.repository.list_rooms(
            selected_hotel.hotel_id,
            guests,
            check_in=check_in,
            check_out=check_out,
        )
        if not rooms:
            raise DomainRuleError("当前日期和人数下没有符合条件的沙箱房型。")
        if room_type:
            scored = [(_room_match_score(item, room_type), item) for item in rooms]
            rooms = [item for score, item in sorted(scored, key=lambda pair: -pair[0]) if score]
            if not rooms:
                available_types = "、".join(item.room_type for _, item in scored)
                raise DomainRuleError(
                    f"沙箱酒店不提供“{room_type}”；当前可选房型：{available_types}。"
                )
        room = rooms[0]
        nights = (check_out - check_in).days
        quote = Quote(
            quote_id=f"quo-{uuid.uuid4().hex[:16]}",
            user_id=user_id,
            hotel=selected_hotel,
            room=room,
            check_in=check_in,
            check_out=check_out,
            guests=guests,
            total_amount=round(room.nightly_rate * nights, 2),
            status=QuoteStatus.ACTIVE,
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )
        await self.repository.save_quote(quote)
        return quote

    async def confirm_booking(
        self,
        *,
        user_id: str,
        quote_id: str,
        idempotency_key: str,
        payment_method: str = "demo_wallet",
    ) -> tuple[Order, dict[str, Any]]:
        if payment_method != "demo_wallet":
            raise DomainRuleError("演示环境只允许使用 demo_wallet，不接收真实支付信息。")
        quote = await self.repository.get_quote(user_id, quote_id)
        if quote is None:
            raise DomainRuleError("报价不存在或不属于当前用户。")
        if quote.expires_at <= datetime.now(UTC):
            raise DomainRuleError("报价已过期，请重新获取报价。")
        order = await self.repository.create_order(
            quote, idempotency_key=idempotency_key, payment_method=payment_method
        )
        if order.status != OrderStatus.PAYMENT_PENDING:
            return order, {
                "idempotent_replay": True,
                "payment": order.payment_status.value,
                "sandbox": True,
            }
        if self.payment_mode == "decline":
            order = await self.repository.update_order(
                user_id,
                order.order_id,
                status=OrderStatus.PAYMENT_FAILED,
                payment_status=PaymentStatus.DECLINED,
            )
            await self.repository.release_inventory(order)
            return order, {"payment": "declined", "sandbox": True}
        order = await self.repository.update_order(
            user_id,
            order.order_id,
            status=OrderStatus.CONFIRMED,
            payment_status=PaymentStatus.CAPTURED,
        )
        if self.payment_mode == "fail_after_capture":
            refund_id = await self.repository.record_refund(
                user_id,
                order.order_id,
                amount=order.total_amount,
                reason="预订提交失败后的自动补偿",
            )
            order = await self.repository.update_order(
                user_id,
                order.order_id,
                status=OrderStatus.REFUNDED,
                payment_status=PaymentStatus.REFUNDED,
            )
            await self.repository.release_inventory(order)
            return order, {
                "payment": "captured_then_refunded",
                "refund_id": refund_id,
                "compensated": True,
                "sandbox": True,
            }
        return order, {"payment": "captured", "compensated": False, "sandbox": True}

    async def get_order(self, user_id: str, order_id: str) -> Order:
        order = await self.repository.get_order(user_id, order_id)
        if order is None:
            raise DomainRuleError("订单不存在或不属于当前用户。")
        return order

    async def list_orders(self, user_id: str) -> list[Order]:
        return await self.repository.list_orders(user_id)

    async def change_dates(
        self, *, user_id: str, order_id: str, check_in: date, check_out: date
    ) -> Order:
        order = await self.get_order(user_id, order_id)
        if order.status != OrderStatus.CONFIRMED:
            raise DomainRuleError("只有已确认订单可以改期。")
        if check_in < self.today() or check_out <= check_in:
            raise DomainRuleError("新的入住和离店日期无效。")
        await self.repository.update_order(user_id, order_id, status=OrderStatus.CHANGE_PENDING)
        try:
            return await self.repository.change_order_dates(
                user_id, order_id, check_in=check_in, check_out=check_out
            )
        except Exception:
            await self.repository.update_order(user_id, order_id, status=OrderStatus.CONFIRMED)
            raise

    async def cancel_booking(self, *, user_id: str, order_id: str, reason: str) -> dict[str, Any]:
        order = await self.get_order(user_id, order_id)
        if order.status != OrderStatus.CONFIRMED:
            raise DomainRuleError("只有已确认订单可以取消。")
        await self.repository.update_order(user_id, order_id, status=OrderStatus.CANCEL_PENDING)
        refundable = order.check_in > self.today() + timedelta(days=1)
        refund_amount = (
            order.total_amount
            if refundable
            else max(
                0.0,
                order.total_amount
                - order.total_amount / max((order.check_out - order.check_in).days, 1),
            )
        )
        refund_id = None
        try:
            if refund_amount > 0:
                refund_id = await self.repository.record_refund(
                    user_id, order_id, amount=round(refund_amount, 2), reason=reason
                )
                order = await self.repository.update_order(
                    user_id,
                    order_id,
                    status=OrderStatus.REFUNDED,
                    payment_status=PaymentStatus.REFUNDED,
                )
            else:
                order = await self.repository.update_order(
                    user_id, order_id, status=OrderStatus.CANCELLED
                )
        except Exception:
            await self.repository.update_order(user_id, order_id, status=OrderStatus.CONFIRMED)
            raise
        await self.repository.release_inventory(order)
        return {
            "order": order.model_dump(mode="json"),
            "refund_id": refund_id,
            "refund_amount": round(refund_amount, 2),
            "sandbox": True,
        }

    async def request_refund(
        self, *, user_id: str, order_id: str, amount: float, reason: str
    ) -> dict[str, Any]:
        order = await self.get_order(user_id, order_id)
        if order.payment_status != PaymentStatus.CAPTURED:
            raise DomainRuleError("当前订单没有可退款的已扣款支付。")
        if amount <= 0 or amount > order.total_amount:
            raise DomainRuleError("退款金额必须大于 0 且不能超过订单总额。")
        refund_id = await self.repository.record_refund(
            user_id, order_id, amount=amount, reason=reason
        )
        order = await self.repository.update_order(
            user_id,
            order_id,
            status=OrderStatus.REFUNDED,
            payment_status=PaymentStatus.REFUNDED,
        )
        await self.repository.release_inventory(order)
        return {"order": order.model_dump(mode="json"), "refund_id": refund_id, "sandbox": True}

    async def create_complaint(
        self,
        *,
        user_id: str,
        order_id: str | None,
        category: str,
        description: str,
    ) -> dict[str, Any]:
        if len(description.strip()) < 5:
            raise DomainRuleError("请提供至少 5 个字符的投诉说明。")
        complaint_id = await self.repository.create_complaint(
            user_id,
            order_id=order_id,
            category=category,
            description=description,
        )
        return {"complaint_id": complaint_id, "status": "open", "sandbox": True}
