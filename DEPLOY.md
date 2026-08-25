# Deploying ARC

"Deployed" here does not mean a server. ARC runs on **your Windows desktop** —
that is the point of it, since half the tools reach the machine it is running
on — and deploying means making that desktop reachable from your phone,
safely, without leaving it open to the internet at large.

The [README](README.md) covers what every setting does. This is the order to
do things in, what to check afterwards, and what has actually gone wrong.

---

## 1. Before anything is reachable

Get it working on `http://localhost:8420` first. A tunnel multiplies problems;
it does not create them.

```powershell
pip install -r requirements.txt
python gauth.py          # links your Google account, opens a browser
python run.py
```

The banner is the checklist. It prints what is online and what is not:

```
  reasoning   online (claude-sonnet-5, effort medium)
  voice       online (elevenlabs)
  google      online (calendar + mail)
  access      google (you@gmail.com; no session timeout)
```

`ARC_ALLOWED_EMAILS` empty, or `credentials_web.json` missing, and ARC refuses
to start rather than coming up ungated. That is deliberate — read the message
rather than working around it.

## 2. A public URL

Two ways, and they differ in whether the URL survives a restart.

### Tailscale Funnel — permanent, and what this install uses

```powershell
tailscale funnel --bg 8420
tailscale funnel status        # gives you https://<machine>.<tailnet>.ts.net
```

The hostname is stable, so the phone's home-screen icon keeps working, the
Google redirect URI stays registered, and you only do the setup once.

### `python tunnel_qr.py` — Cloudflare quick tunnel, throwaway

Starts `cloudflared`, waits for the address, prints a QR code to scan. The URL
changes every time it starts, so it is for showing someone ARC on their phone
in five minutes, not for living with. Closing the window stops the exposure.

### Then, whichever you used

```ini
ARC_PUBLIC_URL=https://your-host.ts.net
```

Set it. Without it the OAuth redirect URI is derived from forwarding headers,
which a client can set — and the sign-in either breaks or trusts something it
shouldn't. Pinning it is one line.

Leave `ARC_HOST` alone. The tunnel connects to `127.0.0.1`; binding to
`0.0.0.0` additionally exposes ARC to everyone on the Wi-Fi, which is a
separate decision with no upside here.

## 3. Google Cloud Console

Under **APIs & Services → Credentials → your Web application client**, the
redirect URI must match exactly:

```
https://your-host.ts.net/oauth/callback
http://localhost:8420/oauth/callback
```

Add `http://localhost:8421/oauth/callback` too if you run the private instance.
A missing entry gives Google's `redirect_uri_mismatch` page, never a hint from
ARC.

**Publishing status matters more than it looks.** Under **OAuth consent
screen**:

| Status | What it means for you |
|---|---|
| **Testing** | Only listed test users can sign in, and **refresh tokens expire after 7 days** — everyone re-links weekly, including guests, with no warning beyond things quietly stopping |
| **In production** | Anyone you allow in `ARC_ALLOWED_EMAILS` signs in normally, tokens last. Requires nothing extra for a scope set like this one |

Sitting in Testing with guests is the single most common cause of "it worked
last week".

## 4. `.env`

Copy `env.example.txt` and set at least:

```ini
ANTHROPIC_API_KEY=...
ARC_SECRET=<64 random hex>       # or a restart mid-sign-in fails
ARC_ALLOWED_EMAILS=you@gmail.com
ARC_PUBLIC_URL=https://your-host.ts.net
```

`ARC_SECRET` signs the sign-in cookie. Unset, ARC generates a throwaway one at
startup, which means every restart invalidates every session.

## 5. Phone alerts

Without `ARC_NTFY_TOPIC`, alarms and reminders can only reach an open browser
tab. On Android that is a weak promise and on iPhone it is close to no promise
at all. Pick an unguessable topic name — anyone who knows it can read what is
pushed to it — install the ntfy app, subscribe:

