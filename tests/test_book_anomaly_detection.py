from polymarket_arb.ws_market import is_book_anomalous


def test_crossed_book_anomaly():
    bids = [{"price": 0.6, "size": 1.0}]
    asks = [{"price": 0.6, "size": 1.0}]
    assert is_book_anomalous(bids, asks, market_active=True)


def test_negative_size_anomaly():
    bids = [{"price": 0.5, "size": -1.0}]
    asks = [{"price": 0.6, "size": 1.0}]
    assert is_book_anomalous(bids, asks, market_active=True)


def test_empty_active_anomaly():
    assert is_book_anomalous([], [], market_active=True)


def test_empty_inactive_not_anomaly():
    assert not is_book_anomalous([], [], market_active=False)
