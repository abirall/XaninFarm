"""Review endpoints.

Listing shows approved reviews only - pending and rejected ones are never
exposed. Creating one goes through ``create_review``, which verifies the
purchase and decides the moderation status; the client cannot set either.
"""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.products.models import Product
from apps.reviews.models import Review
from apps.reviews.serializers import ReviewCreateSerializer, ReviewSerializer
from apps.reviews.services import ReviewError, can_user_review, create_review, has_reviewed


def _visible_product(slug: str) -> Product:
    """The product, but only if the storefront would show it."""
    return get_object_or_404(Product.objects.storefront(), slug=slug)


class ProductReviewListAPIView(generics.ListAPIView):
    serializer_class = ReviewSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        product = _visible_product(self.kwargs["slug"])
        return (
            Review.objects.approved()
            .filter(product=product)
            .select_related("user")
            .order_by("-created_at")
        )


class ProductReviewCreateAPIView(APIView):
    """POST a review, and GET whether the caller is allowed to leave one."""

    permission_classes = [IsAuthenticated]

    def get(self, request, slug: str):
        product = _visible_product(slug)
        return Response(
            {
                "can_review": can_user_review(request.user, product),
                "has_reviewed": has_reviewed(request.user, product),
            }
        )

    def post(self, request, slug: str):
        product = _visible_product(slug)

        serializer = ReviewCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            review = create_review(
                user=request.user,
                product=product,
                rating=serializer.validated_data["rating"],
                body=serializer.validated_data["body"],
                title=serializer.validated_data.get("title", ""),
            )
        except ReviewError as exc:
            # Already reviewed, or never delivered this product.
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                **ReviewSerializer(review, context={"request": request}).data,
                # Says so plainly rather than letting the client wonder why a
                # just-created review is missing from the list.
                "is_published": review.is_published,
            },
            status=status.HTTP_201_CREATED,
        )
