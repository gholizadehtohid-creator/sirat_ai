"""The Bale bot (2026-11-24), offline: a fake Bale API, fake panel answers
and a temporary store. Nothing here touches the network or data/."""

import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime
from unittest import mock

import tests  # noqa: F401  (live stores read-only)

import bale_bot
import bot_api
import bot_content as content
import bot_store as store


class FakeApi:
    def __init__(self):
        self.sent, self.docs, self.answers = [], [], []

    def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))
        return {"message_id": len(self.sent)}

    def send_document(self, chat_id, filename, data, caption=None):
        self.docs.append((chat_id, filename, len(data), caption))

    def answer_callback(self, callback_id, text=None):
        self.answers.append(callback_id)

    def get_me(self):
        return {"username": "sirat_test_bot"}


OUTLOOK = {
    "symbol": "فولاد", "name": "فولاد مبارکه اصفهان", "method": "monthly_sales",
    "valuation": {"price": 5120.0, "pe_ttm": 7.03, "pe_forward": 4.55, "dps": 55.0,
                  "dividend_yield_pct": 1.1},
    "forward": {"eps": 1124.9, "range": {"eps_low": 868.6, "eps_high": 1387.3,
                                         "median_abs_error_pct": 22.1, "n": 324,
                                         "coverage": {"per10": 5, "worst_year": {
                                             "year": 1405, "rate": 0.19}}}},
    "fair_value": {"central": 5899.5, "low": 4391.8, "high": 5899.5, "basis": "pe"},
    "hurdle": {"rate_pct": 44.7, "required_growth_pct": 42.8, "actual_growth_pct": 67.6},
    "flags": ["خرید مواد اولیه +93٪ در برابر فروش +68٪"],
    "positives": ["حاشیه سود بهتر شده"],
    "latest": {"months": 6, "period_end": "1405/06/31"},
    "trading": {"notes": []},
}

CALENDAR = {
    "pending_meetings": [{"symbol": "فولاد", "meeting_type_fa": "مجمع عمومی عادی سالیانه",
                          "invitation": {"tracing_no": "111"},
                          "expected_from": {"jalali": "1405/09/10"},
                          "expected_to": {"jalali": "1405/10/10"}}],
    "recent_decisions": [{"symbol": "فولاد", "meeting_type_fa": "مجمع عمومی عادی سالیانه",
                          "decision": {"tracing_no": "222"}, "dps_codal": 55.0,
                          "meeting_date": {"jalali": "1405/05/12"},
                          "payment_deadline": {"jalali": "1406/01/12"}}],
    "capital_increases": [],
}


class FakeData:
    def __init__(self, tedpix_day=None):
        self.tedpix_day = tedpix_day or store.today()
        self.holdings_asked = []

    def outlook(self, symbol):
        if symbol in ("فولاد",):
            return 200, dict(OUTLOOK)
        return 404, {"error": "نماد پیدا نشد: %s" % symbol}

    def report(self, symbol):
        if symbol == "busy":
            return 429, "busy", None
        return 200, b"%PDF-1.4 fake", "%s.pdf" % symbol

    def market(self):
        return {"indices": {"indices": [{"key": "TEDPIX", "close": 8357840.4,
                                         "last_date": self.tedpix_day,
                                         "returns": {"1d": 2.7, "5d": 6.8, "20d": -1.2}}]},
                "flow": {"last": {"date": self.tedpix_day, "net_individual": 8.6e13,
                                  "breadth_pct": 68.7}, "sum_5": 1.5e14, "sum_20": -1.5e14},
                "valuation": {"now": {"pe": 6.93}, "percentile": 54, "median_pe": 6.7,
                              "bill_yield_pct": 43.7}}

    def allocation(self):
        return {"sleeve_labels": {"equity": "صندوق شاخصی سهام", "gold": "صندوق طلا"},
                "profiles": [{"label": "متعادل", "weights": {"equity": 70, "gold": 30},
                              "evidence": "provisional"}],
                "retest": {"text": "آزمون E36 ..."}, "rebalance": {"text": "هر سه ماه"}}

    def holdings(self, text):
        self.holdings_asked.append(text)
        return {"rows": [{"symbol": "فولاد", "quantity": 2000, "value": 10240000,
                          "change_pct": 2.8, "outlook": {"flags": []}}],
                "total_value": 10240000, "as_of": "2026-11-23", "warnings": []}

    def calendar(self):
        return CALENDAR


