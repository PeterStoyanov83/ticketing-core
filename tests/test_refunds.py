from datetime import datetime, timezone
from decimal import Decimal as D

import pytest

from ticketing_core.refunds import Order, OrderStatus, RefundError, RefundOrchestrator

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


class FakeGateway:
    def __init__(self, fail: bool = False):
        self.calls: list[dict] = []
        self.fail = fail

    async def refund(self, ref, cents, *, idempotency_key, reverse_transfer):
        if self.fail:
            raise RuntimeError("card network down")
        self.calls.append(dict(ref=ref, cents=cents, key=idempotency_key, reverse=reverse_transfer))


class FakeStore:
    def __init__(self):
        self.saved: list[OrderStatus] = []

    async def save(self, order):
        self.saved.append(order.status)


def card_order(**kw) -> Order:
    base = dict(id="o1", status=OrderStatus.PAID, total=D("10.00"), service_fee=D("0.80"),
                provider="stripe", payment_reference="pi_123", organiser_on_connect=True,
                seat_ids=["A1"])
    base.update(kw)
    return Order(**base)


def orchestrator(gateway=None, store=None, effects=None):
    return RefundOrchestrator(gateway or FakeGateway(), store or FakeStore(), effects, clock=lambda: NOW)


async def test_connect_refund_reverses_transfer_and_keeps_fee():
    gw = FakeGateway()
    order = await orchestrator(gw).refund(card_order(), reason="cancelled", admin="peter")
    assert gw.calls == [dict(ref="pi_123", cents=1000, key="refund-o1", reverse=True)]
    assert order.status is OrderStatus.REFUNDED and order.refunded_at == NOW


async def test_non_connect_charge_does_not_reverse():
    gw = FakeGateway()
    await orchestrator(gw).refund(card_order(organiser_on_connect=False), reason="r", admin="a")
    assert gw.calls[0]["reverse"] is False


async def test_gateway_failure_leaves_order_untouched():
    store = FakeStore()
    order = card_order()
    with pytest.raises(RefundError):
        await orchestrator(FakeGateway(fail=True), store).refund(order, reason="r", admin="a")
    assert order.status is OrderStatus.PAID and store.saved == []


async def test_side_effect_failure_never_undoes_refund():
    ran = []

    async def release_seats(order):
        raise RuntimeError("redis blip")

    async def email_buyer(order):
        ran.append("email")

    store = FakeStore()
    order = await orchestrator(store=store, effects=[release_seats, email_buyer]).refund(
        card_order(), reason="r", admin="a")
    assert order.status is OrderStatus.REFUNDED
    assert store.saved == [OrderStatus.REFUNDED]
    assert ran == ["email"]  # later effects still run


async def test_cash_order_goes_to_manual_queue():
    gw = FakeGateway()
    orch = orchestrator(gw)
    order = await orch.refund(card_order(provider="cash", payment_reference=None), reason="r", admin="a")
    assert gw.calls == [] and order.status is OrderStatus.REFUND_PENDING
    assert (await orch.confirm_manual(order)).status is OrderStatus.REFUNDED


async def test_only_paid_orders_can_be_refunded():
    with pytest.raises(RefundError):
        await orchestrator().refund(card_order(status=OrderStatus.REFUNDED), reason="r", admin="a")
