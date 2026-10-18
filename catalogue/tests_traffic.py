"""The traffic explainer (catalogue/traffic.py): like with like, bursts apart, drivers named only when they matter."""

from datetime import date, datetime, timedelta
from itertools import count
from zoneinfo import ZoneInfo

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from catalogue import insights, traffic
from catalogue.models import DailyPageView, DailyVisitor, OutboundClick, Retailer
from catalogue.testing import make_game, make_product, make_retailer, make_set

LONDON = ZoneInfo("Europe/London")
# Sunday 18 Oct 2026 at 13:13, the moment Ben asked why Sunday was so low.
NOW = datetime(2026, 10, 18, 13, 13, tzinfo=LONDON)
TODAY = NOW.date()
YESTERDAY = TODAY - timedelta(days=1)
DAYTIME = list(range(8, 23))
_tokens = count()


def at(day, hour, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=LONDON)


def add_pages(day, hits):
    """Page counts as By day reads them: each counted page bumps a DailyPageView row as well as the visitor."""
    row, _ = DailyPageView.objects.get_or_create(date=day, kind=DailyPageView.Kind.HOME, key="")
    DailyPageView.objects.filter(pk=row.pk).update(hits=row.hits + hits)


def add_visitors(day, n, *, source="", device="mobile", country="GB", returning=False, views=1, landing="home",
                 hours=None, timed=True, minute=0, pages=True):
    """``n`` visitors on ``day``, arriving spread over ``hours`` (daytime by default), and their pages."""
    hours = hours or DAYTIME
    if pages:
        add_pages(day, n * views)
    DailyVisitor.objects.bulk_create([
        DailyVisitor(
            date=day, token=f"t{next(_tokens)}", source=source, device=device, country=country, returning=returning,
            views=views, landing=landing if timed else "",
            first_seen=at(day, hours[i % len(hours)], minute) if timed else None,
        )
        for i in range(n)
    ])


class Shop:
    product = retailer = None

    @classmethod
    def get(cls):
        if cls.product is None or not Retailer.objects.filter(pk=cls.retailer.pk).exists():
            cls.product = make_product(make_set(make_game()))
            cls.retailer = make_retailer()
        return cls.product, cls.retailer


def add_clicks(day, n, hours=None):
    product, retailer = Shop.get()
    hours = hours or DAYTIME
    OutboundClick.objects.bulk_create([
        OutboundClick(product=product, retailer=retailer, created_at=at(day, hours[i % len(hours)], 30))
        for i in range(n)
    ])


def normal_days(first, last, people=100, clicks=50, **kwargs):
    day = first
    while day <= last:
        add_visitors(day, people, views=2, **kwargs)
        add_clicks(day, clicks)
        day += timedelta(days=1)


def texts(result):
    return [f["text"] for f in result["findings"]] + [h["text"] for h in result["health"]]


