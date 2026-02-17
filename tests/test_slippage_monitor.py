from polymarket_arb.slippage_monitor import SlippageMonitor


def test_adaptive_buffer_increases_with_higher_slippage():
    mon = SlippageMonitor(multiplier=1.5, window_size=50, baseline_buffer=0.002)
    for i in range(20):
        cid = f"c{i}"
        mon.record_expected(cid, "m1", 0.5)
        mon.record_fill(cid, 0.501)
    base = mon.adaptive_buffer("m1")
    for i in range(20, 40):
        cid = f"c{i}"
        mon.record_expected(cid, "m1", 0.5)
        mon.record_fill(cid, 0.52)
    bumped = mon.adaptive_buffer("m1")
    assert bumped > base


def test_adaptive_buffer_never_below_baseline():
    mon = SlippageMonitor(multiplier=1.5, window_size=10, baseline_buffer=0.003)
    for i in range(5):
        cid = f"z{i}"
        mon.record_expected(cid, "m1", 0.5)
        mon.record_fill(cid, 0.5001)
    assert mon.adaptive_buffer("m1") >= 0.003