```ini
ARC_NTFY_TOPIC=arc-<something long and random>
```

## 6. Starting it without a terminal

`launch-arc.vbs` starts `launch-arc.ps1` with no console window; shortcut it
into `shell:startup` and ARC comes up with the machine.
`launch-bella-private.ps1` does the same for the private instance on 8421 with
its own `ARC_DATA_DIR`.

---

## Check it actually works

Not "the page loaded". These are the things that have silently not worked:

1. **Sign in from the phone**, over the tunnel. Then check the cookie is
   `Secure` **and** `HttpOnly` — this can only be seen over HTTPS, and it was
   once wrong precisely there while being right on localhost.
2. **Sign in with an account that is not on the allowlist.** Expect the
   refusal page, no session cookie, and no file under `google_sessions/`.
3. **Set an alarm for two minutes' time**, lock the phone, and wait. This is
   the only way to find out whether it can actually wake you.
4. **Ask it something that uses the calendar**, so you know the Google token
   attached to the session, not just the sign-in, is live.
5. **Restart ARC** and confirm you are still signed in. If you are not,
   `ARC_SECRET` is unset or `sessions.json` is not writable.

## When something breaks

**Ask it first.** `Check ARC` in the panel, or *"are you okay"*, runs the same
checks the server runs at boot — background loop, data files, backups, disk,
voices, sign-ins — and names what is wrong in a sentence. It repairs only on a
second press, and only what it just printed. What it has already done on its
own is in `repairs.log` next to your data, with timestamps. Start there; the
entries below are the things it deliberately cannot fix for you.

**An alarm didn't ring and nothing else looks wrong.** Check `repairs.log` for
`watchdog: restarted my background loop`. That loop is what fires alarms,
reminders and price alerts, and before the watchdog existed it could die
without a trace. Repeated restarts in the log mean something is wedging it —
the server console will have the reason.

**Your notes or reminders came back short.** A restore puts back the newest
snapshot that parses, so anything added between that snapshot and the damage is
gone. The damaged original is in `backups/` as `<name>.damaged-<timestamp>`,
never deleted — it is usually mostly readable in a text editor.

**The change you made isn't there.** Check you are talking to the process you
think you are. A second `python run.py` fails to bind and exits silently while
the old one keeps serving, and every symptom says your edit did nothing:

```powershell
$c = Get-NetTCPConnection -LocalPort 8420 -State Listen
Get-Process -Id $c.OwningProcess | Select-Object Id, StartTime
```

If `StartTime` is older than the file you edited, that is your answer. Stop it
by PID rather than by name — the command line is a full path and `pkill`-style
matching misses it.

**"That Google account isn't allowed to use this ARC."** Google authenticated
them; `ARC_ALLOWED_EMAILS` did not. Add the address, restart, and check the
banner lists it. The comparison is lowercase and exact.

**Signed straight back out after signing in.** The session cookie is being
dropped. Check `ARC_SECRET` is set and that the cookie's `Max-Age` is not 0 —
`Max-Age=0` is not "no expiry", it is "delete this now".

**It answers on the desktop but not the phone.** Look at the server log rather
than the page: a 400 on `/api/chat` with "System prompt is too long" is the
prompt ceiling, not the network.

**Google says `redirect_uri_mismatch`.** The URI in the Console must match
`ARC_PUBLIC_URL` + `/oauth/callback` character for character, scheme and all.

---

## Rotating secrets

Anything that has been on screen, in a log, or in a screenshot is spent. To
rotate: change the value in `.env`, delete `token.json` and everything under
`google_sessions/`, restart, sign in again. Changing `ARC_SECRET` signs
everyone out by itself, which is the point of it.

## Updating

```powershell
git pull
pip install -r requirements.txt
python tests/run_all.py
```

Then restart. The suite runs against a temporary directory and cannot touch
your sessions, alarms or notes — see [tests/README.md](tests/README.md).
