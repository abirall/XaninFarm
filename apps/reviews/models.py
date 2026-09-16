"""Customer reviews.

Two rules shape this module:

* A review is tied to an **order item**, not just a product, so "verified
  purchase" is a fact we can prove rather than a badge we hand out.
* Nothing a customer writes reaches the storefront until it has a status of
  ``approved``, and the default for that is set by the site settings.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel

RATING_CHOICES = [
    (5, _("Excellent")),
    (4, _("Very good")),
    (3, _("Average")),
    (2, _("Poor")),
    (1, _("Terrible")),
]


class ReviewStatus(models.TextChoices):
    PENDING = "pending", _("Awaiting moderation")
    APPROVED = "approved", _("Published")
    REJECTED = "rejected", _("Rejected")


class ReviewQuerySet(models.QuerySet):
    def approved(self):
        return self.filter(status=ReviewStatus.APPROVED)

    def pending(self):
        return self.filter(status=ReviewStatus.PENDING)

    def for_product(self, product):
        return self.filter(product=product)

    def rating_breakdown(self, product) -> list[dict]:
        """Star-by-star counts for a product, highest first.

        Returned with percentages already worked out so the template does no
        arithmetic.
        """
        counts = {
            row["rating"]: row["total"]
            for row in self.approved()
            .filter(product=product)
            .values("rating")
            .annotate(total=models.Count("id"))
        }
        total = sum(counts.values())
        return [
            {
                "rating": star,
                "count": counts.get(star, 0),
                "percent": round(counts.get(star, 0) * 100 / total) if total else 0,
            }
            for star in (5, 4, 3, 2, 1)
        ]

    def summary(self, product) -> tuple[Decimal, int]:
        """(average, count) over approved reviews - the source of truth for
        the denormalised columns on ``Product``."""
        aggregate = self.approved().filter(product=product).aggregate(
            average=models.Avg("rating"), count=models.Count("id")
        )
        average = aggregate["average"] or 0
        return Decimal(str(round(average, 2))), aggregate["count"]


class Review(TimeStampedModel):
    product = models.ForeignKey(
        "products.Product",
        on_delete=models.CASCADE,
        related_name="reviews",
        verbose_name=_("product"),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reviews",
        verbose_name=_("customer"),
    )
    order_item = models.ForeignKey(
        "orders.OrderItem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviews",
        verbose_name=_("purchased line"),
        help_text=_("The delivered line this review is about."),
    )

    rating = models.PositiveSmallIntegerField(_("rating"), choices=RATING_CHOICES)
    title = models.CharField(_("headline"), max_length=120, blank=True)
    body = models.TextField(_("review"), max_length=2000)

    status = models.CharField(
        _("status"),
        max_length=10,
        choices=ReviewStatus.choices,
        default=ReviewStatus.PENDING,
        db_index=True,
    )
    is_verified_purchase = models.BooleanField(_("verified purchase"), default=False)

    staff_reply = models.TextField(_("reply from the farm"), max_length=1000, blank=True)
    replied_at = models.DateTimeField(_("replied at"), null=True, blank=True)
    replied_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="review_replies",
        verbose_name=_("replied by"),
    )

    objects = ReviewQuerySet.as_manager()

    class Meta:
        ordering = ("-created_at",)
        verbose_name = _("review")
        verbose_name_plural = _("reviews")
        constraints = [
            models.UniqueConstraint(
                fields=["product", "user"], name="one_review_per_product_per_customer"
            ),
            models.CheckConstraint(
                condition=models.Q(rating__gte=1) & models.Q(rating__lte=5),
                name="review_rating_between_1_and_5",
            ),
        ]
        indexes = [
            models.Index(fields=["product", "status", "-created_at"]),
            models.Index(fields=["status", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.rating}★ {self.product} by {self.user}"

    @property
    def is_published(self) -> bool:
        return self.status == ReviewStatus.APPROVED

    @property
    def author_name(self) -> str:
        """First name plus an initial - never the full name or the email."""
        first = (self.user.first_name or "").strip()
        last = (self.user.last_name or "").strip()
        if first and last:
            return f"{first} {last[0]}."
        return first or _("Verified customer")
