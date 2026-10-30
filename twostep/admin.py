from django.contrib import admin

from .models import TwoStep


@admin.register(TwoStep)
class TwoStepAdmin(admin.ModelAdmin):
    """Who has two-step login on. Deleting a row is how a staff login that lost its phone sets it up again
    (the owner's own: manage.py reset_two_step on the server)."""

    list_display = ("user", "is_on", "confirmed_at", "codes_left")
    readonly_fields = ("user", "confirmed_at", "codes_left")
    fields = readonly_fields

    @admin.display(boolean=True, description="on")
    def is_on(self, obj):
        return obj.is_on

    @admin.display(description="backup codes left")
    def codes_left(self, obj):
        return len(obj.backup_codes)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser
