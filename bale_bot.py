"""The Bale bot: gives approved users the panel's features in a chat.

    python bale_bot.py            run it on its own (the panel need not run)
    started by the panel           when /admin/bot has it enabled (app.main)

WHAT IT DOES (2026-11-24, the owner's choices)
-----------------------------------------------
Bale only (this machine cannot reach Telegram; Bale's API follows Telegram's).
Access by the admin's approval. Four features: one company's outlook with a
PDF report, a market summary, the allocation profiles, and the user's own
holdings with alerts on their companies' meetings, dividends and capital
increases. Nothing the bot's own tests rejected is offered (bot_content).

HOW IT GETS ITS ANSWERS
------------------------
Through the panel's own routes, in process (Flask's test client): the same
JSON the panel shows, with the panel's caches and its one-report-at-a-time
lock. Started from app.main it is handed the running app, so there is one
copy of the panel's state, not two.

HOW IT RUNS
-----------
One thread long-polls getUpdates; each update is handled on a small pool,
one chat at a time. Between polls it sends what the admin page queued in
the outbox and runs the two daily jobs (the evening summary, the alerts) at
the times set on the admin page, Tehran time, once a day each. A failure of
one update is logged and answered with an apology; it never stops the loop.
"""

import json
import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import bot_api
import bot_content as content
import bot_store as store
import jalali

LOG = logging.getLogger("bale_bot")

POLL_SECONDS = 10
WORKERS = 4
OUTBOX_GAP = 0.15
MAX_BACKOFF = 120

MENU_KEYBOARD = bot_api.reply_keyboard(content.MENU)

BUTTONS = {
    "🔎 بررسی نماد": "ask_symbol",
    "📊 خلاصه‌ی بازار": "market",
    "🧭 تخصیص دارایی": "allocation",
    "💼 سبد من": "portfolio",
    "🔔 هشدارها": "alerts",
    "ℹ️ راهنما": "help",
    "/help": "help", "/market": "market", "/allocation": "allocation",
    "/portfolio": "portfolio", "/alerts": "alerts",
}


# ---- the panel, in process ---------------------------------------------------------

class PanelData:
    """The panel's routes through Flask's test client."""

    def __init__(self, flask_app=None):
        self._app = flask_app
        self._client = None

    def _c(self):
        if self._client is None:
            if self._app is None:
                import app as panel
                self._app = panel.app
            self._client = self._app.test_client()
        return self._client

    def _json(self, url, method="get", body=None):
        response = (self._c().post(url, json=body) if method == "post"
                    else self._c().get(url))
        try:
            data = response.get_json(silent=True)
        except Exception:                                       # noqa: BLE001
            data = None
        return response.status_code, (data if data is not None else {})

    def outlook(self, symbol):
        return self._json("/api/outlook?symbol=" + quote(symbol))

    def report(self, symbol):
        """(status, pdf bytes or error text, filename)."""
        response = self._c().get("/api/report?symbol=" + quote(symbol))
        if response.status_code == 200 and response.mimetype == "application/pdf":
            disposition = response.headers.get("Content-Disposition") or ""
            name = "%s.pdf" % symbol
            if "filename" in disposition:
                name = disposition.split("filename", 1)[1].split(";")[0].strip(" =*\"'")
                name = name.replace("UTF-8''", "") or "%s.pdf" % symbol
            return 200, response.data, name
        return response.status_code, response.get_data(as_text=True)[:300], None

    def market(self):
        return {"indices": self._json("/api/indices")[1],
                "flow": self._json("/api/marketflow")[1],
                "valuation": self._json("/api/valuation")[1]}

    def allocation(self):
        return self._json("/api/allocation")[1]

    def holdings(self, text):
        return self._json("/api/holdings", "post", {"list": text})[1]

    def calendar(self):
        return self._json("/api/corporate_calendar")[1]


# ---- the bot ---------------------------------------------------------------------

