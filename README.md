# ticketing-core

The money logic of a Stripe Connect ticketing platform, extracted as a small,
dependency-free Python package: order pricing, fee economics and refunds.

It comes from [НаЖиво.бг](https://najivo.bg), a ticketing platform I built
for Bulgarian cultural venues (FastAPI, PostgreSQL, Redis, React, Stripe Connect).
The full platform is private. This repository isolates the parts where
mistakes cost real money, and shows how I test them.

```
src/ticketing_core/
  pricing.py     stacked discounts, VAT extraction, buyer service fee
  economics.py   net margin per order and break-even volume per organiser
  refunds.py     refund orchestration for destination charges
tests/           19 tests, no network, no database
```

## Quick start

```bash
pip install -e ".[dev]"
pytest
```

```python
from decimal import Decimal
from ticketing_core import price_order, FeePolicy, GroupDiscount

r = price_order(
    [Decimal("12.00")] * 4,
    fee=FeePolicy(percent=Decimal("5"), fixed=Decimal("0.30"), minimum=Decimal("0.50")),
    group_rules=[GroupDiscount(min_tickets=4, percent=Decimal("10"))],
)
r.total        # Decimal('43.20')  -> organiser
r.service_fee  # Decimal('2.46')   -> platform
r.amount_due   # Decimal('45.66')  -> buyer
```

## Design decisions

### 1. The fee is `max(percent × total + fixed, minimum)`, not a plain percentage

With destination charges the platform pays card processing on the *whole*
charge but keeps only the service fee. Card processing has a fixed
per-transaction cost, so a pure percentage fee loses money on cheap tickets.

| €10 ticket, foreign card (3.25% + €0.25) | fee   | processing | platform net |
|------------------------------------------|-------|------------|--------------|
| 5%, €0.50 minimum                        | €0.50 | €0.59      | **−€0.09**   |
| 5% + €0.30, €0.50 minimum                | €0.80 | €0.60      | **+€0.20**   |

`economics.py` turns this into a break-even number: with a €4/month cost per
connected organiser account, an organiser selling €10 tickets must sell about
20 orders a month before they stop costing the platform money. The processor
rates here are illustrative; plug in your own.

### 2. Refunds move money first, and nothing afterwards can undo it

An early version released seats *before* calling the refund API, inside the
same transaction. When seat bookkeeping failed, everything rolled back, the
refund API was never called, and the admin saw only a generic error. The order
stayed "paid" and no money moved.

`RefundOrchestrator` now:

1. calls the payment provider first, with an idempotency key so retries are safe;
2. if the provider refuses, raises `RefundError` and leaves the order untouched;
3. persists the refunded state;
4. runs side effects (release seats, email the buyer, notify the waitlist)
   as best effort: each failure is logged and never reverses the refund.

### 3. Connect refunds reverse the transfer, and the platform fee is kept

With destination charges the ticket money has already been transferred to
the organiser. A refund without `reverse_transfer=True` is paid from the
**platform's** balance while the organiser keeps the ticket revenue. We found
this during end-to-end testing, when two test refunds drove the platform
balance to zero.

Connect orders now refund the ticket amount with `reverse_transfer=True`.
The service fee is non-refundable (disclosed at checkout), so it is not returned.

### 4. Decimal everywhere, half-up rounding, integer cents at the boundary

No floats touch money. Amounts are rounded to cents with `ROUND_HALF_UP`,
the rounding buyers and accountants expect, and converted to integer cents
only when calling the payment API.

## Discount stacking order

1. **Early bird**: % off the first N remaining slots, most expensive seats first
2. **Group**: % off the running total, highest threshold the order qualifies for
3. **Promo code**: % or fixed amount, never below zero
4. **VAT**: extracted from VAT-inclusive prices (`total × 20/120`) or exempt
5. **Service fee**: on the discounted total, charged on top

## What is not here

Seat locking (Redis), webhooks, fiscal receipts, the admin UI and the Connect
onboarding flow live in the private platform. The payment gateway and storage
are injected as protocols here, so the orchestration is testable without Stripe
or a database.

## License

MIT
