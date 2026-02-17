from polymarket_arb.orderbook_snapshot import _parse_levels


def test_snapshot_sanity_filters_and_sorts_bids():
    levels = [
        {"price": 0.5, "size": 1},
        {"price": 1.2, "size": 1},  # invalid price
        {"price": 0.7, "size": -1},  # invalid size
        [0.6, 2],
    ]
    out = _parse_levels(levels, "bids")
    assert out == [{"price": 0.6, "size": 2.0}, {"price": 0.5, "size": 1.0}]


def test_snapshot_sanity_filters_and_sorts_asks():
    levels = [
        {"price": 0.4, "size": 1},
        {"price": 0.2, "size": 3},
        {"price": -0.1, "size": 1},
    ]
    out = _parse_levels(levels, "asks")
    assert out == [{"price": 0.2, "size": 3.0}, {"price": 0.4, "size": 1.0}]