class Bot:

    def __init__(self, api, data, store_path=None, config=None):
        self.api = api
        self.data = data
        self.path = store_path
        self.config = config or store.load_config()
        self.offset = None
        self._chat_locks = {}
        self._locks_guard = threading.Lock()
        self.status = {"running": False, "last_poll": None, "last_error": None,
                       "username": None, "started_at": None, "handled": 0}

    # -- plumbing --
    def _lock_for(self, chat_id):
        with self._locks_guard:
            return self._chat_locks.setdefault(chat_id, threading.Lock())

    def send(self, chat_id, text, markup=None):
        try:
            return self.api.send_message(chat_id, text, markup)
        except bot_api.BaleError as exc:
            LOG.warning("send to %s failed: %s", chat_id, exc)
            store.log_event(chat_id, "send", ok=False, detail=str(exc), path=self.path)
            return None

    def _quota_ok(self, user, kind="request"):
        if user.get("role") == store.ADMIN:
            return True
        limit = user.get("pdf_quota" if kind == "pdf" else "daily_quota")
        if limit is None:
            limit = self.config.get("pdf_quota" if kind == "pdf" else "daily_quota")
        return store.used_today(user["chat_id"], kind, self.path) < int(limit or 0)

    # -- updates --
    def handle_update(self, update):
        message = update.get("message") or update.get("edited_message")
        callback = update.get("callback_query")
        chat_id = None
        if message:
            chat_id = (message.get("chat") or {}).get("id")
        elif callback:
            chat_id = ((callback.get("message") or {}).get("chat") or {}).get("id") \
                or (callback.get("from") or {}).get("id")
        if chat_id is None:
            return
        with self._lock_for(chat_id):
            started = time.time()
            kind = "callback" if callback else "message"
            try:
                if callback:
                    kind = self.on_callback(chat_id, callback) or kind
                else:
                    kind = self.on_message(chat_id, message) or kind
                store.log_event(chat_id, kind, True, int((time.time() - started) * 1000),
                                path=self.path)
            except Exception as exc:                            # noqa: BLE001
                LOG.exception("update for %s failed", chat_id)
                store.log_event(chat_id, kind, False,
                                int((time.time() - started) * 1000),
                                "%s: %s" % (type(exc).__name__, exc), path=self.path)
                self.send(chat_id, content.FAILED)
            self.status["handled"] += 1

    def on_message(self, chat_id, message):
        sender = message.get("from") or {}
        text = str(message.get("text") or "").strip()
        user, created = store.touch_user(chat_id, sender, self.path)

        if created:
            self.send(chat_id, content.welcome_pending(sender.get("first_name")))
            self.notify_admins_of(user)
            return "join"
        if user["status"] == store.BLOCKED:
            self.send(chat_id, content.BLOCKED)
            return "blocked"
        if user["status"] != store.APPROVED:
            self.send(chat_id, content.PENDING)
            return "pending"

        if text in ("/start", "start"):
            self.send(chat_id, content.welcome_approved(sender.get("first_name")),
                      MENU_KEYBOARD)
            return "start"

        if user.get("state") == "awaiting_holdings" and text not in BUTTONS:
            return self.save_holdings(user, text)

        action = BUTTONS.get(text)
        if action == "help":
            self.send(chat_id, content.help_text(), MENU_KEYBOARD)
            return "help"
        if action == "ask_symbol":
            self.send(chat_id, "نماد را بفرستید، مثلاً «فولاد».")
            return "ask_symbol"
        if action == "alerts":
            return self.show_alerts(user)

        if not self._quota_ok(user):
            self.send(chat_id, content.QUOTA)
            return "quota"
        store.count_use(chat_id, "request", self.path)

        if action == "market":
            return self.show_market(chat_id)
        if action == "allocation":
            self.send(chat_id, content.allocation_text(self.data.allocation()))
            return "allocation"
        if action == "portfolio":
            return self.show_portfolio(user)
        if not text or "\n" in text or len(text) > 20:
            self.send(chat_id, "نماد را تنها بفرستید، مثلاً «فولاد»، یا از دکمه‌ها استفاده کنید.")
            return "unknown"
        return self.show_stock(chat_id, text)

    def on_callback(self, chat_id, callback):
        data = str(callback.get("data") or "")
        self.api.answer_callback(callback.get("id"))
        user = store.get_user(chat_id, self.path) or {}
        action, _, arg = data.partition(":")

        if action in ("approve", "block") and user.get("role") == store.ADMIN \
                and user.get("status") == store.APPROVED:
            return self.admin_decide(chat_id, arg, action)
        if user.get("status") != store.APPROVED:
            self.send(chat_id, content.PENDING if user.get("status") == store.PENDING
                      else content.BLOCKED)
            return "denied"
        if action == "pdf":
            return self.send_pdf(user, arg)
        if action == "edit_holdings":
            store.set_fields(chat_id, {"state": "awaiting_holdings"}, self.path)
            self.send(chat_id, content.holdings_prompt(store.get_holdings(chat_id, self.path)))
            return "edit_holdings"
        if action == "toggle":
            field = {"summary": "summary_on", "alerts": "alerts_on"}.get(arg)
            if field:
                user = store.set_fields(chat_id, {field: 0 if user.get(field) else 1},
                                        self.path)
            return self.show_alerts(user)
        return "callback"

    # -- features --
    def show_stock(self, chat_id, symbol):
        status, outlook = self.data.outlook(symbol)
        if status != 200:
            self.send(chat_id, outlook.get("error") or "این نماد پیدا نشد.")
            return "stock_miss"
        events = []
        try:
            events = content.calendar_events_for(self.data.calendar(),
                                                 outlook.get("symbol") or symbol)
        except Exception:                                       # noqa: BLE001
            LOG.exception("calendar for %s", symbol)
        markup = bot_api.inline_keyboard(
            [[("📄 گزارش PDF", "pdf:%s" % (outlook.get("symbol") or symbol))]])
        self.send(chat_id, content.stock_text(outlook, events), markup)
        return "stock"

    def send_pdf(self, user, symbol):
        chat_id = user["chat_id"]
        if not self._quota_ok(user, "pdf"):
            self.send(chat_id, content.PDF_QUOTA)
            return "pdf_quota"
        self.send(chat_id, "گزارش %s در حال ساخت است (چند ثانیه)…" % symbol)
        status, body, name = self.data.report(symbol)
        if status == 429:
            self.send(chat_id, content.BUSY)
            return "pdf_busy"
        if status != 200:
            self.send(chat_id, "گزارش ساخته نشد.")
            store.log_event(chat_id, "pdf", False, detail="HTTP %s" % status, path=self.path)
            return "pdf_failed"
        store.count_use(chat_id, "pdf", self.path)
        # Named here: Flask's ASCII fallback of a Persian file name is only
        # its date ("-1405-09-03.pdf"), and that is what a parser reads first.
        self.api.send_document(chat_id, "%s-%04d-%02d-%02d.pdf" % ((symbol,) + tuple(jalali.today_jalali())), body,
                               caption="گزارش %s\n%s" % (symbol, content.DISCLAIMER))
        return "pdf"

    def show_market(self, chat_id):
        pieces = self.data.market()
        self.send(chat_id, content.market_text(pieces["indices"], pieces["flow"],
                                               pieces["valuation"]))
        return "market"

    def show_portfolio(self, user):
        chat_id = user["chat_id"]
        lines = store.get_holdings(chat_id, self.path)
        markup = bot_api.inline_keyboard([[("✏️ ویرایش سبد", "edit_holdings")]])
        if not lines:
            self.send(chat_id, "هنوز سبدی ثبت نکرده‌اید.", markup)
            return "portfolio_empty"
        rep = self.data.holdings("\n".join(lines))
        self.send(chat_id, content.holdings_text(rep), markup)
        return "portfolio"

    def save_holdings(self, user, text):
        chat_id = user["chat_id"]
        store.set_fields(chat_id, {"state": None}, self.path)
        if text in ("لغو", "/cancel"):
            self.send(chat_id, "ویرایش سبد لغو شد.", MENU_KEYBOARD)
            return "holdings_cancel"
        import holdings
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        lines = lines[:int(self.config.get("max_holdings") or 40)]
        entries, errors = holdings.parse_with_errors("\n".join(lines))
        store.set_holdings(chat_id, lines, self.path)
        reply = "سبد شما با %s خط ذخیره شد." % content.fa(len(lines))
        if errors:                          # {symbol: why its quantity is unread}
            reply += "\nاین خط‌ها کامل خوانده نشد:\n" + "\n".join(
                "• %s: %s" % (symbol, why) for symbol, why in list(errors.items())[:8])
        self.send(chat_id, reply, MENU_KEYBOARD)
        return "holdings_saved"

    def show_alerts(self, user):
        markup = bot_api.inline_keyboard([
            [("خلاصه‌ی روزانه: %s" % ("خاموش کن" if user.get("summary_on") else "روشن کن"),
              "toggle:summary")],
            [("رویدادهای سبد: %s" % ("خاموش کن" if user.get("alerts_on") else "روشن کن"),
              "toggle:alerts")]])
        self.send(user["chat_id"], content.alerts_text(user), markup)
        return "alerts"

    # -- admin --
    def notify_admins_of(self, user):
        markup = bot_api.inline_keyboard([[("✅ تأیید", "approve:%s" % user["chat_id"]),
                                           ("⛔ رد", "block:%s" % user["chat_id"])]])
        for admin in store.admins(self.path):
            self.send(admin["chat_id"], content.admin_join_request(user), markup)

    def admin_decide(self, admin_chat, target, action):
        try:
            target = int(target)
        except (TypeError, ValueError):
            return "admin_bad"
        user = store.get_user(target, self.path)
        if not user:
            self.send(admin_chat, "این کاربر پیدا نشد.")
            return "admin_missing"
        if action == "approve":
            store.set_status(target, store.APPROVED, self.path)
            self.send(target, content.welcome_approved(user.get("first_name")), MENU_KEYBOARD)
            self.send(admin_chat, "تأیید شد ✅")
        else:
            store.set_status(target, store.BLOCKED, self.path)
            self.send(admin_chat, "رد شد ⛔")
        return "admin_" + action

    # -- outbox and jobs --
    def flush_outbox(self):
        for item in store.pending_outbox(path=self.path):
            try:
                self.api.send_message(item["chat_id"], item["text"], item.get("markup"))
                store.mark_sent(item["id"], path=self.path)
            except bot_api.BaleError as exc:
                store.mark_sent(item["id"], error=str(exc), path=self.path)
            time.sleep(OUTBOX_GAP)

    def run_jobs(self, now=None):
        now = now or store.tehran_now()
        day = store.today(now)
        hhmm = now.strftime("%H:%M")
        if hhmm >= str(self.config.get("summary_time") or "19:30") and \
                not store.job_ran("summary", day, self.path):
            store.mark_job("summary", day, self.path)
            self.daily_summary(day)
        if hhmm >= str(self.config.get("alerts_time") or "20:00") and \
                not store.job_ran("alerts", day, self.path):
            store.mark_job("alerts", day, self.path)
            self.daily_alerts()

    def daily_summary(self, day):
        pieces = self.data.market()
        tedpix = [i for i in (pieces["indices"].get("indices") or [])
                  if i.get("key") == "TEDPIX"]
        if not tedpix or str(tedpix[0].get("last_date"))[:10] != day:
            return 0                       # no session today: nothing new to say
        text = content.market_text(pieces["indices"], pieces["flow"], pieces["valuation"])
        sent = 0
        for user in store.list_users(store.APPROVED, self.path):
            if user.get("summary_on"):
                self.send(user["chat_id"], text)
                sent += 1
                time.sleep(OUTBOX_GAP)
        return sent

    def daily_alerts(self):
        calendar = self.data.calendar()
        sent = 0
        for user in store.list_users(store.APPROVED, self.path):
            if not user.get("alerts_on"):
                continue
            lines = store.get_holdings(user["chat_id"], self.path)
            symbols = [l.split()[0] for l in lines if l.split()]
            fresh = []
            for symbol in symbols:
                for item in alert_items(calendar, symbol):
                    if not store.alert_sent(user["chat_id"], item[0], self.path):
                        fresh.append(item)
            if fresh:
                self.send(user["chat_id"], "🔔 رویدادهای نمادهای سبد شما:\n" + "\n".join(
                    "• " + text for _key, text in fresh[:12]) + "\n" + content.DISCLAIMER)
                for key, _text in fresh:
                    store.mark_alert(user["chat_id"], key, self.path)
                sent += 1
                time.sleep(OUTBOX_GAP)
        return sent

    # -- the loop --
    def run(self, stop):
        self.status.update(running=True, started_at=store.now_utc(), last_error=None)
        try:
            me = self.api.get_me() or {}
            self.status["username"] = me.get("username")
        except bot_api.BaleError as exc:
            self.status["last_error"] = str(exc)
        backoff = 5
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            while not stop.is_set():
                try:
                    self.config = store.load_config()
                    self.flush_outbox()
                    self.run_jobs()
                    updates = self.api.get_updates(self.offset, timeout=POLL_SECONDS)
                    self.status["last_poll"] = store.now_utc()
                    for update in updates:
                        self.offset = int(update.get("update_id", 0)) + 1
                        pool.submit(self.handle_update, update)
                    backoff = 5
                    self.status["last_error"] = None
                except bot_api.BaleError as exc:
                    self.status["last_error"] = str(exc)
                    LOG.warning("poll failed: %s", exc)
                    stop.wait(backoff)
                    backoff = min(backoff * 2, MAX_BACKOFF)
                except Exception as exc:                        # noqa: BLE001
                    self.status["last_error"] = "%s: %s" % (type(exc).__name__, exc)
                    LOG.exception("bot loop error")
                    stop.wait(backoff)
                    backoff = min(backoff * 2, MAX_BACKOFF)
        self.status["running"] = False


