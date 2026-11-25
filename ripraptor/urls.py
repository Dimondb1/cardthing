from django.contrib import admin
from django.urls import include, path, re_path

from twostep import views as twostep_views
from twostep.forms import LoginForm
from web import add_shops as web_add_shops
from web import amazon_admin as web_amazon
from web import botcheck as web_botcheck
from web import click_timeline as web_click_timeline
from web import delivery as web_delivery
from web import ebay_health as web_ebay_health
from web import quality as web_quality
from web import merge_box as web_merge_box
from web import email_admin as web_email
from web import media as web_media
from web import online as web_online
from web import people_ask as web_people_ask
from web import rip_wants as web_rip_wants
from web import shop_report as web_shop_report
from web import shop_stock as web_shop_stock
from web import story_deals as web_story_deals
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
    path("admin/clicks/", web_click_timeline.click_timeline_page, name="click_timeline"),
    path("admin/rip-wants/", web_rip_wants.rip_wants_page, name="rip_wants"),
    path("admin/people-ask/", web_people_ask.people_ask_page, name="people_ask"),
    path("admin/story-deals/", web_story_deals.story_deals_page, name="story_deals"),
    path("admin/story-deals/<slug:slug>/story.png", web_story_deals.story_deal_image, name="story_deal_image"),
    path("admin/ebay/", web_ebay_health.ebay_health_page, name="ebay_health"),
    path("admin/merge/", web_merge_box.merge_page, name="merge_products"),
    path("admin/quality/", web_quality.quality_page, name="catalogue_quality"),
    path("admin/add-shops/", web_add_shops.add_shops_page, name="add_shops"),
    path("admin/shop-delivery/", web_delivery.delivery_page, name="shop_delivery"),
    path("admin/shop-stock/", web_shop_stock.shops_page, name="shop_stock"),
    path("admin/shop-stock/<slug:slug>/", web_shop_stock.shop_page, name="shop_stock_shop"),
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