def msg(chat_id, text, first_name="علی"):
    return {"update_id": 1, "message": {"chat": {"id": chat_id}, "text": text,
                                        "from": {"id": chat_id, "first_name": first_name}}}


def cb(chat_id, data):
    return {"update_id": 2, "callback_query": {"id": "cb1", "data": data,
                                               "from": {"id": chat_id},
                                               "message": {"chat": {"id": chat_id}}}}


class StoreCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.db = os.path.join(self.tmp, "bot.db")
        self.cfg = os.path.join(self.tmp, "bot.json")
        store.initialise(self.db)
        p1 = mock.patch.object(store, "DB_PATH", self.db)
        p2 = mock.patch.object(store, "CONFIG_PATH", self.cfg)
        p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)
        self.api = FakeApi()
        self.data = FakeData()
        self.bot = bale_bot.Bot(self.api, self.data, store_path=self.db,
                                config=dict(store.DEFAULT_CONFIG, daily_quota=3, pdf_quota=1))

    def approve(self, chat_id, role=store.USER):
        store.touch_user(chat_id, {"first_name": "x"}, self.db)
        store.set_status(chat_id, store.APPROVED, self.db)
        if role == store.ADMIN:
            store.set_role(chat_id, store.ADMIN, self.db)

    def texts(self, chat_id=None):
        return [t for c, t, _m in self.api.sent if chat_id is None or c == chat_id]


class TestStore(StoreCase):

    def test_new_user_is_pending_and_names_refresh(self):
        user, created = store.touch_user(5, {"first_name": "مریم"}, self.db)
        self.assertTrue(created)
        self.assertEqual(user["status"], store.PENDING)
        user, created = store.touch_user(5, {"username": "maryam"}, self.db)
        self.assertFalse(created)
        self.assertEqual(user["username"], "maryam")
        self.assertEqual(user["first_name"], "مریم")

    def test_token_never_leaves_public_config(self):
        store.save_config({"token": "123456789:ABCDEFGHIJ", "enabled": True})
        public = store.public_config()
        self.assertNotIn("token", public)
        self.assertTrue(public["token_set"])
        self.assertEqual(public["token_hint"], "…GHIJ")
        store.save_config({"token": "", "summary_time": "18:00"})   # empty keeps it
        self.assertEqual(store.load_config()["token"], "123456789:ABCDEFGHIJ")

    def test_broadcast_queues_approved_only(self):
        self.approve(1)
        self.approve(2, store.ADMIN)
        store.touch_user(3, {}, self.db)                          # pending
        self.assertEqual(store.broadcast("سلام", path=self.db), 2)
        self.assertEqual(store.broadcast("ادمین", "admins", path=self.db), 1)
        self.assertEqual(len(store.pending_outbox(path=self.db)), 3)

    def test_counts_are_per_day_and_kind(self):
        self.assertEqual(store.count_use(1, "request", self.db), 1)
        self.assertEqual(store.count_use(1, "request", self.db), 2)
        self.assertEqual(store.used_today(1, "pdf", self.db), 0)