def alert_items(calendar, symbol):
    """[(dedupe key, Persian line)] for one symbol from the calendar."""

    import persian
    want = persian.normalize(symbol or "").replace(" ", "")
    out = []

    def same(item):
        return persian.normalize(item.get("symbol") or "").replace(" ", "") == want

    for p in calendar.get("pending_meetings") or []:
        if same(p):
            key = "meet:%s:%s" % (want, (p.get("invitation") or {}).get("tracing_no"))
            out.append((key, "%s: %s دعوت شد؛ برگزاری بین %s و %s (نماد آن روز معمولاً بسته است)" % (
                p.get("symbol"), p.get("meeting_type_fa") or "مجمع",
                content.jdate((p.get("expected_from") or {}).get("jalali")),
                content.jdate((p.get("expected_to") or {}).get("jalali")))))
    for d in calendar.get("recent_decisions") or []:
        if same(d):
            dps = d.get("dps_exchange") if d.get("dps_exchange") is not None else d.get("dps_codal")
            key = "decision:%s:%s" % (want, (d.get("decision") or {}).get("tracing_no"))
            line = "%s: %s برگزار شد" % (d.get("symbol"), d.get("meeting_type_fa") or "مجمع")
            if dps:
                line += "؛ سود هر سهم %s ریال، مهلت پرداخت تا %s" % (
                    content.fa(dps), content.jdate((d.get("payment_deadline") or {}).get("jalali")))
            out.append((key, line))
    for c in calendar.get("capital_increases") or []:
        if same(c) and c.get("status") != "stale":
            stage = c.get("latest_stage") or {}
            key = "capital:%s:%s" % (want, stage.get("tracing_no"))
            out.append((key, "%s: افزایش سرمایه — %s (%s)" % (
                c.get("symbol"), stage.get("label_fa") or "مرحله‌ی تازه",
                content.jdate((stage.get("date") or {}).get("jalali")))))
    return out


