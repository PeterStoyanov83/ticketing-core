"""
Refund orchestration for Stripe Connect destination charges.

Two production bugs shaped this design:

1. Silent rollback. Seat bookkeeping ran *before* the payment API call inside
   one transaction. Any bookkeeping error rolled back everything, the refund
   API was never called, and the admin only saw a generic error. The order
   stayed "paid" while the buyer waited for money that never moved.
   -> Money moves first. Side effects run afterwards and can never undo it.

2. Platform-funded refunds. With destination charges the ticket money is
   transferred to the organiser's connected account. Refunding without
   reversing that transfer takes the money from the *platform* balance
   while the organiser keeps the ticket revenue.
   -> Connect orders refund with reverse_transfer=True. The platform's
      service fee is non-refundable and is deliberately not returned.

The payment gateway and storage are injected as protocols, so the whole flow
is testable without Stripe or a database.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Awaitable, Callable, Protocol

log = logging.getLogger(__name__)


class OrderStatus(str, Enum):
    PAID = "paid"
    REFUND_PENDING = "refund_pending"  # manual methods (cash, bank) await confirmation
    REFUNDED = "refunded"


@dataclass
class Order:
    id: str
    status: OrderStatus
    total: Decimal                  # ticket amount, the refundable part
    service_fee: Decimal            # platform fee, never refunded
    provider: str                   # "stripe", "cash", ...
    payment_reference: str | None   # PaymentIntent id for card orders
    organiser_on_connect: bool      # was this a destination charge?
    seat_ids: list[str] = field(default_factory=list)
    refund_reason: str | None = None
    refunded_by: str | None = None
    refunded_at: datetime | None = None


class RefundError(Exception):
    """Raised when a refund cannot be performed; the order is left unchanged."""


class PaymentGateway(Protocol):
    async def refund(
        self,
        payment_reference: str,
        amount_cents: int,
        *,
        idempotency_key: str,
        reverse_transfer: bool,
    ) -> None: ...


class OrderStore(Protocol):
    async def save(self, order: Order) -> None: ...


SideEffect = Callable[[Order], Awaitable[None]]


def to_cents(amount: Decimal) -> int:
    return int((amount * 100).to_integral_value())


class RefundOrchestrator:
    def __init__(
        self,
        gateway: PaymentGateway,
        store: OrderStore,
        side_effects: list[SideEffect] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.gateway = gateway
        self.store = store
        self.side_effects = side_effects or []
        self.clock = clock

    async def refund(self, order: Order, *, reason: str, admin: str) -> Order:
        if order.status is not OrderStatus.PAID:
            raise RefundError(f"only paid orders can be refunded (status: {order.status.value})")

        # 1. Money first. If the gateway refuses, nothing changes and the
        #    caller gets an explicit error it can show and retry.
        if order.provider == "stripe" and order.payment_reference:
            try:
                await self.gateway.refund(
                    order.payment_reference,
                    to_cents(order.total),  # ticket amount only; fee is kept
                    idempotency_key=f"refund-{order.id}",  # safe to retry
                    reverse_transfer=order.organiser_on_connect,
                )
            except Exception as exc:
                raise RefundError("payment provider rejected the refund; order is still paid") from exc
            order.status = OrderStatus.REFUNDED
        else:
            order.status = OrderStatus.REFUND_PENDING

        # 2. Persist the outcome before anything optional runs.
        order.refund_reason = reason
        order.refunded_by = admin
        order.refunded_at = self.clock()
        await self.store.save(order)

        # 3. Best effort: release seats, notify the buyer, wake the waitlist.
        #    A failure here is logged and never reverses the refund.
        for effect in self.side_effects:
            try:
                await effect(order)
            except Exception:
                log.warning("refund side effect %s failed for order %s; refund stands",
                            getattr(effect, "__name__", effect), order.id, exc_info=True)
        return order

    async def confirm_manual(self, order: Order) -> Order:
        """Confirm a cash/bank refund that was paid out by hand."""
        if order.status is not OrderStatus.REFUND_PENDING:
            raise RefundError(f"order is not awaiting manual refund (status: {order.status.value})")
        order.status = OrderStatus.REFUNDED
        await self.store.save(order)
        return order