class TestBot(StoreCase):

    def test_first_start_is_pending_and_admins_hear_of_it(self):
        self.approve(99, store.ADMIN)
        self.bot.handle_update(msg(10, "/start"))
        self.assertIn("درخواست شما", self.texts(10)[0])
        admin_msgs = [(t, m) for c, t, m in self.api.sent if c == 99]
        self.assertEqual(len(admin_msgs), 1)
        buttons = admin_msgs[0][1]["inline_keyboard"][0]
        self.assertEqual(buttons[0]["callback_data"], "approve:10")

    def test_pending_and_blocked_get_no_data(self):
        store.touch_user(11, {}, self.db)
        self.bot.handle_update(msg(11, "فولاد"))
        self.assertEqual(self.texts(11)[-1], content.PENDING)
        store.set_status(11, store.BLOCKED, self.db)
        self.bot.handle_update(msg(11, "فولاد"))
        self.assertEqual(self.texts(11)[-1], content.BLOCKED)

    def test_symbol_lookup_with_pdf_button_and_events(self):
        self.approve(12)
        self.bot.handle_update(msg(12, "فولاد"))
        chat, text, markup = self.api.sent[-1]
        self.assertIn("فولاد مبارکه", text)
        self.assertIn("۵ مورد از ۱۰", text)
        self.assertIn("نشانه‌ی خرید", text)
        self.assertIn("دعوت شده", text)
        self.assertIn(content.DISCLAIMER, text)
        self.assertEqual(markup["inline_keyboard"][0][0]["callback_data"], "pdf:فولاد")

    def test_unknown_symbol(self):
        self.approve(13)
        self.bot.handle_update(msg(13, "ناموجود"))
        self.assertIn("پیدا نشد", self.texts(13)[-1])

    def test_daily_quota(self):
        self.approve(14)
        for _ in range(3):
            self.bot.handle_update(msg(14, "📊 خلاصه‌ی بازار"))
        self.bot.handle_update(msg(14, "📊 خلاصه‌ی بازار"))
        self.assertEqual(self.texts(14)[-1], content.QUOTA)

    def test_admin_has_no_quota(self):
        self.approve(15, store.ADMIN)
        for _ in range(5):
            self.bot.handle_update(msg(15, "🧭 تخصیص دارایی"))
        self.assertNotIn(content.QUOTA, self.texts(15))

    def test_pdf_is_sent_and_counted_and_capped(self):
        self.approve(16)
        self.bot.handle_update(cb(16, "pdf:فولاد"))
        self.assertEqual(len(self.api.docs), 1)
        self.assertRegex(self.api.docs[0][1], r"^فولاد-14[0-9]{2}-[0-9]{2}-[0-9]{2}[.]pdf$")
        self.bot.handle_update(cb(16, "pdf:فولاد"))
        self.assertEqual(self.texts(16)[-1], content.PDF_QUOTA)

    def test_pdf_busy(self):
        self.approve(17, store.ADMIN)
        self.bot.handle_update(cb(17, "pdf:busy"))
        self.assertEqual(self.texts(17)[-1], content.BUSY)

    def test_holdings_edit_flow(self):
        self.approve(18)
        self.bot.handle_update(cb(18, "edit_holdings"))
        self.assertEqual(store.get_user(18, self.db)["state"], "awaiting_holdings")
        self.bot.handle_update(msg(18, "فولاد 2000\nعیار 30000\nشتران 12 500"))
        self.assertEqual(store.get_holdings(18, self.db),
                         ["فولاد 2000", "عیار 30000", "شتران 12 500"])
        self.assertIsNone(store.get_user(18, self.db)["state"])
        self.assertIn("کامل خوانده نشد", self.texts(18)[-1])
        self.bot.handle_update(msg(18, "💼 سبد من"))
        self.assertIn("سبد شما", self.texts(18)[-1])
        self.assertEqual(self.data.holdings_asked[-1].splitlines()[0], "فولاد 2000")

    def test_alert_toggles(self):
        self.approve(19)
        self.bot.handle_update(cb(19, "toggle:summary"))
        self.assertEqual(store.get_user(19, self.db)["summary_on"], 0)

    def test_admin_approves_from_the_chat(self):
        self.approve(20, store.ADMIN)
        store.touch_user(21, {"first_name": "رضا"}, self.db)
        self.bot.handle_update(cb(20, "approve:21"))
        self.assertEqual(store.get_user(21, self.db)["status"], store.APPROVED)
        self.assertIn("خوش آمدید", self.texts(21)[-1])

    def test_a_non_admin_cannot_approve(self):
        self.approve(22)
        store.touch_user(23, {}, self.db)
        self.bot.handle_update(cb(22, "approve:23"))
        self.assertEqual(store.get_user(23, self.db)["status"], store.PENDING)

    def test_outbox_is_flushed(self):
        store.enqueue(30, "پیام ادمین", path=self.db)
        self.bot.flush_outbox()
        self.assertEqual(self.texts(30), ["پیام ادمین"])
        self.assertEqual(store.pending_outbox(path=self.db), [])

    def test_daily_alerts_are_sent_once(self):
        self.approve(31)
        store.set_holdings(31, ["فولاد 2000"], self.db)
        self.assertEqual(self.bot.daily_alerts(), 1)
        self.assertIn("سود هر سهم", self.texts(31)[-1])
        self.assertEqual(self.bot.daily_alerts(), 0)

    def test_summary_skips_a_day_without_a_session(self):
        self.approve(32)
        self.bot.data = FakeData(tedpix_day="2000-01-01")
        self.assertEqual(self.bot.daily_summary(store.today()), 0)
        self.bot.data = FakeData()
        self.assertEqual(self.bot.daily_summary(store.today()), 1)

    def test_jobs_run_once_after_their_time(self):
        self.approve(33)
        with mock.patch.object(self.bot, "daily_summary") as summary, \
                mock.patch.object(self.bot, "daily_alerts") as alerts:
            early = datetime(2026, 11, 24, 10, 0, tzinfo=store.TEHRAN)
            self.bot.run_jobs(early)
            summary.assert_not_called()
            late = datetime(2026, 11, 24, 21, 0, tzinfo=store.TEHRAN)
            self.bot.run_jobs(late)
            self.bot.run_jobs(late)
            self.assertEqual(summary.call_count, 1)
            self.assertEqual(alerts.call_count, 1)

    def test_a_failing_update_is_answered_not_raised(self):
        self.approve(34)
        with mock.patch.object(self.data, "allocation", side_effect=RuntimeError("x")):
            self.bot.handle_update(msg(34, "🧭 تخصیص دارایی"))
        self.assertEqual(self.texts(34)[-1], content.FAILED)


class TestContentAndApi(unittest.TestCase):

    def test_split_text_keeps_lines(self):
        text = "\n".join("خط %d" % i for i in range(2000))
        parts = bot_api.split_text(text, limit=500)
        self.assertTrue(all(len(p) <= 500 for p in parts))
        self.assertEqual("\n".join(parts), text)

    def test_markup_only_on_the_last_part(self):
        class Session:
            def __init__(self):
                self.payloads = []

            def post(self, url, json=None, data=None, files=None, timeout=None):
                self.payloads.append(json)
                return mock.Mock(status_code=200, json=lambda: {"ok": True, "result": {}})
        session = Session()
        api = bot_api.BaleApi("t0ken:abc", session=session)
        api.send_message(1, "x" * 9000, {"keyboard": []})
        self.assertEqual(len(session.payloads), 3)
        self.assertNotIn("reply_markup", session.payloads[0])
        self.assertIn("reply_markup", session.payloads[-1])

    def test_api_error_does_not_leak_the_token(self):
        session = mock.Mock()
        session.post.return_value = mock.Mock(status_code=401, json=lambda: {
            "ok": False, "description": "Unauthorized"})
        api = bot_api.BaleApi("SECRET:TOKEN", session=session)
        with self.assertRaises(bot_api.BaleError) as ctx:
            api.get_me()
        self.assertNotIn("SECRET", str(ctx.exception))

    def test_texts_carry_the_disclaimer_and_no_levels(self):
        data = FakeData()
        for text in (content.stock_text(OUTLOOK),
                     content.market_text(**{k: v for k, v in zip(
                         ("indices", "flow", "valuation"), data.market().values())}),
                     content.allocation_text(data.allocation()),
                     content.holdings_text(data.holdings(""))):
            self.assertIn(content.DISCLAIMER, text)
            for banned in ("حد ضرر", "حد سود", "هدف قیمت اول"):
                self.assertNotIn(banned, text)

    def test_todays_provisional_figures_are_labelled(self):
        """2026-11-24: before the exchange publishes the session, the market
        summary carries the overview's index and the flow estimate -- each
        marked provisional -- and nothing of the kind once it is official."""
        indices = {"indices": [], "provisional": {
            "session": "2026-11-24",
            "indices": {"TEDPIX": {"value": 8591498.89, "change_pct": 2.4}}}}
        flow = {"provisional": {"session": "2026-11-24", "flow": {
            "net_individual": 4.4e13, "breadth_pct": 65.1}}}
        text = content.market_text(indices=indices, flow=flow)
        self.assertIn("موقت تا انتشار رسمی", text)
        self.assertIn("برآورد پول حقیقی امروز", text)
        self.assertIn(content.DISCLAIMER, text)
        plain = content.market_text(indices={"indices": []}, flow={})
        self.assertNotIn("موقت", plain)

    def test_persian_digits(self):
        self.assertEqual(content.fa(1234567), "۱٬۲۳۴٬۵۶۷")
        self.assertEqual(content.pct(-1.25, 1), "−۱٫۲٪")


