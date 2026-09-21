"""
Order pricing: stacked discounts, VAT extraction and the buyer service fee.

Everything is pure (no DB, no I/O) and uses Decimal end to end, so every
result is reproducible and unit-testable to the cent.

Calculation order
-----------------
1. subtotal            sum of seat prices
2. early bird          % off the first N remaining slots, most expensive seats first
3. group discount      % off the running total (best threshold wins)
4. promo code          % or fixed amount off the running total (never below zero)
5. ticket VAT          extracted from VAT-inclusive prices, or exempt
6. buyer service fee   max(percent * total + fixed, floor), charged on top

The organiser always receives `total`. The platform keeps `service_fee`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum

CENT = Decimal("0.01")
ZERO = Decimal("0")
HUNDRED = Decimal("100")


def money(value: Decimal | int | str) -> Decimal:
    """Round to cents, half-up (the way people and accountants expect)."""
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


class VatRegime(str, Enum):
    EXEMPT = "exempt"            # e.g. cultural events exempt under local VAT law
    STANDARD_20 = "standard_20"  # prices are entered VAT-inclusive at 20%


@dataclass(frozen=True)
class EarlyBird:
    percent: Decimal
    max_tickets: int
    sold: int = 0
    label: str = "Early bird"

    @property
    def remaining(self) -> int:
        return max(self.max_tickets - self.sold, 0)


@dataclass(frozen=True)
class GroupDiscount:
    min_tickets: int
    percent: Decimal
    label: str = "Group discount"


@dataclass(frozen=True)
class PromoCode:
    code: str
    value: Decimal
    is_percent: bool = True


@dataclass(frozen=True)
class FeePolicy:
    """Buyer service fee: max(percent * total + fixed, minimum).

    The fixed part exists because card processors charge a fixed amount per
    transaction. A pure percentage loses money on cheap tickets; see README.
    """

    percent: Decimal = Decimal("5.0")
    fixed: Decimal = Decimal("0.30")
    minimum: Decimal = Decimal("0.50")
    vat_percent: Decimal = ZERO  # VAT on the platform's own fee, if registered

    def fee_for(self, total: Decimal) -> Decimal:
        if total <= ZERO or self.percent <= ZERO:
            return ZERO  # free orders, comps and cash sales carry no fee
        fee = money(total * self.percent / HUNDRED + self.fixed)
        return max(fee, money(self.minimum))


@dataclass(frozen=True)
class AppliedDiscount:
    kind: str
    label: str
    amount: Decimal


@dataclass(frozen=True)
class PriceBreakdown:
    subtotal: Decimal
    discounts: tuple[AppliedDiscount, ...]
    total: Decimal               # what the organiser receives
    ticket_vat: Decimal          # VAT contained in `total`
    service_fee: Decimal         # what the platform keeps
    service_fee_vat: Decimal
    amount_due: Decimal          # what the buyer pays
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def discount_total(self) -> Decimal:
        return money(sum((d.amount for d in self.discounts), ZERO))


def best_group_discount(
    rules: list[GroupDiscount], ticket_count: int
) -> GroupDiscount | None:
    """Highest threshold the order qualifies for."""
    eligible = [r for r in rules if r.min_tickets <= ticket_count]
    return max(eligible, key=lambda r: r.min_tickets, default=None)


def price_order(
    seat_prices: list[Decimal],
    *,
    fee: FeePolicy = FeePolicy(),
    early_bird: EarlyBird | None = None,
    group_rules: list[GroupDiscount] | None = None,
    promo: PromoCode | None = None,
    vat: VatRegime = VatRegime.EXEMPT,
) -> PriceBreakdown:
    """Price one order. `seat_prices` are already resolved per ticket type."""
    if any(p < ZERO for p in seat_prices):
        raise ValueError("seat prices cannot be negative")

    subtotal = money(sum(seat_prices, ZERO))
    running = subtotal
    applied: list[AppliedDiscount] = []

    # 2. Early bird: discount the most expensive eligible seats first,
    #    so the buyer gets the largest benefit from the limited slots.
    if early_bird and early_bird.remaining > 0 and seat_prices:
        eligible = sorted(seat_prices, reverse=True)[: early_bird.remaining]
        amount = money(sum(eligible, ZERO) * early_bird.percent / HUNDRED)
        running = money(running - amount)
        applied.append(AppliedDiscount("early_bird", early_bird.label, amount))

    # 3. Group discount on what is left after early bird.
    group = best_group_discount(group_rules or [], len(seat_prices))
    if group:
        amount = money(running * group.percent / HUNDRED)
        running = money(running - amount)
        applied.append(AppliedDiscount("group", group.label, amount))

    # 4. Promo code last; a fixed promo can never push the total below zero.
    if promo:
        if promo.is_percent:
            amount = money(running * promo.value / HUNDRED)
        else:
            amount = min(money(promo.value), running)
        running = money(running - amount)
        applied.append(AppliedDiscount("promo", f"Code {promo.code.upper()}", amount))

    total = running

    # 5. VAT is *contained* in inclusive prices: VAT = total * 20 / 120.
    ticket_vat = (
        money(total * Decimal("20") / Decimal("120"))
        if vat is VatRegime.STANDARD_20
        else ZERO
    )

    # 6. Service fee on the discounted total, charged on top.
    service_fee = fee.fee_for(total)
    service_fee_vat = money(service_fee * fee.vat_percent / HUNDRED)

    return PriceBreakdown(
        subtotal=subtotal,
        discounts=tuple(applied),
        total=total,
        ticket_vat=ticket_vat,
        service_fee=service_fee,
        service_fee_vat=service_fee_vat,
        amount_due=money(total + service_fee + service_fee_vat),
    )
