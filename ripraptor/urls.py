from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "RipRaptor admin"
admin.site.site_title = "RipRaptor admin"
admin.site.index_title = "Products, prices and site wording"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("web.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