class TestAdminRoutes(unittest.TestCase):

    def setUp(self):
        import app
        self.client = app.app.test_client()
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        patches = [mock.patch.object(store, "DB_PATH", os.path.join(self.tmp, "b.db")),
                   mock.patch.object(store, "CONFIG_PATH", os.path.join(self.tmp, "b.json")),
                   mock.patch.object(bale_bot, "ensure", return_value=False),
                   mock.patch.object(bale_bot, "stop")]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        store.initialise()

    def test_page_and_state(self):
        self.assertEqual(self.client.get("/admin/bot").status_code, 200)
        state = self.client.get("/api/bot/state").get_json()
        self.assertFalse(state["config"]["token_set"])
        self.assertEqual(state["users"], [])

    def test_config_validates_and_checks_the_token(self):
        bad = self.client.post("/api/bot/config", json={"summary_time": "25:00"})
        self.assertEqual(bad.status_code, 400)
        with mock.patch.object(bot_api.BaleApi, "get_me",
                               side_effect=bot_api.BaleError("getMe: HTTP 401")):
            refused = self.client.post("/api/bot/config", json={"token": "x:bad"})
        self.assertEqual(refused.status_code, 400)
        with mock.patch.object(bot_api.BaleApi, "get_me", return_value={"username": "b"}):
            ok = self.client.post("/api/bot/config", json={
                "token": "123:goodtoken", "enabled": True, "daily_quota": 5000})
        body = ok.get_json()
        self.assertTrue(body["config"]["token_set"])
        self.assertNotIn("token", body["config"])
        self.assertEqual(body["config"]["daily_quota"], 1000)          # capped
        self.assertNotIn("123:goodtoken", json.dumps(body))

    def test_user_actions_and_broadcast(self):
        store.touch_user(7, {"first_name": "سارا"})
        r = self.client.post("/api/bot/users/7", json={"action": "admin"})
        self.assertEqual(r.status_code, 400)                          # not approved yet
        self.client.post("/api/bot/users/7", json={"action": "approve"})
        self.assertEqual(store.get_user(7)["status"], store.APPROVED)
        self.assertEqual(len(store.pending_outbox()), 1)              # the welcome
        self.client.post("/api/bot/users/7", json={"action": "admin"})
        self.assertEqual(store.get_user(7)["role"], store.ADMIN)
        self.client.post("/api/bot/users/7", json={"action": "quota", "daily_quota": 9,
                                                    "pdf_quota": ""})
        self.assertEqual(store.get_user(7)["daily_quota"], 9)
        self.assertIsNone(store.get_user(7)["pdf_quota"])
        sent = self.client.post("/api/bot/broadcast", json={"text": "سلام همه"}).get_json()
        self.assertEqual(sent["queued"], 1)
        self.client.post("/api/bot/users/7", json={"action": "delete"})
        self.assertIsNone(store.get_user(7))

    def test_cross_site_post_is_refused(self):
        r = self.client.post("/api/bot/broadcast", json={"text": "x"},
                             headers={"Origin": "https://evil.example"})
        self.assertEqual(r.status_code, 403)


if __name__ == "__main__":
    unittest.main()
