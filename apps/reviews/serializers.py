"""Review serializers.

The writable surface is rating, title and body. Everything that decides trust -
whether the purchase is verified, whether the review is published - is derived
server-side in ``apps.reviews.services.create_review`` and is read-only here.
"""

from __future__ import annotations

from rest_framework import serializers

from apps.reviews.models import RATING_CHOICES, Review


class ReviewSerializer(serializers.ModelSerializer):
    author = serializers.CharField(source="author_name", read_only=True)

    class Meta:
        model = Review
        fields = [
            "id",
            "rating",
            "title",
            "body",
            "author",
            "is_verified_purchase",
            "staff_reply",
            "replied_at",
            "created_at",
        ]
        read_only_fields = fields


class ReviewCreateSerializer(serializers.Serializer):
    """Leave a review. Status and verification are not client-settable."""

    rating = serializers.ChoiceField(choices=RATING_CHOICES)
    title = serializers.CharField(max_length=120, required=False, allow_blank=True, default="")
    body = serializers.CharField(max_length=2000)

    def validate_body(self, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 10:
            raise serializers.ValidationError(
                "Tell us a little more - at least 10 characters."
            )
        return cleaned
