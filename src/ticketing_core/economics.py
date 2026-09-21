"""
Unit economics for a destination-charge marketplace.

The platform pays card processing on the *whole* charge (ticket + fee), but
only keeps the fee. This module answers: what does the platform actually net
per order, and how many tickets does an organiser need before they stop
costing the platform money?

Default processor numbers are illustrative placeholders. Check your own
processor's current pricing before relying on them.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .pricing import HUNDRED, FeePolicy, money


@dataclass(frozen=True)
class ProcessorCosts:
    percent: Decimal = Decimal("1.5")         # card processing, % of the charge
    fixed: Decimal = Decimal("0.25")          # card processing, per charge
    per_account_monthly: Decimal = Decimal("4.00")  # per active connected account


def net_per_order(ticket_total: Decimal, fee: FeePolicy, costs: ProcessorCosts) -> Decimal:
    """Platform fee minus processing cost on the full charged amount."""
    service_fee = fee.fee_for(ticket_total)
    charged = ticket_total + service_fee
    processing = money(charged * costs.percent / HUNDRED + costs.fixed)
    return money(service_fee - processing)


def breakeven_orders_per_month(
    ticket_total: Decimal, fee: FeePolicy, costs: ProcessorCosts
) -> int | None:
    """Orders an organiser must sell monthly to cover their account cost.

    Returns None if each order loses money (no volume ever breaks even).
    """
    net = net_per_order(ticket_total, fee, costs)
    if net <= 0:
        return None
    orders, remainder = divmod(costs.per_account_monthly, net)
    return int(orders) + (1 if remainder else 0)
