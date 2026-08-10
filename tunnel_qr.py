#!/usr/bin/env python3
"""
Start the Cloudflare tunnel AND show a QR code for the address it hands out.

A quick tunnel's URL changes every time it starts, so a saved QR would go
stale. This launches cloudflared, watches its output for the trycloudflare
address, then (1) prints a scannable QR right here in the window and (2) saves
and opens phone-qr.png. Point your phone's camera at either one, log in, and
Add to Home Screen.

Keep this window open while you use ARC on your phone. Close it to stop
exposing ARC to the internet.
"""

import os
import re
import sys
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
CLOUDFLARED = ROOT / "cloudflared.exe"
QR_PNG = ROOT / "phone-qr.png"
PORT = os.getenv("ARC_PORT", "8420")

try:
    import segno
except ImportError:
    print("Installing the QR library (one time) ...")
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "segno"])
    import segno


def show_qr(url: str) -> None:
    qr = segno.make(url, error="m")
    # A QR you can scan straight off the screen.
    try:
        qr.terminal(compact=True)
    except Exception:
        pass
    # ...and a PNG, opened in the default image viewer as a fallback.
    try:
        qr.save(str(QR_PNG), scale=8, border=3)
        os.startfile(str(QR_PNG))  # Windows
    except Exception:
        pass
    print("\n  " + "-" * 66)
    print("  Scan the QR above with your phone camera, or open this on the phone:")
    print("  " + url)
    print("  Then log in (J.A.R.V.I.S) and 'Add to Home screen'. Allow the mic.")
    print("  " + "-" * 66 + "\n")


def main() -> int:
    if not CLOUDFLARED.exists():
        print(f"Can't find cloudflared.exe next to this script ({CLOUDFLARED}).")
        return 1

    print("Starting a public HTTPS tunnel to ARC (port %s) ...\n" % PORT)
    # --protocol http2 forces the tunnel over TCP/443-style HTTP/2 instead of
    # QUIC (UDP 7844). Many home/school/work networks block outbound UDP 7844,
    # which makes the default QUIC transport fail its precheck; HTTP/2 gets
    # through those. Slightly higher latency, but it connects.
    proc = subprocess.Popen(
        [str(CLOUDFLARED), "tunnel", "--protocol", "http2",
         "--url", f"http://localhost:{PORT}"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )

    pat = re.compile(r"https://[-a-z0-9]+\.trycloudflare\.com", re.I)
    shown = False
    try:
        for line in proc.stdout:                      # mirror cloudflared's own log
            sys.stdout.write(line)
            sys.stdout.flush()
            if not shown:
                m = pat.search(line)
                if m:
                    shown = True
                    print()
                    show_qr(m.group(0))
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
    finally:
        try:
            proc.terminate()
        except Exception:
            pass
    print("\nTunnel stopped. ARC is no longer reachable from the internet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
