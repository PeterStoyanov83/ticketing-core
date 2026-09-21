from decimal import Decimal as D

import pytest

from ticketing_core.pricing import (
    EarlyBird, FeePolicy, GroupDiscount, PromoCode, VatRegime, price_order,
)

FEE = FeePolicy(percent=D("5"), fixed=D("0.30"), minimum=D("0.50"))


def test_single_ticket_fee_is_percent_plus_fixed():
    r = price_order([D("10.00")], fee=FEE)
    assert r.service_fee == D("0.80")   # 0.50 + 0.30
    assert r.amount_due == D("10.80")
    assert r.total == D("10.00")        # organiser receives the full price


def test_minimum_fee_protects_cheap_tickets():
    r = price_order([D("2.00")], fee=FEE)
    assert r.service_fee == D("0.50")   # 0.10 + 0.30 = 0.40 -> floored


def test_free_order_has_no_fee():
    r = price_order([D("0.00")], fee=FEE)
    assert r.service_fee == D("0") and r.amount_due == D("0.00")


def test_discounts_stack_in_order():
    r = price_order(
        [D("20.00"), D("20.00"), D("10.00"), D("10.00")],
        fee=FEE,
        early_bird=EarlyBird(percent=D("10"), max_tickets=10, sold=8),  # 2 slots left
        group_rules=[GroupDiscount(3, D("5")), GroupDiscount(4, D("10"))],
        promo=PromoCode("spring", D("5.00"), is_percent=False),
    )
    # early bird on the two most expensive seats: 10% of 40 = 4.00 -> 56.00
    # best group rule (4+ tickets) 10%: 5.60 -> 50.40
    # fixed promo 5.00 -> 45.40
    assert [d.amount for d in r.discounts] == [D("4.00"), D("5.60"), D("5.00")]
    assert r.total == D("45.40")
    assert r.discount_total == D("14.60")
    assert r.service_fee == D("2.57")    # 2.27 + 0.30


def test_fixed_promo_never_goes_below_zero():
    r = price_order([D("3.00")], fee=FEE, promo=PromoCode("x", D("50"), is_percent=False))
    assert r.total == D("0.00") and r.service_fee == D("0")


def test_vat_is_extracted_from_inclusive_prices():
    r = price_order([D("12.00")], fee=FEE, vat=VatRegime.STANDARD_20)
    assert r.ticket_vat == D("2.00")     # 12 * 20/120


def test_exhausted_early_bird_is_ignored():
    r = price_order([D("10.00")], fee=FEE, early_bird=EarlyBird(D("50"), 5, sold=5))
    assert r.discounts == ()


def test_negative_price_rejected():
    with pytest.raises(ValueError):
        price_order([D("-1")], fee=FEE)
