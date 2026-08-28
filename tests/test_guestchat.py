# -*- coding: utf-8 -*-
"""A guest asking a question, through the real /api/chat, with Claude stubbed.

The point is to separate "ARC is broken for guests" from "the model said
nothing". The stub records exactly what ARC sent to Anthropic, so the tool list,
the system prompt and the reply path can all be inspected on a guest turn and
compared against an owner turn.
"""
import io
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, HUD, sandbox, system_text   # noqa: E402
sandbox()

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["ARC_ALLOWED_EMAILS"] = "owner@example.com"
os.environ["ARC_GUEST_EMAILS"] = "guest@example.com"
os.environ["ARC_SESSION_MAX_HOURS"] = "0"

from starlette.testclient import TestClient  # noqa: E402
import run       # noqa: E402
import session   # noqa: E402
import gauth     # noqa: E402

ok = True
sent = []          # every kwargs dict ARC handed to Anthropic


def check(label, got, want):
    global ok
    good = got == want
    ok = ok and good
    print(("  PASS  " if good else "  FAIL  ") + label +
          ("" if good else "\n          got  %r\n          want %r" % (got, want)))


def truthy(label, got):
    check(label, bool(got), True)


class Blk:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class Resp:
    def __init__(self, text):
        self.content = [Blk(text)]
        self.stop_reason = "end_turn"
        self.usage = types.SimpleNamespace(input_tokens=10, output_tokens=5)


class FakeMessages:
    async def create(self, **kwargs):
        sent.append(kwargs)
        return Resp("Certainly, sir. It is sunny.")


class FakeClaude:
    def __init__(self):
        self.messages = FakeMessages()

    async def close(self):
        pass


PROMPT = "You are ARC."


def ask(client, cookies, text="what is the weather"):
    return client.post("/api/chat", cookies=cookies, json={
        "system": PROMPT,
        "messages": [{"role": "user", "content": text}],
        "allow_actions": False,
    })


