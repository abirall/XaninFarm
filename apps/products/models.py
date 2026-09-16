"""Catalogue models.

Pricing lives on ProductVariant and is the single source of truth: the
storefront never accepts a price from the client, it re-reads it here.
Physical stock lives in apps.inventory, keyed by variant.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Max, Min, Q
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import SEOModel, TimeStampedModel
from apps.core.utils import MONEY_FIELD, QUANTITY_FIELD, unique_slugify
from apps.core.validators import validate_image_file


# ---------------------------------------------------------------------------
# Category
# ---------------------------------------------------------------------------
class CategoryQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def top_level(self):
        return self.filter(parent__isnull=True)

    def top_level_active(self):
        return self.active().top_level().order_by("sort_order", "name")

    def nav_menu(self):
        """Categories for the header navigation, with their children preloaded."""
        return (
            self.active()
            .top_level()
            .filter(show_in_nav=True)
            .prefetch_related(
                models.Prefetch(
                    "children",
                    queryset=Category.objects.active().order_by("sort_order", "name"),
                )
            )
            .order_by("sort_order", "name")
        )


class Category(TimeStampedModel, SEOModel):
    """A produce category, optionally nested one level (e.g. Dairy > Milk)."""

    name = models.CharField(_("name"), max_length=120)
    slug = models.SlugField(_("slug"), max_length=140, unique=True, blank=True)
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
        verbose_name=_("parent category"),
    )
    description = models.TextField(_("description"), blank=True, max_length=600)
    image = models.ImageField(
        _("image"),
        upload_to="categories/",
        blank=True,
        null=True,
        validators=[validate_image_file],
    )
    image_alt = models.CharField(_("image alt text"), max_length=160, blank=True)

    is_active = models.BooleanField(_("active"), default=True, db_index=True)
    show_in_nav = models.BooleanField(_("show in navigation"), default=True)
    is_featured = models.BooleanField(_("featured on homepage"), default=False)
    sort_order = models.PositiveIntegerField(_("sort order"), default=0)

    objects = CategoryQuerySet.as_manager()

    class Meta:
        ordering = ("sort_order", "name")
        verbose_name = _("category")
        verbose_name_plural = _("categories")
        indexes = [models.Index(fields=["is_active", "sort_order"])]

    def __str__(self) -> str:
        return self.full_name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slugify(self, self.name)
        super().save(*args, **kwargs)

    def clean(self):
        # One level of nesting only, and never its own ancestor.
        if self.parent_id:
            if self.pk and self.parent_id == self.pk:
                raise ValidationError({"parent": _("A category cannot be its own parent.")})
            if self.parent.parent_id:
                raise ValidationError(
                    {"parent": _("Categories can only be nested one level deep.")}
                )

    def get_absolute_url(self) -> str:
        return reverse("products:category", kwargs={"slug": self.slug})

    @property
    def full_name(self) -> str:
        return f"{self.parent.name} / {self.name}" if self.parent_id else self.name

    def descendant_ids(self) -> list[int]:
        """This category plus its children - used to filter the shop listing."""
        return [self.pk, *self.children.values_list("pk", flat=True)]


# ---------------------------------------------------------------------------
# Product
# ---------------------------------------------------------------------------
class ProductQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def published(self):
        return self.filter(published_at__isnull=False, published_at__lte=timezone.now())

    def storefront(self):
        """Everything a shopper is allowed to see, ready to render in a grid."""
        return (
            self.active()
            .published()
            .filter(category__is_active=True)
            .select_related("category")
            .prefetch_related("images", "variants")
            .distinct()
        )

    def with_price_range(self):
        """Annotate min/max active variant price for listing cards."""
        active_variant = Q(variants__is_active=True)
        return self.annotate(
            min_price=Min("variants__price", filter=active_variant),
            max_price=Max("variants__price", filter=active_variant),
            max_compare_price=Max("variants__compare_at_price", filter=active_variant),
        )

    def search(self, term: str):
        if not term:
            return self
        return self.filter(
            Q(name__icontains=term)
            | Q(short_description__icontains=term)
            | Q(description__icontains=term)
            | Q(category__name__icontains=term)
            | Q(variants__sku__iexact=term)
        ).distinct()

    def in_category(self, category: Category):
        return self.filter(category_id__in=category.descendant_ids())


class Product(TimeStampedModel, SEOModel):
    """A catalogue item. Sold through one or more variants."""

    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="products",
        verbose_name=_("category"),
    )
    name = models.CharField(_("name"), max_length=180)
    slug = models.SlugField(_("slug"), max_length=200, unique=True, blank=True)

    short_description = models.CharField(
        _("short description"),
        max_length=300,
        blank=True,
        help_text=_("One line shown on product cards."),
    )
    description = models.TextField(_("description"), blank=True)
    highlights = models.TextField(
        _("highlights"),
        blank=True,
        help_text=_("One selling point per line, e.g. 'Grass-fed, no antibiotics'."),
    )
    storage_instructions = models.CharField(
        _("storage instructions"),
        max_length=220,
        blank=True,
        help_text=_("Shown on perishable items, e.g. 'Keep refrigerated at 2-4 C'."),
    )
    farm_note = models.CharField(
        _("farm note"),
        max_length=220,
        blank=True,
        help_text=_("Provenance line, e.g. 'From our Savar dairy unit'."),
    )

    # --- Perishability drives the inventory rules ---
    is_perishable = models.BooleanField(
        _("perishable"),
        default=True,
        help_text=_("Perishable products require an expiry date on every stock batch."),
    )
    default_shelf_life_days = models.PositiveIntegerField(
        _("default shelf life (days)"),
        default=7,
        help_text=_("Used to pre-fill the expiry date when receiving new stock."),
    )

    is_active = models.BooleanField(_("active"), default=True, db_index=True)
    is_featured = models.BooleanField(_("featured"), default=False, db_index=True)
    published_at = models.DateTimeField(
        _("published at"),
        null=True,
        blank=True,
        default=timezone.now,
        help_text=_("Leave empty to keep the product out of the storefront."),
    )

    # Denormalised review aggregates, refreshed by apps.reviews.
    rating_average = models.DecimalField(
        _("average rating"), max_digits=3, decimal_places=2, default=Decimal("0.00")
    )
    rating_count = models.PositiveIntegerField(_("review count"), default=0)

    objects = ProductQuerySet.as_manager()

    class Meta:
        ordering = ("-is_featured", "-published_at", "name")
        verbose_name = _("product")
        verbose_name_plural = _("products")
        indexes = [
            models.Index(fields=["is_active", "published_at"]),
            models.Index(fields=["category", "is_active"]),
            models.Index(fields=["-rating_average"]),
        ]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slugify(self, self.name)
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse("products:detail", kwargs={"slug": self.slug})

    # --- Presentation helpers -------------------------------------------------
    @property
    def is_published(self) -> bool:
        return bool(self.is_active and self.published_at and self.published_at <= timezone.now())

    @property
    def primary_image(self):
        """The hero image. Uses prefetched rows when available to avoid N+1."""
        if "images" in getattr(self, "_prefetched_objects_cache", {}):
            images = sorted(
                self.images.all(), key=lambda img: (not img.is_primary, img.sort_order, img.id)
            )
            return images[0] if images else None
        return self.images.order_by("-is_primary", "sort_order", "id").first()

    @property
    def highlight_list(self) -> list[str]:
        return [line.strip() for line in self.highlights.splitlines() if line.strip()]

    @property
    def active_variants(self):
        if "variants" in getattr(self, "_prefetched_objects_cache", {}):
            return sorted(
                (v for v in self.variants.all() if v.is_active),
                key=lambda v: (v.sort_order, v.price),
            )
        return list(self.variants.filter(is_active=True).order_by("sort_order", "price"))

    @property
    def default_variant(self):
        variants = self.active_variants
        return variants[0] if variants else None

    @property
    def price_from(self) -> Decimal | None:
        variants = self.active_variants
        return min((v.price for v in variants), default=None)

    @property
    def price_to(self) -> Decimal | None:
        variants = self.active_variants
        return max((v.price for v in variants), default=None)

    @property
    def has_price_range(self) -> bool:
        return self.price_from is not None and self.price_from != self.price_to

    @property
    def best_compare_at_price(self) -> Decimal | None:
        """Highest strike-through price among active variants, when discounted."""
        candidates = [
            v.compare_at_price
            for v in self.active_variants
            if v.compare_at_price and v.compare_at_price > v.price
        ]
        return max(candidates) if candidates else None

    @property
    def is_in_stock(self) -> bool:
        return any(variant.is_in_stock for variant in self.active_variants)


class ProductImage(TimeStampedModel):
    """Gallery image. Exactly one image per product should be primary."""

    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="images", verbose_name=_("product")
    )
    image = models.ImageField(
        _("image"), upload_to="products/%Y/%m/", validators=[validate_image_file]
    )
    alt_text = models.CharField(
        _("alt text"),
        max_length=160,
        help_text=_("Describe the image for screen readers and search engines."),
    )
    is_primary = models.BooleanField(_("primary image"), default=False)
    sort_order = models.PositiveIntegerField(_("sort order"), default=0)

    class Meta:
        ordering = ("-is_primary", "sort_order", "id")
        verbose_name = _("product image")
        verbose_name_plural = _("product images")
        constraints = [
            models.UniqueConstraint(
                fields=["product"],
                condition=Q(is_primary=True),
                name="unique_primary_image_per_product",
            )
        ]

    def __str__(self) -> str:
        return f"{self.product.name} image #{self.pk}"

    def save(self, *args, **kwargs):
        # First image uploaded becomes the primary one.
        if not self.pk and not type(self).objects.filter(product=self.product).exists():
            self.is_primary = True
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# Variant - the sellable unit
# ---------------------------------------------------------------------------
class ProductVariantQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True, product__is_active=True)

    def sellable(self):
        """Active variants whose product is visible in the storefront."""
        return self.active().filter(
            product__published_at__isnull=False,
            product__published_at__lte=timezone.now(),
            product__category__is_active=True,
        )

    def on_offer(self):
        return self.filter(compare_at_price__isnull=False, compare_at_price__gt=F("price"))


class ProductVariant(TimeStampedModel):
    """A specific pack of a product: '1 Litre', '500g', 'Whole bird'.

    This is what carts, orders and stock batches point at.
    """

    class Unit(models.TextChoices):
        LITRE = "litre", _("Litre")
        MILLILITRE = "ml", _("Millilitre")
        KILOGRAM = "kg", _("Kilogram")
        GRAM = "g", _("Gram")
        PIECE = "pc", _("Piece")
        DOZEN = "dozen", _("Dozen")
        PACK = "pack", _("Pack")

    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="variants", verbose_name=_("product")
    )
    name = models.CharField(
        _("variant name"), max_length=120, help_text=_("Shown to shoppers, e.g. '1 Litre bottle'.")
    )
    sku = models.CharField(
        _("SKU"),
        max_length=40,
        unique=True,
        help_text=_("Stock keeping unit. Used on batches, orders and packing slips."),
    )

    pack_size = models.DecimalField(
        _("pack size"),
        default=Decimal("1.000"),
        validators=[MinValueValidator(Decimal("0.001"))],
        **QUANTITY_FIELD,
    )
    unit = models.CharField(_("unit"), max_length=10, choices=Unit.choices, default=Unit.PIECE)

    # --- Money. Always Decimal, never float, never client-supplied. ---
    price = models.DecimalField(
        _("price"), validators=[MinValueValidator(Decimal("0.01"))], **MONEY_FIELD
    )
    compare_at_price = models.DecimalField(
        _("compare-at price"),
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text=_("Original price, shown struck through. Leave empty when not discounted."),
        **MONEY_FIELD,
    )
    cost_price = models.DecimalField(
        _("cost price"),
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text=_("Internal only. Powers the margin column in sales analytics."),
        **MONEY_FIELD,
    )

    weight_grams = models.PositiveIntegerField(
        _("weight (grams)"),
        default=0,
        help_text=_("Shipping weight. 0 when delivery is not weight-priced."),
    )

    is_active = models.BooleanField(_("active"), default=True, db_index=True)
    sort_order = models.PositiveIntegerField(_("sort order"), default=0)

    objects = ProductVariantQuerySet.as_manager()

    class Meta:
        ordering = ("sort_order", "price")
        verbose_name = _("product variant")
        verbose_name_plural = _("product variants")
        constraints = [
            models.UniqueConstraint(fields=["product", "name"], name="unique_variant_name"),
            models.CheckConstraint(
                condition=Q(price__gt=Decimal("0.00")), name="variant_price_positive"
            ),
            models.CheckConstraint(
                condition=Q(compare_at_price__isnull=True)
                | Q(compare_at_price__gte=F("price")),
                name="compare_price_not_below_price",
            ),
        ]
        indexes = [models.Index(fields=["is_active", "price"])]

    def __str__(self) -> str:
        return f"{self.product.name} - {self.name}"

    def clean(self):
        if self.compare_at_price is not None and self.compare_at_price < self.price:
            raise ValidationError(
                {"compare_at_price": _("Compare-at price cannot be lower than the price.")}
            )

    def save(self, *args, **kwargs):
        self.sku = (self.sku or "").strip().upper()
        super().save(*args, **kwargs)

    # --- Pricing --------------------------------------------------------------
    @property
    def is_on_offer(self) -> bool:
        return bool(self.compare_at_price and self.compare_at_price > self.price)

    @property
    def discount_percent(self) -> int:
        if not self.is_on_offer:
            return 0
        saved = self.compare_at_price - self.price
        return int(round(saved / self.compare_at_price * 100))

    @property
    def display_unit(self) -> str:
        """'1 Litre', '500 Gram', '2 Piece' - the pack size in words."""
        size = self.pack_size.normalize()
        size_text = f"{size:f}".rstrip("0").rstrip(".") if "." in f"{size:f}" else f"{size:f}"
        return f"{size_text} {self.get_unit_display()}"

    # --- Stock (delegates to apps.inventory) ---------------------------------
    @property
    def available_stock(self) -> Decimal:
        from apps.inventory.services import available_quantity

        return available_quantity(self)

    @property
    def is_in_stock(self) -> bool:
        return self.available_stock > 0

    @property
    def is_low_stock(self) -> bool:
        from django.conf import settings

        stock = self.available_stock
        return 0 < stock <= settings.LOW_STOCK_THRESHOLD

    @property
    def stock_status(self) -> str:
        """One of: in_stock, low_stock, out_of_stock."""
        stock = self.available_stock
        if stock <= 0:
            return "out_of_stock"
        from django.conf import settings

        if stock <= settings.LOW_STOCK_THRESHOLD:
            return "low_stock"
        return "in_stock"
