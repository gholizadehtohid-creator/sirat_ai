"""A small client for the Bale bot API (tapi.bale.ai), which follows the
Telegram bot API's methods and shapes.

Why Bale and not Telegram (2026-11-24): this machine cannot reach
api.telegram.org (filtered; no proxy), while tapi.bale.ai answered, and the
owner chose Bale. Only the methods the bot uses are here, each one request,
no retries beyond what the caller decides: getMe, getUpdates (long poll),
sendMessage, sendDocument, answerCallbackQuery.

The token is part of every URL. It is never logged: errors are reported by
method name and status only.
"""

import json

import requests

TIMEOUT = (10, 40)
MAX_TEXT = 4000            # Bale/Telegram cap a message at 4,096 characters


class BaleError(Exception):
    """An API call that did not return ok=true."""


class BaleApi:

    def __init__(self, token, base="https://tapi.bale.ai", session=None):
        if not token:
            raise BaleError("no bot token is configured")
        self._token = str(token).strip()
        self._base = base.rstrip("/")
        self._session = session or requests.Session()

    def _url(self, method):
        return "%s/bot%s/%s" % (self._base, self._token, method)

    def call(self, method, payload=None, files=None, timeout=TIMEOUT):
        try:
            if files:
                response = self._session.post(self._url(method), data=payload or {},
                                              files=files, timeout=timeout)
            else:
                response = self._session.post(self._url(method), json=payload or {},
                                              timeout=timeout)
        except requests.RequestException as exc:
            raise BaleError("%s: %s" % (method, type(exc).__name__)) from None
        try:
            body = response.json()
        except ValueError:
            raise BaleError("%s: HTTP %s, not JSON" % (method, response.status_code))
        if not body.get("ok"):
            raise BaleError("%s: HTTP %s %s" % (
                method, response.status_code,
                str(body.get("description") or body.get("error_code") or "")[:160]))
        return body.get("result")

    # ---- methods --------------------------------------------------------------

    def get_me(self):
        return self.call("getMe")

    def get_updates(self, offset=None, timeout=20):
        payload = {"timeout": int(timeout)}
        if offset is not None:
            payload["offset"] = int(offset)
        return self.call("getUpdates", payload,
                         timeout=(10, int(timeout) + 15)) or []

    def send_message(self, chat_id, text, reply_markup=None):
        sent = []
        chunks = split_text(text)
        for i, chunk in enumerate(chunks):
            payload = {"chat_id": chat_id, "text": chunk}
            if reply_markup and i == len(chunks) - 1:
                payload["reply_markup"] = reply_markup     # buttons on the last part
            sent.append(self.call("sendMessage", payload))
        return sent[-1] if sent else None

    def send_document(self, chat_id, filename, content, caption=None):
        payload = {"chat_id": str(chat_id)}
        if caption:
            payload["caption"] = caption[:1000]
        files = {"document": (filename, content, "application/pdf")}
        return self.call("sendDocument", payload, files=files,
                         timeout=(10, 120))

    def answer_callback(self, callback_id, text=None):
        payload = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text[:190]
        try:
            return self.call("answerCallbackQuery", payload)
        except BaleError:
            return None            # not every Bale build implements it


def split_text(text, limit=MAX_TEXT):
    """Split a long message on line breaks, never inside a line if avoidable."""

    text = str(text or "")
    if len(text) <= limit:
        return [text]
    parts, current = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) + 1 > limit:
            parts.append(current)
            current = line
        else:
            current = (current + "\n" + line) if current else line
    if current:
        parts.append(current)
    return parts


def inline_keyboard(rows):
    """[[(text, callback_data), ...], ...] -> reply_markup."""

    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row]
                                for row in rows]}


def reply_keyboard(rows):
    """[[text, ...], ...] -> a persistent reply keyboard."""

    return {"keyboard": [[{"text": t} for t in row] for row in rows],
            "resize_keyboard": True}


def dumps(markup):
    return json.dumps(markup, ensure_ascii=False)