def build_bens_table(timed_from=None):
    """Ben's By day table for 6 to 18 Oct 2026, people spread over the day, mostly search and typed visits.

    Page views go into DailyPageView exactly as By day shows them. As on the live site, each visitor's own page
    count (DailyVisitor.views, migration 0022, live 7 Oct 06:06 UTC) is 0 on every row before 7 Oct. Rows
    before ``timed_from`` have no arrival time or landing page, as rows from before this change. The spread of
    arrivals over 08:00 to 22:59 (and 08:00 to 12:59 today) is the fixture's choice, not Ben's data."""
    table = {
        # day: (visitors, returning, views, clicks)
        date(2026, 10, 18): (70, 2, 78, 0),
        date(2026, 10, 17): (640, 31, 1036, 687),
        date(2026, 10, 16): (316, 66, 624, 560),
        date(2026, 10, 15): (338, 78, 530, 651),
        date(2026, 10, 14): (373, 61, 525, 549),
        date(2026, 10, 13): (447, 102, 820, 212),
        date(2026, 10, 12): (577, 40, 751, 420),
        date(2026, 10, 11): (791, 158, 1143, 289),
        date(2026, 10, 10): (408, 22, 560, 301),
        date(2026, 10, 9): (4386, 21, 4584, 67),
        date(2026, 10, 8): (255, 22, 542, 69),
        date(2026, 10, 7): (448, 33, 31295, 82),
        date(2026, 10, 6): (360, 18, 25185, 102),
        date(2026, 10, 2): (3, 0, 5, 0),
        date(2026, 10, 3): (4, 0, 6, 1),
        date(2026, 10, 4): (2, 0, 2, 0),
        date(2026, 10, 5): (6, 0, 9, 0),
    }
    for day, (visitors, returning, views, clicks) in table.items():
        add_pages(day, views)
        timed = timed_from is not None and day >= timed_from
        hours = list(range(0, 14)) if day == TODAY else None
        if day == TODAY:
            hours = list(range(8, 13))
        capped = 0
        if views > visitors * 20:   # the 31k and 25k page days: scripts at the cap hold most of the pages
            capped = (views - visitors * 3) // insights.VIEWS_PER_VISITOR_CAP
        people = visitors - capped
        rest = views - capped * insights.VIEWS_PER_VISITOR_CAP
        search = people * 45 // 100
        typed = people - search
        add_visitors(day, capped, views=insights.VIEWS_PER_VISITOR_CAP, source="", timed=timed, hours=hours,
                     device="desktop", country="US", pages=False)
        # Pages per person spread so the day's total comes out right.
        per = max(1, rest // max(people, 1))
        add_visitors(day, search, views=per, source="google.com", timed=timed, hours=hours, pages=False,
                     landing="product:prismatic-evolutions-elite-trainer-box")
        add_visitors(day, typed - returning, views=per, source="", timed=timed, hours=hours, pages=False)
        add_visitors(day, returning, views=per, source="", returning=True, timed=timed, hours=hours, pages=False)
        add_clicks(day, clicks, hours=hours)
        if day < date(2026, 10, 7):
            DailyVisitor.objects.filter(date=day).update(views=0)


class PartDayTests(TestCase):
    def test_today_so_far_is_compared_with_the_same_hours_last_week_and_yesterday(self):
        last_week = TODAY - timedelta(days=7)
        normal_days(TODAY - timedelta(days=20), YESTERDAY, people=100, clicks=40)
        # Last Sunday: 30 people before 13:13, and 200 more in the evening that must not count.
        DailyVisitor.objects.filter(date=last_week).delete()
        add_visitors(last_week, 30, hours=[9, 10, 11, 12])
        add_visitors(last_week, 200, hours=[18, 19, 20])
        add_visitors(TODAY, 33, hours=[9, 10, 11, 12])
        result = traffic.explain(NOW)
        today = result["today_so_far"]
        self.assertEqual((today["now"], today["then"], today["until"]), (33, 30, "13:13"))
        self.assertEqual([r["key"] for r in today["references"]], ["week", "yesterday"])
        # Yesterday spread 100 people over 08:00 to 22:00; by 13:13 the ones up to 13:00 had arrived.
        self.assertEqual(today["references"][1]["then"], DailyVisitor.objects.filter(
            date=YESTERDAY, first_seen__lte=at(YESTERDAY, 13, 13)).count())
        self.assertIn("Today to 13:13: 33 people, against 30 by the same time last Sunday. That is within normal",
                      " ".join(texts(result)))

    def test_rows_without_an_arrival_time_are_left_out_and_the_history_is_stated(self):
        # Every row from before arrival times were kept, and today's too.
        for day in range(1, 15):
            add_visitors(TODAY - timedelta(days=day), 100, views=2, timed=False)
            add_clicks(TODAY - timedelta(days=day), 40)
        add_visitors(TODAY, 30, timed=False)
        result = traffic.explain(NOW)
        self.assertTrue(result["enough"])
        self.assertEqual(result["today_so_far"]["references"], [])
        self.assertIsNone(result["history"]["timed_since"])
        self.assertTrue(any("Arrival times are recorded from" in t for t in texts(result)))
        # Whole days still compare, and nothing breaks without landing pages.
        self.assertEqual(result["yesterday"]["now"], 100)
        self.assertFalse(result["yesterday"]["drivers"]["landing_recorded"])

    def test_a_site_with_no_visitors_says_there_is_too_little_to_compare(self):
        result = traffic.explain(NOW)
        self.assertFalse(result["enough"])
        self.assertIn("Not enough visitors yet", result["headline"])


class BurstTests(TestCase):
    def test_a_burst_day_is_flagged_and_left_out_of_the_baseline(self):
        normal_days(TODAY - timedelta(days=30), YESTERDAY, people=100, clicks=40)
        scripted = YESTERDAY - timedelta(days=7)    # last Saturday: one hundred visitors opening 90 pages each
        DailyVisitor.objects.filter(date=scripted).update(views=90)
        add_pages(scripted, 100 * 88)
        crowd = YESTERDAY - timedelta(days=3)       # Wednesday: 4,000 one-page visitors and the usual clicks
        add_visitors(crowd, 3900, source="", views=1)
        result = traffic.explain(NOW)
        flagged = {b["date"]: b for b in result["bursts"]}
        self.assertEqual(set(flagged), {scripted, crowd})
        self.assertIn("9,000 page views from 100 visitors", flagged[scripted]["reasons"])
        self.assertIn("4,000 visitors but 40 clicks", flagged[crowd]["reasons"])
        self.assertNotIn(scripted, result["yesterday"]["base_days"])
        self.assertEqual(result["yesterday"]["then"], 100)
        self.assertTrue(any("Left out of the averages as likely automated" in t for t in texts(result)))

    def test_a_few_visitors_holding_most_pages_is_a_burst(self):
        normal_days(TODAY - timedelta(days=20), YESTERDAY, people=100, clicks=40)
        add_visitors(YESTERDAY, 2, views=insights.VIEWS_PER_VISITOR_CAP)
        add_visitors(YESTERDAY, 3, views=120)
        result = traffic.explain(NOW)
        reasons = next(b["reasons"] for b in result["bursts"] if b["date"] == YESTERDAY)
        # Below the cap: 360 of 560 pages. The two at the cap are scripts already, and never counted as people.
        self.assertIn("3 visitors opened 64% of the pages", reasons)
        self.assertEqual(result["yesterday"]["now"], 103)

    def test_one_visitor_holding_most_pages_is_named_in_the_singular(self):
        normal_days(TODAY - timedelta(days=20), YESTERDAY - timedelta(days=1), people=100, clicks=40)
        add_visitors(YESTERDAY, 60, views=1)
        add_clicks(YESTERDAY, 24)
        add_visitors(YESTERDAY, 1, views=140)
        result = traffic.explain(NOW)
        reasons = next(b["reasons"] for b in result["bursts"] if b["date"] == YESTERDAY)
        self.assertIn("1 visitor opened 70% of the pages", reasons)

    def test_a_script_at_the_cap_does_not_make_a_quiet_morning_a_burst(self):
        # The script is left out of people already, so the day's people count is sound.
        build_bens_table()
        add_clicks(TODAY, 60, hours=[8, 9, 10, 11, 12])
        add_visitors(TODAY, 1, views=insights.VIEWS_PER_VISITOR_CAP, hours=[9])
        result = traffic.explain(NOW)
        self.assertNotIn(TODAY, result["burst_dates"])
        self.assertEqual(result["today_so_far"]["people"], 70)
        self.assertFalse(any("looks automated" in t for t in texts(result)))

    def test_page_views_from_before_visitors_kept_their_own_count_still_show_a_burst(self):
        # Before 7 Oct 2026 every visitor row holds 0 pages; only the page counts know about the 9,000.
        normal_days(TODAY - timedelta(days=20), YESTERDAY, people=100, clicks=40)
        scripted = YESTERDAY - timedelta(days=7)
        DailyVisitor.objects.filter(date=scripted).update(views=0)
        add_pages(scripted, 8800)
        result = traffic.explain(NOW)
        burst = next(b for b in result["bursts"] if b["date"] == scripted)
        self.assertIn("9,000 page views from 100 visitors", burst["reasons"])


class PastMomentTests(TestCase):
    def test_a_past_moment_counts_only_the_arrivals_up_to_then(self):
        sunday = date(2026, 10, 11)
        moment = at(sunday, 13, 13)
        normal_days(sunday - timedelta(days=20), sunday, people=100, clicks=40)
        result = traffic.explain(moment)
        today = result["today_so_far"]
        # 100 people spread over 08:00 to 22:00: 42 had arrived by 13:13, as by the same time last Sunday.
        arrived = DailyVisitor.objects.filter(date=sunday, first_seen__lte=moment).count()
        self.assertEqual((today["now"], today["people"]), (arrived, arrived))
        self.assertEqual(today["then"], arrived)
        self.assertFalse(any(h["kind"] == "conversion" for h in result["health"]), result["health"])
        self.assertEqual(result["today_so_far"]["drivers"]["now"], arrived)


class DriverTests(TestCase):
    def setUp(self):
        # Four Saturdays before yesterday with 100 people from search and 100 typed, and the days between.
        normal_days(TODAY - timedelta(days=30), YESTERDAY - timedelta(days=1), people=100, clicks=60, source="google.com")
        normal_days(TODAY - timedelta(days=30), YESTERDAY - timedelta(days=1), people=100, clicks=0, source="")

    def test_a_source_drop_is_named_with_its_share(self):
        add_visitors(YESTERDAY, 30, source="google.com", views=2)
        add_visitors(YESTERDAY, 100, source="", views=2)
        add_clicks(YESTERDAY, 40)
        result = traffic.explain(NOW)
        y = result["yesterday"]
        self.assertEqual((y["now"], y["then"]), (130, 200))
        named = y["drivers"]["named"][0]
        self.assertEqual((named["label"], named["change"], named["share"]), ("Search engines", -70, 100))
        finding = next(f for f in result["findings"] if f["kind"] == "driver" and f["period"] == "yesterday")
        self.assertEqual(finding["text"], "Most of the drop came from Search engines: 100 then, 30 now, 70 fewer, "
                                          "100% of the change.")
        self.assertIn("Google Search Console", finding["check"])
        self.assertEqual(y["drivers"]["sources"][0]["label"], "Search engines")

    def test_a_change_spread_over_every_source_names_no_driver(self):
        add_visitors(YESTERDAY, 60, source="google.com", views=2)
        add_visitors(YESTERDAY, 60, source="", views=2)
        add_clicks(YESTERDAY, 36)
        result = traffic.explain(NOW)
        self.assertEqual(result["yesterday"]["drivers"]["named"], [])
        self.assertTrue(any(f["text"].startswith("The change is spread out") for f in result["findings"]
                            if f["period"] == "yesterday"))

    def test_a_small_change_is_called_normal_and_not_explained(self):
        add_visitors(YESTERDAY, 98, source="google.com", views=2)
        add_visitors(YESTERDAY, 96, source="", views=2)
        add_clicks(YESTERDAY, 58)
        result = traffic.explain(NOW)
        self.assertTrue(result["yesterday"]["noise"])
        self.assertFalse(any(f["kind"] == "driver" and f["period"] == "yesterday" for f in result["findings"]))


class DriverEdgeTests(TestCase):
    def test_a_comparison_with_nobody_has_no_percentage_and_names_no_driver(self):
        normal_days(TODAY - timedelta(days=20), YESTERDAY, people=100, clicks=40, hours=[9, 10, 11, 12])
        add_visitors(TODAY, 70, hours=[0])
        result = traffic.explain(at(TODAY, 0, 30))
        today = result["today_so_far"]
        self.assertEqual((today["now"], today["then"]), (70, 0))
        self.assertEqual(today["drivers"]["named"], [])
        self.assertIn("Today to 00:30: 70 people, against none by the same time last Sunday.", texts(result))
        self.assertFalse(any("%" in t for t in texts(result) if t.startswith("Today")))

    def test_a_group_that_moved_more_than_the_whole_change_says_why(self):
        normal_days(TODAY - timedelta(days=30), YESTERDAY - timedelta(days=1), people=100, clicks=40)
        normal_days(TODAY - timedelta(days=30), YESTERDAY - timedelta(days=1), people=60, clicks=0, returning=True)
        add_visitors(YESTERDAY, 250, views=2)
        add_visitors(YESTERDAY, 10, views=2, returning=True)
        add_clicks(YESTERDAY, 100)
        result = traffic.explain(NOW)
        finding = next(f["text"] for f in result["findings"] if f["kind"] == "driver" and f["period"] == "yesterday")
        self.assertEqual(finding, "Most of the rise came from New visitors: 100 then, 250 now, 150 more. That is more "
                                  "than the whole rise of 100, because Returning visitors fell by 50.")


class HealthTests(TestCase):
    def test_visitors_with_no_clicks_for_hours_warn_about_the_buy_links(self):
        normal_days(TODAY - timedelta(days=20), YESTERDAY, people=150, clicks=60)
        add_visitors(TODAY, 60, hours=[8, 9, 10, 11, 12])
        add_clicks(TODAY, 4, hours=[8])
        result = traffic.explain(NOW)
        warning = next(h for h in result["health"] if h["kind"] == "clicks")
        self.assertEqual((warning["start"], warning["end"]), ("09:00", "13:00"))
        self.assertIn("Check that the Buy now links work", warning["text"])
        self.assertEqual(result["findings"][0]["kind"], "health")

    def test_hours_with_no_visitors_warn_about_counting(self):
        normal_days(TODAY - timedelta(days=20), YESTERDAY, people=150, clicks=60)
        add_visitors(TODAY, 20, hours=[8, 9])
        add_clicks(TODAY, 8, hours=[8, 9])
        result = traffic.explain(NOW)
        warning = next(h for h in result["health"] if h["kind"] == "gap")
        self.assertEqual((warning["start"], warning["end"]), ("10:00", "13:00"))
        self.assertIn("No visitors were recorded from 10:00 to 13:00", result["headline"])

    def test_a_quiet_night_is_not_a_counting_gap(self):
        normal_days(TODAY - timedelta(days=20), YESTERDAY, people=150, clicks=60)
        add_visitors(TODAY, 50, hours=[8, 9, 10, 11, 12])
        add_clicks(TODAY, 20, hours=[8, 9, 10, 11, 12])
        result = traffic.explain(NOW)
        self.assertEqual(result["health"], [])
        self.assertEqual(result["healthy"], "Counting, Buy now clicks and shop reads look normal.")

    def test_a_whole_day_with_no_clicks_is_worded_from_the_registry(self):
        normal_days(TODAY - timedelta(days=20), YESTERDAY - timedelta(days=1), people=150, clicks=60)
        add_visitors(YESTERDAY, 150, views=2)
        result = traffic.explain(NOW)
        warning = next(h for h in result["health"] if h["kind"] == "clicks")
        self.assertTrue(warning["text"].startswith("No clicks to shops on Sat 17 Oct while 150 people arrived"))
        self.assertNotIn("24:00", warning["text"])

    def test_a_new_crowd_that_clicks_less_is_not_a_buy_links_fault_and_keeps_its_driver(self):
        normal_days(TODAY - timedelta(days=30), YESTERDAY - timedelta(days=1), people=400, clicks=360, source="google.com")
        add_visitors(YESTERDAY, 400, source="google.com", views=2)
        add_visitors(YESTERDAY, 1000, source="reddit.com", views=2)
        add_clicks(YESTERDAY, 420)
        add_visitors(TODAY, 150, source="google.com", hours=[8, 9, 10, 11, 12])
        add_clicks(TODAY, 130, hours=[8, 9, 10, 11, 12])
        result = traffic.explain(NOW)
        self.assertFalse(any(h["kind"] == "conversion" for h in result["health"]), result["health"])
        self.assertTrue(any(f["kind"] == "driver" and "Reddit" in f["text"] for f in result["findings"]))

    def test_a_real_click_drop_with_a_new_crowd_is_still_flagged(self):
        normal_days(TODAY - timedelta(days=30), YESTERDAY - timedelta(days=1), people=800, clicks=200, source="google.com")
        add_visitors(YESTERDAY, 800, source="google.com", views=2)
        add_visitors(YESTERDAY, 300, source="reddit.com", views=2)
        add_clicks(YESTERDAY, 90)          # the usual 800 alone should make about 200
        result = traffic.explain(NOW)
        self.assertTrue(any(h["kind"] == "conversion" for h in result["health"]), result["health"])
        self.assertTrue(any(f["kind"] == "driver" and "Reddit" in f["text"] for f in result["findings"]))

    def test_a_shown_change_keeps_its_driver_when_health_warnings_crowd_the_list(self):
        def finding(weight, kind, period="", text=""):
            return {"weight": weight, "kind": kind, "period": period, "text": text or f"{kind} {period} {weight}"}
        findings = [finding(95, "health"), finding(80, "health"), finding(70, "compare", "today"),
                    finding(69, "driver", "today"), finding(60, "compare", "yesterday"),
                    finding(59, "driver", "yesterday"), finding(30, "history")]
        ranked = [(f["kind"], f["period"], f["weight"]) for f in traffic.rank(findings)]
        # The second health warning (still listed under Health) gives way to where yesterday's change came from.
        self.assertEqual(ranked, [("health", "", 95), ("compare", "today", 70), ("driver", "today", 69),
                                  ("compare", "yesterday", 60), ("driver", "yesterday", 59)])
        # A second driver for today gives way before any health warning does.
        findings = [finding(95, "health"), finding(70, "compare", "today"), finding(69, "driver", "today"),
                    finding(68, "driver", "today"), finding(60, "compare", "yesterday"),
                    finding(59, "driver", "yesterday")]
        ranked = [(f["kind"], f["weight"]) for f in traffic.rank(findings)]
        self.assertEqual(ranked, [("health", 95), ("compare", 70), ("driver", 69), ("compare", 60), ("driver", 59)])

    def test_days_with_more_clicks_than_people_are_left_out_of_the_usual_rate(self):
        normal_days(TODAY - timedelta(days=20), YESTERDAY, people=100, clicks=30)
        for day in (TODAY - timedelta(days=7), YESTERDAY - timedelta(days=1)):
            add_clicks(day, 170)     # 200 clicks from 100 people
        add_visitors(TODAY, 60, hours=[8, 9, 10, 11, 12])
        result = traffic.explain(NOW)
        warning = next(h for h in result["health"] if h["kind"] == "clicks")
        # Last Sunday is left out of the rate, so 60 people at the 0.3 a person of the Sunday before.
        self.assertIsNone(result["today_so_far"]["conversion"])
        self.assertIn("when about 18 clicks were expected", warning["text"])
        self.assertEqual(round(result["yesterday"]["conversion"], 2), 0.3)
        doubtful = next(f for f in result["findings"] if f["kind"] == "clicks_doubtful")
        self.assertIn("Fri 16 Oct (200 clicks, 100 people), Sun 11 Oct (200 clicks, 100 people)", doubtful["text"])
        self.assertIn("a script that was clicking them may have stopped", doubtful["check"])

    def test_todays_expected_clicks_follow_the_reference_shown(self):
        # Sundays click far less than other days; last Sunday by 13:13 is the reference shown for today.
        normal_days(TODAY - timedelta(days=20), YESTERDAY, people=100, clicks=90)
        for sunday in (TODAY - timedelta(days=7), TODAY - timedelta(days=14)):
            OutboundClick.objects.filter(created_at__date=sunday).delete()
            add_clicks(sunday, 20)
        add_visitors(TODAY, 60, hours=[8, 9, 10, 11, 12])
        result = traffic.explain(NOW)
        today = result["today_so_far"]
        rate = today["then_clicks"] / today["then"]
        warning = next(h for h in result["health"] if h["kind"] == "clicks")
        self.assertIn(f"when about {round(60 * rate)} clicks were expected", warning["text"])

    def test_deploy_day_without_arrival_times_still_checks_clicks_and_never_claims_hours_were_checked(self):
        build_bens_table()
        result = traffic.explain(NOW)
        warning = next(h for h in result["health"] if h["kind"] == "clicks")
        self.assertTrue(warning["text"].startswith("No clicks to shops today up to 13:13 while 70 people arrived"))
        self.assertEqual(result["healthy"], "")
        self.assertEqual(result["checked"], {"clicks"})

    def test_deploy_day_with_clicks_says_what_could_not_be_checked(self):
        normal_days(TODAY - timedelta(days=20), YESTERDAY, people=100, clicks=40, timed=False)
        add_visitors(TODAY, 40, timed=False)
        add_clicks(TODAY, 16, hours=[9, 10, 11])
        result = traffic.explain(NOW)
        self.assertEqual(result["health"], [])
        self.assertIn("Visitors hour by hour can't be checked", result["healthy"])

    def test_clicks_per_visitor_falling_sharply_is_flagged(self):
        normal_days(TODAY - timedelta(days=20), YESTERDAY - timedelta(days=1), people=150, clicks=60)
        add_visitors(YESTERDAY, 150, views=2)
        add_clicks(YESTERDAY, 10)
        result = traffic.explain(NOW)
        warning = next(h for h in result["health"] if h["kind"] == "conversion")
        self.assertIn("6.7 clicks per 100 people, against 40 usually", warning["text"])


class HistoryTests(TestCase):
    def test_a_partly_timed_deploy_day_puts_same_time_comparisons_a_week_after_the_next_day(self):
        deploy = TODAY - timedelta(days=6)                  # Mon 12 Oct, live at midday
        normal_days(TODAY - timedelta(days=20), deploy - timedelta(days=1), people=100, clicks=40, timed=False)
        add_visitors(deploy, 50, timed=False)
        add_visitors(deploy, 50, hours=[13, 14, 15])
        normal_days(deploy + timedelta(days=1), YESTERDAY, people=100, clicks=40)
        add_visitors(TODAY, 30, hours=[9, 10])
        result = traffic.explain(NOW)
        self.assertEqual(result["history"]["first_full_day"], deploy + timedelta(days=1))
        note = next(f["text"] for f in result["findings"] if f["kind"] == "history")
        self.assertEqual(note, "Arrival times are recorded from Mon 12 Oct. The first whole day with them is Tue 13 Oct, "
                               "so same-time comparisons with last week start on Tue 20 Oct.")
        # A week after the first whole day, last week is there to compare with and the note has gone.
        add_visitors(TODAY + timedelta(days=2), 30, hours=[9, 10])
        later = traffic.explain(NOW + timedelta(days=2))
        self.assertEqual(later["today_so_far"]["references"][0]["key"], "week")
        self.assertFalse(any(f["kind"] == "history" for f in later["findings"]))


class BensTableTests(TestCase):
    def test_bens_week_reads_the_bursts_out_and_compares_sunday_like_with_like(self):
        build_bens_table(timed_from=date(2026, 10, 11))
        with CaptureQueriesContext(connection) as queries:
            result = traffic.explain(NOW)
        self.assertLessEqual(len(queries), 15)
        self.assertEqual({b["date"] for b in result["bursts"]}, {date(2026, 10, 6), date(2026, 10, 7), date(2026, 10, 9)})
        # The burst wording quotes the page views By day shows, not the visitors' own counts.
        reasons = " ".join(r for b in result["bursts"] for r in b["reasons"])
        self.assertIn("25,185 page views from 360 visitors", reasons)
        self.assertIn("31,295 page views from 448 visitors", reasons)
        today = result["today_so_far"]
        self.assertEqual(today["references"][0]["key"], "week")
        self.assertNotIn(date(2026, 10, 9), result["yesterday"]["base_days"])


class CommandTests(TestCase):
    def test_explain_traffic_prints_the_findings(self):
        from io import StringIO

        from django.core.management import call_command

        normal_days(TODAY - timedelta(days=20), YESTERDAY - timedelta(days=1), people=100, clicks=40, source="google.com")
        add_visitors(YESTERDAY, 30, source="google.com", views=2)
        add_clicks(YESTERDAY, 12)
        out = StringIO()
        call_command("explain_traffic", at="2026-10-18T13:13", stdout=out)
        text = out.getvalue()
        self.assertIn("Why traffic changed, at Sun 18 Oct 13:13", text)
        self.assertIn("Yesterday (Sat 17 Oct): 30 people, against 100 on an average Saturday (last 2)", text)
        self.assertIn("Search engines", text)
        self.assertIn("Health:", text)

    def test_the_command_says_plainly_when_no_arrival_times_exist(self):
        from io import StringIO

        from django.core.management import call_command

        normal_days(TODAY - timedelta(days=20), YESTERDAY, people=100, clicks=40, timed=False)
        out = StringIO()
        call_command("explain_traffic", at="2026-10-18T13:13", stdout=out)
        self.assertIn("; arrival times not recorded yet.", out.getvalue())


class QueryBoundTests(TestCase):
    def test_click_queries_read_only_the_window(self):
        normal_days(TODAY - timedelta(days=20), YESTERDAY, people=100, clicks=40)
        with CaptureQueriesContext(connection) as queries:
            traffic.explain(NOW)
        for query in queries.captured_queries:
            if "catalogue_outboundclick" in query["sql"]:
                self.assertIn("WHERE", query["sql"].split("FROM", 1)[1], query["sql"])
