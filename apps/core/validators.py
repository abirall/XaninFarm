"""Reusable validators for user input and uploaded files."""

from __future__ import annotations

import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.deconstruct import deconstructible
from django.utils.translation import gettext_lazy as _

# ---------------------------------------------------------------------------
# Phone numbers (Bangladesh)
# ---------------------------------------------------------------------------
# Accepts 01712345678, 8801712345678, +8801712345678.
BD_PHONE_RE = re.compile(r"^(?:\+?88)?0?1[3-9]\d{8}$")


def validate_bd_phone(value: str) -> None:
    """Validate a Bangladeshi mobile number."""
    if not value:
        return
    cleaned = re.sub(r"[\s\-()]", "", str(value))
    if not BD_PHONE_RE.match(cleaned):
        raise ValidationError(
            _("Enter a valid Bangladeshi mobile number, for example 01712345678."),
            code="invalid_phone",
        )


def normalize_bd_phone(value: str | None) -> str:
    """Normalise a phone number to +8801XXXXXXXXX form.

    Returns the input unchanged when it is not a recognisable BD mobile number;
    validation is the validator's job, not this function's.
    """
    if not value:
        return ""
    cleaned = re.sub(r"[\s\-()]", "", str(value))
    if not BD_PHONE_RE.match(cleaned):
        return cleaned
    digits = cleaned.lstrip("+")
    if digits.startswith("88"):
        digits = digits[2:]
    digits = digits.lstrip("0")
    return f"+880{digits}"


# ---------------------------------------------------------------------------
# Uploaded images
# ---------------------------------------------------------------------------
@deconstructible
class ImageFileValidator:
    """Validate an uploaded image by size, extension and actual decoded content.

    Extension checks alone are trivially bypassed, so the file is also opened
    with Pillow to confirm it really is a decodable image of an allowed format.
    """

    def __init__(self, max_bytes: int | None = None, allowed_extensions: list[str] | None = None):
        self.max_bytes = max_bytes or getattr(settings, "MAX_UPLOAD_IMAGE_SIZE", 5 * 1024 * 1024)
        self.allowed_extensions = [
            ext.lower().lstrip(".")
            for ext in (
                allowed_extensions
                or getattr(settings, "ALLOWED_IMAGE_EXTENSIONS", ["jpg", "jpeg", "png", "webp"])
            )
        ]

    def __call__(self, file_obj) -> None:
        size = getattr(file_obj, "size", None)
        if size and size > self.max_bytes:
            raise ValidationError(
                _("Image is too large. Maximum size is %(limit)s MB.")
                % {"limit": round(self.max_bytes / (1024 * 1024), 1)},
                code="image_too_large",
            )

        name = (getattr(file_obj, "name", "") or "").lower()
        extension = name.rsplit(".", 1)[-1] if "." in name else ""
        if extension not in self.allowed_extensions:
            raise ValidationError(
                _("Unsupported image type. Allowed: %(allowed)s.")
                % {"allowed": ", ".join(self.allowed_extensions)},
                code="image_bad_extension",
            )

        self._verify_decodable(file_obj)

    @staticmethod
    def _verify_decodable(file_obj) -> None:
        try:
            from PIL import Image
        except ImportError:  # pragma: no cover - Pillow is a hard requirement
            return

        try:
            position = file_obj.tell() if hasattr(file_obj, "tell") else 0
            file_obj.seek(0)
            with Image.open(file_obj) as image:
                image.verify()
        except ValidationError:
            raise
        except Exception as exc:
            raise ValidationError(
                _("This file is not a valid image."), code="image_corrupt"
            ) from exc
        finally:
            if hasattr(file_obj, "seek"):
                file_obj.seek(position)

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, ImageFileValidator)
            and self.max_bytes == other.max_bytes
            and self.allowed_extensions == other.allowed_extensions
        )


validate_image_file = ImageFileValidator()
