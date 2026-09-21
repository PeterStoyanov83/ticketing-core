from decimal import Decimal as D

from ticketing_core.economics import ProcessorCosts, breakeven_orders_per_month, net_per_order
from ticketing_core.pricing import FeePolicy

# Illustrative processor profiles, not quoted prices.
DOMESTIC_CARD = ProcessorCosts(percent=D("1.5"), fixed=D("0.25"), per_account_monthly=D("4.00"))
FOREIGN_CARD = ProcessorCosts(percent=D("3.25"), fixed=D("0.25"), per_account_monthly=D("4.00"))

PURE_PERCENT = FeePolicy(percent=D("5"), fixed=D("0"), minimum=D("0.50"))
PERCENT_PLUS_FIXED = FeePolicy(percent=D("5"), fixed=D("0.30"), minimum=D("0.50"))


def test_pure_percentage_loses_money_on_foreign_cards():
    # fee 0.50, processing 10.50 * 3.25% + 0.25 = 0.59
    assert net_per_order(D("10.00"), PURE_PERCENT, FOREIGN_CARD) == D("-0.09")


def test_fixed_component_restores_margin_in_the_worst_case():
    # fee 0.80, processing 10.80 * 3.25% + 0.25 = 0.60
    assert net_per_order(D("10.00"), PERCENT_PLUS_FIXED, FOREIGN_CARD) == D("0.20")


def test_domestic_cards_leave_more_margin():
    assert net_per_order(D("10.00"), PERCENT_PLUS_FIXED, DOMESTIC_CARD) == D("0.39")


def test_breakeven_volume_per_organiser():
    # A connected account costs 4.00/month; at 0.20 net per order that is 20 orders.
    assert breakeven_orders_per_month(D("10.00"), PERCENT_PLUS_FIXED, FOREIGN_CARD) == 20
    assert breakeven_orders_per_month(D("10.00"), PERCENT_PLUS_FIXED, DOMESTIC_CARD) == 11


def test_no_breakeven_when_every_order_loses():
    assert breakeven_orders_per_month(D("10.00"), PURE_PERCENT, FOREIGN_CARD) is None