with TestClient(run.app) as client:
    run.app.state.claude = FakeClaude()

    owner_sid = session.create("owner@example.com", "browser")
    guest_sid = session.create("guest@example.com", "phone")
    OWNER = {run.COOKIE: owner_sid}
    GUEST = {run.COOKIE: guest_sid}

    print("The owner asks something:")
    sent.clear()
    r = ask(client, OWNER)
    check("HTTP 200", r.status_code, 200)
    truthy("a reply came back", (r.json() or {}).get("reply"))
    check("the model was actually called", len(sent), 1)

    print("\nThe GUEST asks the same thing:")
    sent.clear()
    r = ask(client, GUEST)
    check("HTTP 200", r.status_code, 200)
    if r.status_code != 200:
        print("      body:", r.text[:500])
    body = r.json() if r.status_code == 200 else {}
    truthy("a reply came back", body.get("reply"))
    check("the model was actually called", len(sent), 1)

    if sent:
        kw = sent[0]
        names = [t.get("name") for t in kw.get("tools", [])]
        print("\n  what ARC sent for the guest turn:")
        print("    tools     :", len(names))
        print("    names     :", sorted(n for n in names if n))
        # `system` is a list of blocks now — the server's rulebook first and
        # marked cacheable, the client's contribution second. The guest
        # preamble is appended to the second one.
        whole = system_text(kw)
        truthy("    guest is told it is a guest", "GUEST ACCOUNT" in whole)
        truthy("    no owner alarm summary leaked", "ALARMS SET" not in whole)
        truthy("    no alarm tools offered",
               not any((n or "").endswith("_alarm") or n == "list_alarms" for n in names))
        # NOT asserted: calendar/mail. Those appear only when the signed-in
        # browser's OWN google token is present, and this suite runs against a
        # scratch data dir with no token linked -- so 3 tools here is the test
        # environment, not the guest tier. run.GUEST_TOOLS is the contract.
        truthy("    public lookups are there", "weather" in names)
        # Pinned on purpose: the guest tier growing should be a decision, not
        # a side effect of adding a tool somewhere. 13 -> 15 when market_outlook
        # and market_compare were added, which are public prices and arithmetic.
        check("    the guest tier names 15 tools", len(run.GUEST_TOOLS), 15)
        truthy("    and the two additions are the market ones",
               {"market_outlook", "market_compare"} <= run.GUEST_TOOLS)
        # Every tool offered must be one a guest may actually run, or the model
        # will pick one and hit a refusal it cannot explain.
        bad = [n for n in names if n and n not in run.GUEST_TOOLS]
        check("    every offered tool is allowed for a guest", bad, [])

    print("\nThe guest asks something needing a tool it does NOT have:")
    sent.clear()
    r = ask(client, GUEST, "send a telegram to mum")
    check("still answers rather than erroring", r.status_code, 200)
    truthy("with words", (r.json() or {}).get("reply"))

    print("\nThings the guest's page polls on its own:")
    for path, want in [("/api/health", 200), ("/api/session", 200),
                       ("/api/alarms/due", 403), ("/api/reminders/due", 403),
                       ("/api/alerts/due", 403)]:
        got = client.get(path, cookies=GUEST).status_code
        check("  %-22s -> %d" % (path, want), got, want)

    h = client.get("/api/health", cookies=GUEST).json()
    check("  health says guest", h.get("guest"), True)
    check("  and hides the machine", h.get("computer"), False)

    print("\nThe guest's page itself loads and boots:")
    r = client.get("/", cookies=GUEST)
    check("  HUD served", r.status_code, 200)
    page = r.text
    # 'prompt: "main"' replaces SYSTEM_PROMPT here: the rulebook is not in the
    # page any more, and what proves the real HUD was served is that it asks
    # the server for it by name.
    for t in ['prompt: "main"', "startAlarmPoll", "speakerFace", "lvlShown"]:
        truthy("  contains %s" % t, t in page)

    # The expensive brain is the owner's. A switch in a browser is a
    # suggestion, so the check that matters is what actually reached the API —
    # not what the page offered or what the server said it would do.
    print("\nThe deep brain is the owner's, and the page cannot talk its way in:")

    def ask_with_brain(cookies, brain):
        sent.clear()
        r = client.post("/api/chat", cookies=cookies, json={
            "system": PROMPT, "allow_actions": False, "model": brain,
            "messages": [{"role": "user", "content": "why is my code failing"}]})
        return r, (sent[0].get("model") if sent else None)

    r, model = ask_with_brain(OWNER, "deep")
    truthy("  the owner asking for it gets Opus", "opus" in (model or ""))
    r, model = ask_with_brain(GUEST, "deep")
    check("  a guest asking for it is answered, not refused", r.status_code, 200)
    check("  ...by Sonnet", model, run.MODEL_CHOICES["smart"])
    # And told so, rather than shown OPUS-5 over a Sonnet answer.
    check("  ...and the reply says which brain answered",
          (r.json() or {}).get("brain"), "smart")

    # Chat mode is the top paid capability: it turns thinking on, raises effort,
    # and doubles the token ceiling, so a long written answer can cost more than
    # a hundred spoken ones. Same rule as the deep brain — the page may ASK, the
    # server decides, and the check that matters is what reached the API rather
    # than what the page believed.
    print("\nChat mode is bought, not toggled:")

    def ask_in_chat(cookies, want=True):
        sent.clear()
        r = client.post("/api/chat", cookies=cookies, json={
            "system": PROMPT, "allow_actions": False, "chat": want,
            "messages": [{"role": "user", "content": "explain how caching works"}]})
        return r, (sent[0] if sent else {})

    r, kw = ask_in_chat(OWNER)
    truthy("  the owner gets the written register", "CHAT MODE IS ON" in system_text(kw))
    check("  ...with room to write in", kw.get("max_tokens"), run.MAX_TOKENS_CHAT)
    truthy("  ...and thinking on, since nobody is waiting to hear it",
           (kw.get("thinking") or {}).get("type") == "adaptive")
    check("  ...and the reply says the register it used", (r.json() or {}).get("chat"), True)

    r, kw = ask_in_chat(GUEST)
    check("  a guest asking for it is answered, not refused", r.status_code, 200)
    check("  ...but NOT in the written register", "CHAT MODE IS ON" in system_text(kw), False)
    check("  ...and not at the written ceiling", kw.get("max_tokens"), run.MAX_TOKENS)
    # Told, rather than shown a chat layout over a reply written to be spoken.
    check("  ...and the reply says so", (r.json() or {}).get("chat"), False)

    # The entitlement table is the only place that decides, so that "may this
    # account do X" cannot drift into three answers.
    check("  the owner is entitled to both", run.ENTITLEMENTS["owner"], {"deep", "chat"})
    check("  a guest to neither", run.ENTITLEMENTS["guest"], set())
    truthy("  and health tells the page which, so it can grey out the rest",
           "entitled" in client.get("/api/health", cookies=GUEST).json())
    check("  a guest is told it has nothing",
          client.get("/api/health", cookies=GUEST).json().get("entitled"), [])

    # ---------------------------------------------------------------- Google
    # The leak this rules out: a guest signing in and reading the OWNER's
    # calendar and mail because their own session has no Google linked and
    # something helpfully fell back to the account that does.
    #
    # gauth keeps the active token path in a CONTEXTVAR, set per request. That
    # is the right design and it makes the obvious test wrong: reading
    # gauth._tok() after the response reads a DIFFERENT context, where nothing
    # was ever set, and every account appears to be using the owner's token.
    # It has to be recorded from inside the request, which is what the spy does.
    print("\nNobody borrows the owner's Google account:")
    import gauth
    seen = []
    _real = gauth.set_active_token_path

    def _spy(path):
        seen.append(str(path) if path else None)
        return _real(path)

    gauth.set_active_token_path = _spy
    try:
        # A real owner token on disk, so a fallback would actually WORK and the
        # test can catch it rather than passing because there was nothing there.
        gauth.TOKEN.write_text('{"token": "OWNER-ONLY"}', encoding="utf-8")

        seen.clear()
        client.get("/api/health", cookies=OWNER)
        owner_path = seen[-1] if seen else None
        seen.clear()
        client.get("/api/health", cookies=GUEST)
        guest_path = seen[-1] if seen else None

        truthy("    the owner is pinned to a file of their own", bool(owner_path))
        truthy("    so is the guest", bool(guest_path))
        check("    and they are NOT the same file", owner_path == guest_path, False)
        # None means "use the owner's token.json". Either of these being None is
        # the leak, and on a machine where that file exists it would work.
        check("    neither falls back to the owner's token.json",
              [p for p in (owner_path, guest_path) if p is None], [])
        truthy("    ...which is a real file here, so a fallback would have worked",
               gauth.TOKEN.exists())
        # A guest who has not linked Google reads as not connected, rather than
        # quietly reading somebody else's mail.
        h = client.get("/api/health", cookies=GUEST).json()
        check("    an unlinked guest sees no calendar", h.get("calendar"), False)
        check("    and no mail", h.get("email"), False)
    finally:
        gauth.set_active_token_path = _real
        try:
            gauth.TOKEN.unlink()
        except Exception:
            pass


    # A dark chip has two very different causes and they need opposite
    # instructions. Google's consent screen arrives with EVERY BOX UNTICKED,
    # and prompt=consent means it arrives on every single sign-in — so pressing
    # Continue a moment early leaves an account that is properly connected and
    # cannot read the calendar. Telling somebody to connect Google when they
    # just did is how they conclude the app is broken.
    print("\nA missing permission is not a missing account:")
    import json as _json
    ALL = (list(gauth.CAL_SCOPES) + list(gauth.MAIL_SCOPES)
           + list(gauth.CONTACTS_SCOPES) + list(gauth.DRIVE_SCOPES))

    def _health_with(scopes):
        sid = session.create("owner@example.com", "browser")
        p = run.google_path(sid)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_json.dumps({"token": "x", "scopes": scopes}), encoding="utf-8")
        return client.get("/api/health", cookies={run.COOKIE: sid}).json()

    h = _health_with(ALL)
    check("    everything ticked -> nothing to report", h.get("ungranted"), [])
    check("    ...and linked", h.get("google_linked"), True)

    h = _health_with([x for x in ALL if "calendar" not in x])
    check("    calendar left unticked is NAMED", h.get("ungranted"), ["calendar"])
    check("    ...the chip is still dark", h.get("calendar"), False)
    check("    ...but the account is linked, which is the distinction",
          h.get("google_linked"), True)

    sid = session.create("owner@example.com", "browser")
    h = client.get("/api/health", cookies={run.COOKIE: sid}).json()
    check("    never connected -> not linked", h.get("google_linked"), False)
    check("    ...and nothing to blame on a tick box", h.get("ungranted"), [])

    hud = io.open(HUD, encoding="utf-8").read()
    truthy("    the page says which permission it was",
           "was\" : \"were" in hud or "not allowed. The consent screen" in hud)
    truthy("    ...once, not on every poll", "__arcSaidUngranted" in hud)

    session.revoke_all()

print("\nALL PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
