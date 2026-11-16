import asyncio

from app.sources.hl_client import WeightBucket, base_weight, extra_weight


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_documented_weights():
    assert base_weight("clearinghouseState") == 2
    assert base_weight("portfolio") == 20
    assert base_weight("userRole") == 60
    assert extra_weight("userFillsByTime", [{}] * 2000) == 100
    assert extra_weight("candleSnapshot", [{}] * 5000) == 83
    assert extra_weight("portfolio", [{}] * 100) == 0


def test_bucket_refills_at_configured_rate():
    clock = FakeClock()
    b = WeightBucket(600, clock=clock)
    assert b.wait_time(600) == 0
    asyncio.run(b.acquire(600))
    assert b.wait_time(20) == 2.0  # 600/min = 10 per second
    clock.t = 2.0
    assert b.wait_time(20) == 0


def test_post_hoc_charge_delays_next_request():
    clock = FakeClock()
    b = WeightBucket(600, clock=clock)
    asyncio.run(b.acquire(20))
    b.charge(680)  # large response surcharge drives the bucket negative
    assert b.wait_time(20) > 10
