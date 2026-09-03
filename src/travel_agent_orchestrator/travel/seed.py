"""Deterministic hotel catalog used when external discovery is unavailable."""

from __future__ import annotations

from psycopg.types.json import Jsonb

SEED_HOTELS = (
    ("hz-westlake-01", "西湖云栖酒店", "杭州", "西湖区北山街 18 号", "120.1536,30.2520", 5, 688),
    ("hz-westlake-02", "湖畔精选酒店", "杭州", "上城区湖滨路 66 号", "120.1664,30.2513", 4, 498),
    ("hz-westlake-03", "武林雅居酒店", "杭州", "拱墅区武林广场 8 号", "120.1638,30.2741", 4, 428),
    (
        "nc-tengwang-01",
        "滕王阁江景酒店",
        "南昌",
        "东湖区沿江北大道 39 号",
        "115.8812,28.6830",
        5,
        598,
    ),
    ("nc-tengwang-02", "赣江商务酒店", "南昌", "西湖区中山路 210 号", "115.8892,28.6745", 4, 368),
    ("nc-tengwang-03", "豫章精品酒店", "南昌", "东湖区叠山路 88 号", "115.8951,28.6890", 3, 298),
    ("sh-bund-01", "外滩城市酒店", "上海", "黄浦区中山东一路 20 号", "121.4903,31.2392", 5, 988),
    ("sh-bund-02", "南京东路精选酒店", "上海", "黄浦区南京东路 300 号", "121.4801,31.2360", 4, 668),
    ("sh-bund-03", "人民广场商务酒店", "上海", "黄浦区西藏中路 180 号", "121.4752,31.2328", 4, 588),
    (
        "bj-wangfujing-01",
        "王府井国际酒店",
        "北京",
        "东城区王府井大街 88 号",
        "116.4112,39.9148",
        5,
        888,
    ),
    (
        "bj-wangfujing-02",
        "东单精选酒店",
        "北京",
        "东城区东单北大街 45 号",
        "116.4181,39.9081",
        4,
        568,
    ),
    (
        "bj-wangfujing-03",
        "前门城市酒店",
        "北京",
        "东城区前门东路 12 号",
        "116.3976,39.8996",
        3,
        398,
    ),
)


async def seed_hotel_catalog(pool) -> None:
    """Insert demo hotels and room inventory without overwriting local changes."""
    async with pool.connection() as connection, connection.transaction():
        for hotel_id, name, city, address, location, stars, base_rate in SEED_HOTELS:
            await connection.execute(
                """
                INSERT INTO travel_hotels
                    (hotel_id, name, city, address, location, stars, source, amenities)
                VALUES (%s, %s, %s, %s, %s, %s, 'sandbox', %s)
                ON CONFLICT (hotel_id) DO NOTHING
                """,
                (
                    hotel_id,
                    name,
                    city,
                    address,
                    location,
                    stars,
                    Jsonb(["Wi-Fi", "早餐", "行李寄存", "沙箱库存"]),
                ),
            )
            for suffix, room_type, multiplier, capacity, rooms in (
                ("queen", "标准大床房", 1.0, 2, 10),
                ("twin", "高级双床房", 1.18, 2, 8),
                ("family", "家庭房", 1.48, 4, 4),
            ):
                await connection.execute(
                    """
                    INSERT INTO travel_room_inventory
                        (room_id, hotel_id, room_type, nightly_rate, capacity,
                         available_rooms, cancellation_policy)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (hotel_id, room_type) DO NOTHING
                    """,
                    (
                        f"{hotel_id}-{suffix}",
                        hotel_id,
                        room_type,
                        round(base_rate * multiplier, 2),
                        capacity,
                        rooms,
                        "入住前一天 18:00 前可免费取消；此后收取首晚房费。",
                    ),
                )
