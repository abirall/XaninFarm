"""Abstract base models plus editable site-wide and homepage content."""

from __future__ import annotations

from decimal import Decimal

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from apps.core.utils import MONEY_FIELD, unique_slugify
from apps.core.validators import validate_bd_phone, validate_image_file


# ---------------------------------------------------------------------------
# Abstract bases
# ---------------------------------------------------------------------------
class TimeStampedModel(models.Model):
    """Adds created/updated audit columns to any model."""

    created_at = models.DateTimeField(_("created at"), auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)

    class Meta:
        abstract = True


class SEOModel(models.Model):
    """Optional per-page search metadata."""

    seo_title = models.CharField(_("SEO title"), max_length=70, blank=True)
    seo_description = models.CharField(_("SEO description"), max_length=160, blank=True)

    class Meta:
        abstract = True


class ActiveOrderedModel(models.Model):
    """Content that editors can hide and re-order."""

    is_active = models.BooleanField(_("active"), default=True, db_index=True)
    sort_order = models.PositiveIntegerField(_("sort order"), default=0)

    class Meta:
        abstract = True
        ordering = ("sort_order", "id")


class ActiveQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)


# ---------------------------------------------------------------------------
# Site settings (singleton)
# ---------------------------------------------------------------------------
class SiteSetting(TimeStampedModel):
    """Editable global configuration. Exactly one row, always pk=1.

    Commercial rules such as the delivery fee live here rather than in settings
    so the farm team can change them without a deployment.
    """

    CACHE_KEY = "core:site_settings"
    SINGLETON_PK = 1

    brand_name = models.CharField(_("brand name"), max_length=80, default="XaninFarm")
    tagline = models.CharField(
        _("tagline"),
        max_length=160,
        default="Fresh From Our Farm, Delivered to Your Door.",
    )
    about_short = models.TextField(
        _("short about text"),
        blank=True,
        help_text=_("One paragraph shown in the footer and about strip."),
    )

    # --- Announcement bar ---
    announcement_text = models.CharField(_("announcement text"), max_length=180, blank=True)
    announcement_url = models.CharField(_("announcement link"), max_length=300, blank=True)
    announcement_active = models.BooleanField(_("show announcement"), default=False)

    # --- Contact ---
    support_phone = models.CharField(
        _("support phone"), max_length=20, blank=True, validators=[validate_bd_phone]
    )
    whatsapp_number = models.CharField(
        _("WhatsApp number"), max_length=20, blank=True, validators=[validate_bd_phone]
    )
    support_email = models.EmailField(_("support email"), blank=True)
    farm_address = models.CharField(_("farm address"), max_length=255, blank=True)
    opening_hours = models.CharField(
        _("opening hours"), max_length=120, blank=True, default="Sat-Thu, 8:00 AM - 8:00 PM"
    )

    # --- Social ---
    facebook_url = models.URLField(_("Facebook URL"), blank=True)
    instagram_url = models.URLField(_("Instagram URL"), blank=True)
    youtube_url = models.URLField(_("YouTube URL"), blank=True)

    # --- Commerce rules ---
    default_delivery_fee = models.DecimalField(
        _("default delivery fee"),
        default=Decimal("60.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        **MONEY_FIELD,
    )
    free_delivery_threshold = models.DecimalField(
        _("free delivery threshold"),
        default=Decimal("1500.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text=_("Orders at or above this subtotal ship free. Set 0 to disable."),
        **MONEY_FIELD,
    )
    min_order_amount = models.DecimalField(
        _("minimum order amount"),
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
        **MONEY_FIELD,
    )
    cod_enabled = models.BooleanField(_("cash on delivery enabled"), default=True)

    class Meta:
        verbose_name = _("site setting")
        verbose_name_plural = _("site settings")

    def __str__(self) -> str:
        return self.brand_name

    def save(self, *args, **kwargs):
        # Force the singleton row, then drop the cached copy.
        self.pk = self.SINGLETON_PK
        super().save(*args, **kwargs)
        cache.delete(self.CACHE_KEY)

    def delete(self, *args, **kwargs):  # pragma: no cover - guard rail
        raise ValidationError(_("Site settings cannot be deleted."))

    @classmethod
    def get_solo(cls) -> SiteSetting:
        """Return the singleton, creating and caching it on first use."""
        cached = cache.get(cls.CACHE_KEY)
        if cached is not None:
            return cached
        obj, _created = cls.objects.get_or_create(pk=cls.SINGLETON_PK)
        cache.set(cls.CACHE_KEY, obj, 300)
        return obj

    @property
    def offers_free_delivery(self) -> bool:
        return self.free_delivery_threshold > Decimal("0.00")


# ---------------------------------------------------------------------------
# Homepage content
# ---------------------------------------------------------------------------
class HeroSlide(TimeStampedModel, ActiveOrderedModel):
    """Full-bleed hero panels at the top of the homepage."""

    eyebrow = models.CharField(_("eyebrow"), max_length=60, blank=True)
    title = models.CharField(_("title"), max_length=120)
    subtitle = models.TextField(_("subtitle"), blank=True, max_length=400)
    image = models.ImageField(
        _("image"),
        upload_to="homepage/hero/%Y/%m/",
        validators=[validate_image_file],
        help_text=_("Large farm photography. 2400x1400px or wider works best."),
    )
    image_alt = models.CharField(
        _("image alt text"),
        max_length=160,
        help_text=_("Describe the photo for screen readers."),
    )
    cta_label = models.CharField(_("button label"), max_length=40, blank=True)
    cta_url = models.CharField(_("button link"), max_length=300, blank=True)
    secondary_cta_label = models.CharField(_("secondary button label"), max_length=40, blank=True)
    secondary_cta_url = models.CharField(_("secondary button link"), max_length=300, blank=True)

    objects = ActiveQuerySet.as_manager()

    class Meta(ActiveOrderedModel.Meta):
        verbose_name = _("hero slide")
        verbose_name_plural = _("hero slides")

    def __str__(self) -> str:
        return self.title


class ValueProposition(TimeStampedModel, ActiveOrderedModel):
    """The "why buy from the farm" strip."""

    class Icon(models.TextChoices):
        LEAF = "leaf", _("Leaf")
        TRUCK = "truck", _("Delivery truck")
        SHIELD = "shield", _("Shield")
        CLOCK = "clock", _("Clock")
        HEART = "heart", _("Heart")
        COW = "cow", _("Cow")

    icon = models.CharField(_("icon"), max_length=20, choices=Icon.choices, default=Icon.LEAF)
    title = models.CharField(_("title"), max_length=80)
    description = models.CharField(_("description"), max_length=220)

    objects = ActiveQuerySet.as_manager()

    class Meta(ActiveOrderedModel.Meta):
        verbose_name = _("value proposition")
        verbose_name_plural = _("value propositions")

    def __str__(self) -> str:
        return self.title


class Testimonial(TimeStampedModel, ActiveOrderedModel):
    """Customer quotes."""

    author_name = models.CharField(_("author name"), max_length=80)
    author_location = models.CharField(_("author location"), max_length=80, blank=True)
    quote = models.TextField(_("quote"), max_length=400)
    rating = models.PositiveSmallIntegerField(
        _("rating"),
        default=5,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    avatar = models.ImageField(
        _("avatar"),
        upload_to="homepage/testimonials/",
        blank=True,
        null=True,
        validators=[validate_image_file],
    )

    objects = ActiveQuerySet.as_manager()

    class Meta(ActiveOrderedModel.Meta):
        verbose_name = _("testimonial")
        verbose_name_plural = _("testimonials")

    def __str__(self) -> str:
        return f"{self.author_name} ({self.rating}/5)"


class PromoBanner(TimeStampedModel, ActiveOrderedModel):
    """Scheduled promotional banners placed around the storefront."""

    class Placement(models.TextChoices):
        HOME_MID = "home_mid", _("Homepage - middle")
        HOME_BOTTOM = "home_bottom", _("Homepage - bottom")
        SHOP_TOP = "shop_top", _("Shop - top")

    placement = models.CharField(
        _("placement"), max_length=20, choices=Placement.choices, default=Placement.HOME_MID
    )
    title = models.CharField(_("title"), max_length=120)
    subtitle = models.CharField(_("subtitle"), max_length=220, blank=True)
    image = models.ImageField(
        _("image"),
        upload_to="homepage/banners/%Y/%m/",
        blank=True,
        null=True,
        validators=[validate_image_file],
    )
    image_alt = models.CharField(_("image alt text"), max_length=160, blank=True)
    cta_label = models.CharField(_("button label"), max_length=40, blank=True)
    cta_url = models.CharField(_("button link"), max_length=300, blank=True)
    starts_at = models.DateTimeField(_("starts at"), null=True, blank=True)
    ends_at = models.DateTimeField(_("ends at"), null=True, blank=True)

    objects = ActiveQuerySet.as_manager()

    class Meta(ActiveOrderedModel.Meta):
        verbose_name = _("promo banner")
        verbose_name_plural = _("promo banners")

    def __str__(self) -> str:
        return f"{self.title} ({self.get_placement_display()})"

    def clean(self):
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": _("End time must be after the start time.")})

    def is_live(self, now=None) -> bool:
        from django.utils import timezone

        now = now or timezone.now()
        if not self.is_active:
            return False
        if self.starts_at and now < self.starts_at:
            return False
        return not (self.ends_at and now > self.ends_at)


# ---------------------------------------------------------------------------
# Static pages
# ---------------------------------------------------------------------------
class Page(TimeStampedModel, SEOModel):
    """Editor-managed static content (About, FAQ, Terms, Privacy...)."""

    title = models.CharField(_("title"), max_length=140)
    slug = models.SlugField(_("slug"), max_length=160, unique=True, blank=True)
    summary = models.CharField(_("summary"), max_length=300, blank=True)
    body = models.TextField(
        _("body"),
        help_text=_("Supports basic HTML. Rendered inside a styled prose container."),
    )
    is_published = models.BooleanField(_("published"), default=True, db_index=True)
    show_in_footer = models.BooleanField(_("show in footer"), default=True)
    sort_order = models.PositiveIntegerField(_("sort order"), default=0)

    class Meta:
        ordering = ("sort_order", "title")
        verbose_name = _("page")
        verbose_name_plural = _("pages")

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slugify(self, self.title)
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse("core:page", kwargs={"slug": self.slug})
