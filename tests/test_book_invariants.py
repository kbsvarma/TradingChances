from polymarket_arb.ws_market import is_book_anomalous


def test_equal_best_bid_ask_is_anomaly():
    bids = [{"price": 0.5, "size": 1.0}]
    asks = [{"price": 0.5, "size": 1.0}]
    assert is_book_anomalous(bids, asks, market_active=True)


def test_empty_active_book_is_configurable():
    assert is_book_anomalous([], [], market_active=True, require_nonempty_active=True)
    assert not is_book_anomalous([], [], market_active=True, require_nonempty_active=False)
