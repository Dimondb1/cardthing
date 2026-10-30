from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from twostep import views as twostep_views
from twostep.forms import LoginForm
from web import views as web_views

admin.site.site_header = "RipRaptor admin"
admin.site.site_title = "RipRaptor admin"
admin.site.index_title = "Products, prices and site wording"
admin.site.index_template = "admin/ripraptor_index.html"
# The sign-in takes the authenticator app's code too, and turns an address away after 10 wrong tries.
admin.site.login_form = LoginForm
admin.site.login_template = "twostep/login.html"

urlpatterns = [
    path("admin/insights/", web_views.insights_page, name="insights"),
    path("admin/checks/", web_views.checks_page, name="checks"),
    path("admin/crawl/", web_views.crawl_page, name="crawl"),
    path("admin/data/", web_views.data_page, name="data"),
    path("admin/data/download/<int:pk>/", web_views.data_download, name="data_download"),
    path("admin/data/upload/<int:pk>/", web_views.data_import_page, name="data_import"),
    path("admin/two-step/", twostep_views.setup, name="twostep_setup"),
    path("admin/manifest.webmanifest", web_views.admin_manifest, name="admin_manifest"),
    path("admin/", admin.site.urls),
    path("", include("web.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
