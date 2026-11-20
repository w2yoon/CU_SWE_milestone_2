#!/bin/bash
source "$(dirname "$0")/lib.sh"
PY=/opt/test-venv/bin/python3 py <<'PY'
from chk import *
import re
from playwright.sync_api import sync_playwright
STATIC = "auth-form auth-submit auth-toggle workspace-create-form empty-state-create-workspace join-workspace-form join-workspace-submit channel-list dms-list new-channel-btn current-user logout-btn workspace-header workspace-settings-btn workspace-settings-close workspace-name-input workspace-join-mode workspace-general-submit channel-title channel-topic channel-settings-btn channel-settings-title channel-settings-close channel-members-list channel-add-member-form channel-add-member-input channel-add-member-submit channel-topic-input channel-topic-submit archive-channel-btn unarchive-channel-btn create-channel-form create-channel-private create-channel-submit create-channel-cancel message-list message-input send-btn thread-panel thread-input thread-send close-thread settings-tab-members settings-tab-invitations settings-tab-general settings-pane-members settings-pane-invitations settings-pane-general create-invitation-btn create-invitation-form create-invitation-submit invitations-list".split()
IDS = "auth-modal create-channel-modal channel-settings-modal workspace-settings-modal create-invitation-form".split()
ROLE = {"primary": "auth-submit send-btn thread-send create-channel-submit join-workspace-submit workspace-general-submit create-invitation-submit channel-topic-submit channel-add-member-submit",
        "danger": "archive-channel-btn logout-btn",
        "secondary": "new-channel-btn create-invitation-btn unarchive-channel-btn create-channel-cancel close-thread channel-settings-close workspace-settings-close channel-settings-btn workspace-settings-btn"}
