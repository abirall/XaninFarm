"""Customer-facing review views.

Posting a review is a POST-only, CSRF-protected action, and the eligibility
check is repeated here rather than trusted from the page that rendered the
form - a hidden field saying "verified buyer" would be worth nothing.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from apps.core.mixins import htmx_trigger
from apps.products.models import Product
from apps.reviews.forms import ReviewForm
from apps.reviews.models import Review
from apps.reviews.services import (
    ReviewError,
    can_user_review,
    create_review,
    has_reviewed,
    refresh_product_rating,
)

PAGE_SIZE = 10


def _published_product(slug: str) -> Product:
    return get_object_or_404(Product.objects.storefront(), slug=slug)


def _review_context(request, product: Product, *, form: ReviewForm | None = None, page=1) -> dict:
    reviews = (
        Review.objects.approved()
        .filter(product=product)
        .select_related("user")
        .order_by("-created_at")
    )
    paginator = Paginator(reviews, PAGE_SIZE)
    page_obj = paginator.get_page(page)
    return {
        "product": product,
        "reviews": page_obj.object_list,
        "page_obj": page_obj,
        "review_count": paginator.count,
        "rating_breakdown": Review.objects.rating_breakdown(product),
        "form": form if form is not None else ReviewForm(),
        "can_review": can_user_review(request.user, product),
        "has_reviewed": has_reviewed(request.user, product),
    }


@require_GET
def review_list(request, slug: str):
    """Paginated reviews, loaded into the product page by HTMX."""
    product = _published_product(slug)
    context = _review_context(request, product, page=request.GET.get("page", 1))
    return render(request, "reviews/partials/_review_list.html", context)


@login_required
@require_POST
def review_create(request, slug: str):
    """Accept a review from a verified buyer."""
    product = _published_product(slug)
    form = ReviewForm(request.POST)

    if form.is_valid():
        try:
            review = create_review(
                user=request.user,
                product=product,
                rating=form.cleaned_data["rating"],
                title=form.cleaned_data.get("title", ""),
                body=form.cleaned_data["body"],
            )
        except ReviewError as exc:
            form.add_error(None, exc.message)
        else:
            note = (
                _("Thank you - your review is live.")
                if review.is_published
                else _("Thank you! Your review will appear once we have read it.")
            )
            if request.htmx:
                context = _review_context(request, product)
                context["flash"] = note
                response = render(request, "reviews/partials/_review_panel.html", context)
                return htmx_trigger(response, "reviews:changed")
            messages.success(request, note)
            return redirect(product.get_absolute_url())

    if request.htmx:
        context = _review_context(request, product, form=form)
        return render(request, "reviews/partials/_review_panel.html", context)

    messages.error(request, _("Please check your review and try again."))
    return redirect(product.get_absolute_url())


@login_required
@require_POST
def review_delete(request, pk: int):
    """Customers may withdraw their own review, and only their own."""
    review = get_object_or_404(Review, pk=pk, user=request.user)
    product = review.product
    review.delete()
    refresh_product_rating(product)

    if request.htmx:
        response = render(
            request, "reviews/partials/_review_panel.html", _review_context(request, product)
        )
        return htmx_trigger(response, "reviews:changed")

    messages.success(request, _("Your review has been removed."))
    return redirect(product.get_absolute_url())