# ---- the runner the panel controls ---------------------------------------------------

_RUNNER = {"thread": None, "stop": None, "bot": None}
_RUNNER_LOCK = threading.Lock()


def start(flask_app=None):
    """Start the bot thread if it is enabled and has a token. True if running."""

    with _RUNNER_LOCK:
        if _RUNNER["thread"] is not None and _RUNNER["thread"].is_alive():
            return True
        config = store.load_config()
        if not (config.get("enabled") and config.get("token")):
            return False
        store.initialise()
        api = bot_api.BaleApi(config["token"], config.get("api_base") or
                              "https://tapi.bale.ai")
        bot = Bot(api, PanelData(flask_app), config=config)
        stop_event = threading.Event()
        thread = threading.Thread(target=bot.run, args=(stop_event,),
                                  name="bale-bot", daemon=True)
        _RUNNER.update(thread=thread, stop=stop_event, bot=bot)
        thread.start()
        return True


def stop(wait=5.0):
    with _RUNNER_LOCK:
        if _RUNNER["stop"] is not None:
            _RUNNER["stop"].set()
        thread = _RUNNER["thread"]
    if thread is not None:
        thread.join(wait)
    with _RUNNER_LOCK:
        _RUNNER.update(thread=None, stop=None)


def ensure(flask_app=None):
    """Match the thread to the settings: start, or stop, as they say."""

    config = store.load_config()
    if config.get("enabled") and config.get("token"):
        return start(flask_app)
    stop()
    return False


def runner_status():
    bot = _RUNNER.get("bot")
    thread = _RUNNER.get("thread")
    out = dict(bot.status) if bot else {"running": False}
    out["running"] = bool(thread is not None and thread.is_alive())
    return out


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    store.initialise()
    config = store.load_config()
    if not config.get("token"):
        print("No bot token. Set it on the panel's /admin/bot page first.")
        return 1
    api = bot_api.BaleApi(config["token"], config.get("api_base") or
                          "https://tapi.bale.ai")
    bot = Bot(api, PanelData(), config=config)
    stop_event = threading.Event()
    print("Bale bot running; Ctrl+C to stop.")
    try:
        bot.run(stop_event)
    except KeyboardInterrupt:
        stop_event.set()
    return 0


if __name__ == "__main__":
    sys.exit(main())