RGB = {"primary": "rgb(0, 122, 90)", "danger": "rgb(224, 30, 90)", "secondary": "rgb(18, 100, 163)"}
BTN = {t: r for r, ts in ROLE.items() for t in ts.split()}
res = [http("GET", "/", port=p, full=True) for p in (8000, 8001, 8002)]
check("E4.1", "GET / -> 200 text/html on 3 nodes", all(r[0] == 200 and "text/html" in str((r[2] or {}).get("Content-Type", "")) for r in res), [r[0] for r in res])
html = (res[0][3] or b"").decode(errors="replace")
tags = {m.group(1): m.group(0) for m in re.finditer(r'<[^>]*data-testid=["\']([\w-]+)["\'][^>]*>', html)}
miss = [t for t in STATIC if t not in tags] + ["#" + i for i in IDS if not re.search(r'id=["\']%s["\']' % i, html)]
check("E4.2", "initial HTML has every static data-testid and DOM id", not miss, f"missing {miss}")
wr = [t for t, r in BTN.items() if not re.search(r'data-button-role=["\']%s["\']' % r, tags.get(t, ""))]
check("E4.3", "data-button-role on every pinned button", not wr, f"wrong/missing {wr}")
U = "ui" + S; T = None
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1280, "height": 900}); pg.set_default_timeout(3000)
    pg.goto("http://127.0.0.1:8000/")
    tok = lambda: pg.evaluate("localStorage.getItem('huddle.token')")
    q = lambda t: pg.locator(f"[data-testid={t}]").first
    def colors():
        got = pg.evaluate("bs=>Object.keys(bs).map(t=>{const e=document.querySelector(`[data-testid=${t}]`);if(!e)return [t,'missing'];const s=getComputedStyle(e);return [t,s.backgroundColor,s.color,s.backgroundImage]})", BTN)
        wrong = [g for g in got if g[1] != RGB[BTN[g[0]]] or g[2] != "rgb(255, 255, 255)" or g[3] != "none"]
        return True if not wrong else f"wrong {wrong[:4]}"
    rule("E4.4", "computed button colours match roles", colors)
    def auth_modes():
        assert pg.locator("#auth-modal").is_visible(), "auth-modal not visible"
        m = [q("auth-form").get_attribute("data-mode")]
        q("auth-toggle").click(); m.append(q("auth-form").get_attribute("data-mode"))
        q("auth-toggle").click(); m.append(q("auth-form").get_attribute("data-mode"))
        q("auth-toggle").click()
        return True if m == ["login", "register", "login"] else f"modes {m}"
    rule("E4.5", "auth modal visible; toggle login<->register", auth_modes)
    f = lambda n: q("auth-form").locator(f"[name={n}]")
    def bad_signup():
        f("username").fill("bad name"); f("password").fill("short"); q("auth-submit").click()
        e = pg.locator("#auth-modal [data-testid=auth-error]").first; e.wait_for(state="visible")
        return True if e.inner_text().strip() and not tok() else "no visible error or token stored"
    rule("E4.6", "invalid sign-up shows visible error", bad_signup)
    def signup():
        f("username").fill(U); f("password").fill(PW); f("display_name").fill("UI User"); q("auth-submit").click()
        pg.wait_for_function("localStorage.getItem('huddle.token')")
        pg.locator("#auth-modal").wait_for(state="hidden")
        return True if U in q("current-user").inner_text() or "UI User" in q("current-user").inner_text() else "current-user lacks name"
    rule("E4.7", "valid sign-up auto-logs in", signup)
    def mkws():
        q("empty-state-create-workspace").wait_for(state="visible")
        wf = q("workspace-create-form"); wf.locator("[name=name]").fill("UI WS"); wf.locator("[name=slug]").fill("ui" + S)
        wf.evaluate("f=>f.requestSubmit()")
        e = pg.locator("[data-testid=channel-entry][data-channel-name=general]").first; e.wait_for()
        ok_ = re.match(r"^#\s?general", e.inner_text().strip()) and e.get_attribute("data-channel-id") and "UI WS" in q("workspace-header").inner_text()
        pg.wait_for_function("document.querySelector('[data-testid=channel-title]').innerText.includes('general')")
        return True if ok_ else f"entry {e.inner_text()!r}, header {q('workspace-header').inner_text()!r}"
    rule("E4.8", "create workspace from empty state; #general listed and selected", mkws)
    def newch():
        q("new-channel-btn").click(); pg.locator("#create-channel-modal").wait_for(state="visible")
        q("create-channel-form").locator("[name=name]").fill("random"); q("create-channel-submit").click()
        pg.locator("[data-testid=channel-entry][data-channel-name=random]").first.wait_for()
        pg.locator("#create-channel-modal").wait_for(state="hidden")
        pg.wait_for_function("document.querySelector('[data-testid=channel-title]').innerText.includes('random')")
        q("new-channel-btn").click(); q("create-channel-form").locator("[name=name]").fill("random"); q("create-channel-submit").click()
        e = pg.locator("#create-channel-modal [data-testid=create-channel-error]").first; e.wait_for(state="visible")
        t = e.inner_text().strip(); q("create-channel-cancel").click()
        return True if t else "empty duplicate error"
    rule("E4.9", "create channel via modal; duplicate shows error", newch)
    def switch():
        pg.locator("[data-testid=channel-entry][data-channel-name=general]").first.click()
        pg.wait_for_function("document.querySelector('[data-testid=channel-title]').innerText.includes('general')")
        return True if pg.locator("[data-testid=channel-topic]").count() else "no channel-topic"
    rule("E4.10", "switch channel updates header", switch)
    def relog():
        q("logout-btn").click(); pg.locator("#auth-modal").wait_for(state="visible")
        assert not tok(), "token still stored after logout"
        if q("auth-form").get_attribute("data-mode") != "login": q("auth-toggle").click()
        f("username").fill(U); f("password").fill(PW); q("auth-submit").click()
        pg.wait_for_function("localStorage.getItem('huddle.token')")
        pg.wait_for_function("document.querySelector('[data-testid=workspace-header]').innerText.includes('UI WS')")
        return True
    rule("E4.11", "logout + login restores the same workspace", relog)
    b.close()
finish()
PY
