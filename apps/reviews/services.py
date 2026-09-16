"""Review rules.

A review may only be written by someone who actually received the goods, and
the denormalised rating columns on ``Product`` are rebuilt from the approved
reviews here - never incremented by hand, so they cannot drift.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.db import transaction
from django.db.models import Avg, Count, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.orders.models import OrderItem, OrderStatus
from apps.products.models import Product
from apps.reviews.models import Review, ReviewStatus

logger = logging.getLogger(__name__)


class ReviewError(Exception):
    """A review could not be accepted. The message is safe to show."""

    def __init__(self, message: str):
        self.message = str(message)
        super().__init__(self.message)


def purchased_items(user, product):
    """Delivered lines this customer has for this product, newest first."""
    if not getattr(user, "is_authenticated", False):
        return OrderItem.objects.none()
    return (
        OrderItem.objects.filter(
            order__user=user,
            order__status=OrderStatus.DELIVERED,
            variant__product=product,
        )
        .select_related("order")
        .order_by("-order__delivered_at")
    )


def has_reviewed(user, product) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    return Review.objects.filter(user=user, product=product).exists()


def can_user_review(user, product) -> bool:
    """Delivered it, and hasn't said their piece yet."""
    return purchased_items(user, product).exists() and not has_reviewed(user, product)


@transaction.atomic
def create_review(*, user, product, rating: int, body: str, title: str = "") -> Review:
    """Record a review, verifying the purchase server-side.

    The ``is_verified_purchase`` flag is derived here rather than accepted
    from the form, and the same check decides whether the review is allowed
    at all.
    """
    if has_reviewed(user, product):
        raise ReviewError(_("You have already reviewed this product."))

    item = purchased_items(user, product).first()
    if item is None:
        raise ReviewError(
            _("You can review a product once it has been delivered to you.")
        )

    review = Review.objects.create(
        product=product,
        user=user,
        order_item=item,
        rating=int(rating),
        title=title.strip()[:120],
        body=body.strip(),
        is_verified_purchase=True,
        status=(
            ReviewStatus.APPROVED
            if getattr(settings, "REVIEW_AUTO_APPROVE", False)
            else ReviewStatus.PENDING
        ),
    )
    if review.is_published:
        refresh_product_rating(product)
    return review


@transaction.atomic
def moderate_review(review: Review, status: str, *, by=None) -> Review:
    """Publish or reject a review and rebuild the product's rating."""
    if status not in ReviewStatus.values:
        raise ReviewError(_("That is not a review status."))

    review.status = status
    review.save(update_fields=["status", "updated_at"])
    refresh_product_rating(review.product)
    logger.info("Review %s set to %s by %s", review.pk, status, getattr(by, "pk", None))
    return review


@transaction.atomic
def reply_to_review(review: Review, *, reply: str, by=None) -> Review:
    review.staff_reply = reply.strip()[:1000]
    review.replied_at = timezone.now() if review.staff_reply else None
    review.replied_by = by if review.staff_reply else None
    review.save(update_fields=["staff_reply", "replied_at", "replied_by", "updated_at"])
    return review


def refresh_product_rating(product: Product) -> None:
    """Rebuild a single product's rating columns from its approved reviews."""
    aggregate = Review.objects.approved().filter(product=product).aggregate(
        average=Avg("rating"), count=Count("id")
    )
    Product.objects.filter(pk=product.pk).update(
        rating_average=round(aggregate["average"] or 0, 2),
        rating_count=aggregate["count"] or 0,
    )


def refresh_all_product_ratings() -> int:
    """Repair every product's rating columns. Used after a bulk moderation."""
    aggregates = (
        Product.objects.annotate(
            live_average=Avg("reviews__rating", filter=Q(reviews__status=ReviewStatus.APPROVED)),
            live_count=Count("reviews", filter=Q(reviews__status=ReviewStatus.APPROVED)),
        )
        .values("pk", "live_average", "live_count")
    )

    updated = []
    for row in aggregates:
        updated.append(
            Product(
                pk=row["pk"],
                rating_average=round(row["live_average"] or 0, 2),
                rating_count=row["live_count"] or 0,
            )
        )
    if updated:
        Product.objects.bulk_update(updated, ["rating_average", "rating_count"], batch_size=500)
    return len(updated)
