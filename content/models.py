from django.db import models
from django.urls import reverse

from .registry import SECTIONS, Kind


class SiteContent(models.Model):
    """A piece of public site wording, looked up by a stable key.

    Rows are created from ``content/registry.py``. Templates read them through
    the ``{% copy %}`` tag, which falls back to the registry default if a row
    is missing or switched off.
    """

    key = models.CharField(max_length=100, unique=True)
    label = models.CharField(max_length=120)
    section = models.CharField(max_length=20, choices=SECTIONS, db_index=True)
    kind = models.CharField("type", max_length=20, choices=Kind.choices)
    content = models.TextField("wording", blank=True)
    default_content = models.TextField("default wording", blank=True)
    description = models.TextField("where it appears", blank=True)
    placeholders = models.CharField(max_length=200, blank=True)
    is_legal = models.BooleanField(
        "legal or disclosure text",
        default=False,
        help_text="Commission and independence statements. Check changes carefully.",
    )
    active = models.BooleanField(
        "use this wording",
        default=True,
        help_text="Untick to show the default wording instead. Your edit is kept.",
    )
    position = models.PositiveIntegerField(
        default=0, editable=False, help_text="Order on the page, from content/registry.py."
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["position", "key"]
        verbose_name = "site text"
        verbose_name_plural = "site text"

    def __str__(self):
        return self.key

    @property
    def allowed_placeholders(self):
        return tuple(name for name in self.placeholders.split(",") if name)

    @property
    def is_edited(self):
        return self.content != self.default_content

    def page_url(self):
        """A page where this text can be seen, used by "View on site"."""
        if self.section == "home" or self.section in {"site", "footer", "meta"}:
            return reverse("web:home")
        if self.section == "browse":
            if self.key.startswith("browse.games"):
                return reverse("web:games")
            return reverse("web:search")
        if self.section == "about":
            return reverse("web:about")
        if self.section == "product":
            from catalogue.models import Product

            product = Product.objects.filter(is_active=True).order_by("-created_at").first()
            return product.get_absolute_url() if product else None
        return None


class LegalText(SiteContent):
    """Proxy so legal and disclosure wording gets its own admin page."""

    class Meta:
        proxy = True
        verbose_name = "legal and disclosure text"
        verbose_name_plural = "legal and disclosure text"
