from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest

from travel_agent_orchestrator.travel.models import Hotel, PaymentStatus, RoomOffer
from travel_agent_orchestrator.travel.repository import InMemoryTravelRepository
from travel_agent_orchestrator.travel.service import DomainRuleError, HotelSandboxService


def repository_with_inventory() -> InMemoryTravelRepository:
    hotel = Hotel(
        hotel_id="hotel-1",
        name="湖畔精选酒店",
        city="杭州",
        address="湖滨路 66 号",
        stars=4,
    )
    repository = InMemoryTravelRepository([hotel])
    repository.rooms["room-1"] = RoomOffer(
        room_id="room-1",
        hotel_id="hotel-1",
        room_type="标准大床房",
        nightly_rate=498,
        capacity=2,
        available_rooms=5,
        cancellation_policy="入住前一天可免费取消。",
    )
    return repository


def create_quote(service: HotelSandboxService, user_id: str = "user-1"):
    check_in = date.today() + timedelta(days=5)
    return asyncio.run(
        service.create_quote(
            user_id=user_id,
            hotel="hotel-1",
            check_in=check_in,
            check_out=check_in + timedelta(days=2),
            guests=2,
        )
    )


def test_booking_is_idempotent_and_never_accepts_real_payment_details() -> None:
    service = HotelSandboxService(repository_with_inventory())
    quote = create_quote(service)
    first, _ = asyncio.run(
        service.confirm_booking(user_id="user-1", quote_id=quote.quote_id, idempotency_key="same")
    )
    second, replay = asyncio.run(
        service.confirm_booking(user_id="user-1", quote_id=quote.quote_id, idempotency_key="same")
    )
    assert first.order_id == second.order_id
    assert replay["idempotent_replay"] is True

    with pytest.raises(DomainRuleError, match="demo_wallet"):
        asyncio.run(
            service.confirm_booking(
                user_id="user-1",
                quote_id=quote.quote_id,
                idempotency_key="unsafe",
                payment_method="credit_card",
            )
        )


def test_payment_failure_after_capture_is_compensated() -> None:
    repository = repository_with_inventory()
    service = HotelSandboxService(repository, payment_mode="fail_after_capture")
    quote = create_quote(service)
    order, saga = asyncio.run(
        service.confirm_booking(
            user_id="user-1", quote_id=quote.quote_id, idempotency_key="compensate"
        )
    )

    assert order.status == "refunded"
    assert order.payment_status == PaymentStatus.REFUNDED
    assert saga["compensated"] is True
    assert saga["refund_id"].startswith("ref-")
    assert repository.rooms["room-1"].available_rooms == 5
    assert set(repository.daily_inventory.values()) == {5}


def test_order_ownership_is_enforced() -> None:
    service = HotelSandboxService(repository_with_inventory())
    quote = create_quote(service)
    order, _ = asyncio.run(
        service.confirm_booking(user_id="user-1", quote_id=quote.quote_id, idempotency_key="owner")
    )

    with pytest.raises(DomainRuleError, match="不属于"):
        asyncio.run(service.get_order("user-2", order.order_id))


def test_inventory_is_reserved_and_released_per_stay_date() -> None:
    repository = repository_with_inventory()
    service = HotelSandboxService(repository)
    quote = create_quote(service)
    stay_dates = [quote.check_in, quote.check_in + timedelta(days=1)]

    order, _ = asyncio.run(
        service.confirm_booking(
            user_id="user-1",
            quote_id=quote.quote_id,
            idempotency_key="daily-inventory",
        )
    )
    assert [repository.daily_inventory[("room-1", day)] for day in stay_dates] == [4, 4]

    asyncio.run(
        service.cancel_booking(
            user_id="user-1",
            order_id=order.order_id,
            reason="测试释放日期库存",
        )
    )
    assert [repository.daily_inventory[("room-1", day)] for day in stay_dates] == [5, 5]


def test_quote_rejects_a_sold_out_night() -> None:
    repository = repository_with_inventory()
    service = HotelSandboxService(repository)
    check_in = date.today() + timedelta(days=5)
    repository.daily_inventory[("room-1", check_in)] = 0

    with pytest.raises(DomainRuleError, match="没有符合条件"):
        asyncio.run(
            service.create_quote(
                user_id="user-1",
                hotel="hotel-1",
                check_in=check_in,
                check_out=check_in + timedelta(days=2),
                guests=2,
            )
        )


@pytest.mark.parametrize("requested", ["双人房", "双人间", "标准间", "标间"])
def test_common_room_aliases_match_available_sandbox_inventory(requested: str) -> None:
    service = HotelSandboxService(repository_with_inventory())
    check_in = date.today() + timedelta(days=5)

    quote = asyncio.run(
        service.create_quote(
            user_id="user-1",
            hotel="hotel-1",
            check_in=check_in,
            check_out=check_in + timedelta(days=2),
            guests=2,
            room_type=requested,
        )
    )

    assert quote.room.room_type == "标准大床房"


def test_unknown_room_type_returns_available_types_instead_of_sold_out_message() -> None:
    service = HotelSandboxService(repository_with_inventory())
    check_in = date.today() + timedelta(days=5)

    with pytest.raises(DomainRuleError, match="当前可选房型：标准大床房"):
        asyncio.run(
            service.create_quote(
                user_id="user-1",
                hotel="hotel-1",
                check_in=check_in,
                check_out=check_in + timedelta(days=2),
                guests=2,
                room_type="总统套房",
            )
        )
