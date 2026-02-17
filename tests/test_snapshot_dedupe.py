from polymarket_arb.orderbook_snapshot import _parse_levels


def test_snapshot_dedupes_price_levels_keep_max_size():
    levels = [
        {"price": 0.4, "size": 1.0},
        {"price": 0.4, "size": 3.0},
        {"price": 0.5, "size": 2.0},
    ]
    out = _parse_levels(levels, "bids")
    assert out == [{"price": 0.5, "size": 2.0}, {"price": 0.4, "size": 3.0}]


def test_snapshot_drops_oversized_levels():
    levels = [
        {"price": 0.4, "size": 1.0},
        {"price": 0.5, "size": 10_000.0},
    ]
    out = _parse_levels(levels, "asks", max_level_size=100.0)
    assert out == [{"price": 0.4, "size": 1.0}]
