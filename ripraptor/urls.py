from django.contrib import admin
from django.urls import include, path, re_path

from twostep import views as twostep_views
from twostep.forms import LoginForm
from web import add_shops as web_add_shops
from web import amazon_admin as web_amazon
from web import botcheck as web_botcheck
from web import delivery as web_delivery
from web import email_admin as web_email
from web import media as web_media
from web import online as web_online
from web import shop_report as web_shop_report
from web import visibility as web_visibility
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
    path("admin/claude/", web_views.claude_page, name="claude"),
    path("admin/crawl/", web_views.crawl_page, name="crawl"),
    path("admin/online/", web_online.online_page, name="online"),
    path("admin/add-shops/", web_add_shops.add_shops_page, name="add_shops"),
    path("admin/shop-delivery/", web_delivery.delivery_page, name="shop_delivery"),
    path("admin/amazon/", web_amazon.amazon_page, name="amazon_keys"),
    path("admin/email/", web_email.email_page, name="email_sending"),
    path("admin/visibility/", web_visibility.visibility_page, name="visibility"),
    path("admin/shop-report/", web_shop_report.report_page, name="shop_report"),
    path("admin/shop-report/download/", web_shop_report.report_download, name="shop_report_download"),
    path("admin/data/", web_views.data_page, name="data"),
    path("admin/data/download/<int:pk>/", web_views.data_download, name="data_download"),
    path("admin/data/upload/<int:pk>/", web_views.data_import_page, name="data_import"),
    path("admin/two-step/", twostep_views.setup, name="twostep_setup"),
    path("admin/manifest.webmanifest", web_views.admin_manifest, name="admin_manifest"),
    path("admin/", admin.site.urls),
    # Uploaded pictures (web/media.py): served here because Caddy hands every request to Django.
    re_path(r"^media/(?P<path>.+)$", web_media.serve_media, name="media"),
    # The visitor check's answer (web/botcheck.py): never checked itself.
    path("check/", web_botcheck.answer, name="botcheck"),
    path("", include("web.urls")),
]
