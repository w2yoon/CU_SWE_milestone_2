#!/bin/bash
source "$(dirname "$0")/lib.sh"
PY=/opt/test-venv/bin/python3 py <<'PY'
from chk import *
import re
from playwright.sync_api import sync_playwright
(u0, u1), slug, ws, ch = setup(2); (t0, i0), (t1, i1) = u0, u1
C = "c" + S; dialogs = []
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1280, "height": 900}); pg.set_default_timeout(3000)
    pg.on("dialog", lambda d: (dialogs.append(d.type), d.dismiss()))
    q = lambda t: pg.locator(f"[data-testid={t}]").first
    row = lambda txt: pg.locator("[data-testid=message]", has=pg.locator("[data-testid=message-body]", has_text=txt)).first
    def open_ch():
        e = pg.locator(f"[data-testid=channel-entry][data-channel-name={C}]").first; e.wait_for(); e.click()
        pg.wait_for_function(f"document.querySelector('[data-testid=channel-title]').innerText.includes('{C}')")
    try:
        pg.goto(f"http://127.0.0.1:8000/?token={t0}"); open_ch()
    except Exception as e:
        for i in range(1, 10): bad(f"E5.{i}", "setup: load /?token= and open the channel entry", str(e).split("\n")[0])
        finish()
    def empty():
        e = q("empty-channel"); e.wait_for(state="visible"); return True if e.inner_text().strip() else "blank empty state"
    rule("E5.1", "empty channel shows non-blank copy", empty)
    def send():
        q("message-input").fill("hello ui"); q("send-btn").click(); r = row("hello ui"); r.wait_for()
        parts = {t: r.locator(f"[data-testid={t}]").first.inner_text().strip() for t in ("message-avatar", "message-author", "message-time")}
        if not all(parts.values()) or re.search(r"\d{4}-\d\d-\d\dT", parts["message-time"]): return f"row parts {parts}"
        return True if q("message-input").input_value() == "" else "input not cleared"
    rule("E5.2", "send message; row has avatar/author/time/body", send)
    def blank():
        n = pg.locator("[data-testid=message]").count(); q("message-input").fill("   "); q("send-btn").click(); pg.wait_for_timeout(700)
        api = len(http("GET", f"/api/channels/{ch}/messages", t0)[1].get("messages", []))
        return True if pg.locator("[data-testid=message]").count() == n and api == 1 else f"rows {n}->{pg.locator('[data-testid=message]').count()}, api msgs {api}"
    rule("E5.3", "blank message is not sent", blank)
    def reload():
        pg.reload(); open_ch(); row("hello ui").wait_for(); return True
    rule("E5.4", "message survives hard reload", reload)
    def edit():
        r = row("hello ui"); r.hover(); r.locator("[data-testid=edit-message-btn]").click()
        e = q("edit-input"); v = e.input_value(); e.fill("edited ui"); e.press("Enter")
        r = row("edited ui"); r.wait_for(); r.locator("[data-testid=edited-indicator]").wait_for(state="visible")
        pg.reload(); open_ch(); row("edited ui").locator("[data-testid=edited-indicator]").wait_for(state="visible")
        return True if v == "hello ui" else f"edit-input prefilled with {v!r}"
    rule("E5.5", "edit via hover toolbar; (edited) shown, survives reload", edit)
    def delete():
        q("message-input").fill("to delete"); q("send-btn").click(); r = row("to delete"); r.wait_for()
        r.hover(); r.locator("[data-testid=delete-message-btn]").click(); r.wait_for(state="detached")
        pg.reload(); open_ch(); row("edited ui").wait_for()
        return True if row("to delete").count() == 0 and not dialogs else f"still there or dialog used {dialogs}"
    rule("E5.6", "delete via hover toolbar, no dialog, survives reload", delete)
    def react():
        r = row("edited ui"); r.hover(); r.locator("[data-testid=reaction-button]").click()
        pk = q("emoji-picker"); pk.wait_for(state="visible")
        disp = pk.evaluate("e=>getComputedStyle(e).display"); n = pk.locator("[data-testid=emoji-option]").count()
        em = pk.locator("[data-testid=emoji-option]").first.inner_text().strip()
        pk.locator("[data-testid=emoji-option]").first.click(); pk.wait_for(state="hidden")
        c = r.locator("[data-testid=reaction-chip]").first; c.wait_for(state="visible"); t = c.inner_text()
        if disp != "grid" or n < 6 or em not in t or "1" not in t: return f"display={disp} options={n} chip={t!r}"
        c.click(); c.wait_for(state="detached"); return True
    rule("E5.7", "emoji-picker grid -> reaction chip with count 1; click removes", react)
    def thread():
        r = row("edited ui"); r.hover(); r.locator("[data-testid=open-thread-btn]").click()
        tp = q("thread-panel"); tp.wait_for(state="visible")
        right = tp.bounding_box()["x"] > q("message-list").bounding_box()["x"]
        q("thread-input").fill("reply ui"); q("thread-send").click()
        tp.locator("[data-testid=message-body]", has_text="reply ui").first.wait_for()
        rc = r.locator("[data-testid=reply-count]").first; rc.wait_for(state="visible"); txt = rc.inner_text()
        q("close-thread").click(); tp.wait_for(state="hidden")
        return True if right and re.search(r"1 repl", txt) else f"right={right} reply-count={txt!r}"
    rule("E5.8", "thread panel on the right; reply bumps 'N replies'", thread)
    def live():
        post(t1, ch, "from api", port=8001); row("from api").wait_for(timeout=3500); return True
    rule("E5.9", "message from another user appears without reload", live)
    b.close()
finish()
PY
