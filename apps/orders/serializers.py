"""Order serializers.

Orders are read-only over the API except for placement, and placement accepts
only *choices* - which address, how to pay, which slot. Every amount is
computed by ``place_order`` from the server-side cart.

Order lines echo the frozen snapshot columns, never a live product lookup, so
an order always reports what was actually bought at the price it was sold for.
"""

from __future__ import annotations

from rest_framework import serializers

from apps.orders.models import Order, OrderEvent, OrderItem


class OrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = [
            "id",
            "product_name",
            "variant_name",
            "sku",
            "quantity",
            "unit_price",
            "line_total",
        ]
        read_only_fields = fields


class OrderEventSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = OrderEvent
        fields = ["id", "status", "status_display", "note", "created_at"]
        read_only_fields = fields


class OrderListSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    payment_status_display = serializers.CharField(
        source="get_payment_status_display", read_only=True
    )
    payment_method_display = serializers.CharField(
        source="get_payment_method_display", read_only=True
    )
    item_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Order
        fields = [
            "number",
            "status",
            "status_display",
            # Payment status is a separate field on purpose: a cash order can be
            # out for delivery while still unpaid. Never collapse the two.
            "payment_status",
            "payment_status_display",
            "payment_method",
            "payment_method_display",
            "item_count",
            "total",
            "placed_at",
        ]
        read_only_fields = fields


class OrderDetailSerializer(OrderListSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    events = serializers.SerializerMethodField()
    shipping_address = serializers.ListField(
        source="shipping_address_lines", read_only=True, child=serializers.CharField()
    )
    delivery_zone = serializers.CharField(source="delivery_zone.name", read_only=True, default=None)
    delivery_slot = serializers.CharField(source="delivery_slot.label", read_only=True, default=None)
    balance_due = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    total_savings = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    is_cancellable = serializers.BooleanField(
        source="is_cancellable_by_customer", read_only=True
    )
    tracking_url = serializers.CharField(source="get_tracking_url", read_only=True)

    class Meta(OrderListSerializer.Meta):
        fields = [
            *OrderListSerializer.Meta.fields,
            "subtotal",
            "discount_amount",
            "delivery_fee",
            "delivery_discount",
            "amount_paid",
            "amount_refunded",
            "balance_due",
            "total_savings",
            "coupon_code",
            "contact_email",
            "contact_phone",
            "shipping_address",
            "delivery_zone",
            "delivery_slot",
            "requested_delivery_date",
            "customer_note",
            "tracking_note",
            "is_cancellable",
            "tracking_url",
            "items",
            "events",
            "confirmed_at",
            "delivered_at",
            "cancelled_at",
        ]
        read_only_fields = fields

    def get_events(self, order) -> list[dict]:
        # Staff-only entries stay out of the customer's view.
        visible = [event for event in order.events.all() if event.is_customer_visible]
        return OrderEventSerializer(visible, many=True, context=self.context).data


# ---------------------------------------------------------------------------
# The only writable order surface
# ---------------------------------------------------------------------------
class PlaceOrderSerializer(serializers.Serializer):
    """Turn the caller's own cart into an order.

    Note what is absent: no items, no prices, no totals, no payment status.
    The cart is read from the session on the server and priced there.
    """

    address = serializers.IntegerField()
    payment_method = serializers.CharField(max_length=20)
    delivery_slot = serializers.IntegerField(required=False, allow_null=True)
    requested_delivery_date = serializers.DateField(required=False, allow_null=True)
    customer_note = serializers.CharField(
        max_length=500, required=False, allow_blank=True, default=""
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def validate_address(self, value: int):
        """Resolve the address, scoped to the requesting customer.

        Ownership is checked here rather than trusted, so passing another
        customer's address id is a validation error, not a delivery to them.
        """
        address = self.user.addresses.filter(pk=value).first()
        if address is None:
            raise serializers.ValidationError("That delivery address is not available.")
        return address

    def validate_payment_method(self, value: str) -> str:
        from apps.orders.forms import enabled_payment_methods

        allowed = {choice for choice, _label in enabled_payment_methods()}
        if value not in allowed:
            raise serializers.ValidationError("That payment method is not available.")
        return value

    def validate_delivery_slot(self, value):
        if value is None:
            return None
        from apps.delivery.models import DeliverySlot

        slot = DeliverySlot.objects.filter(pk=value, is_active=True).first()
        if slot is None:
            raise serializers.ValidationError("That delivery slot is not available.")
        return slot


class CancelOrderSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
