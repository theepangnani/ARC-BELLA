import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir, os.pardir))
import pc

print("monitors:", len(pc._list_monitors()))
print()

print("-- each (default) --")
print("  baseline:", pc.screen_changed())
print("  idle    :", pc.screen_changed())
print("  frames held:", len(pc._watch_prev))

print()
print("-- switching to primary mid-run must re-baseline, not fire --")
r = pc.screen_changed(monitor="primary")
print("  first primary call:", r, "<- must be changed=False")
print("  frames held:", len(pc._watch_prev))
print("  idle primary      :", pc.screen_changed(monitor="primary"))

print()
print("-- and back to each --")
r = pc.screen_changed(monitor="each")
print("  first each call   :", r, "<- must be changed=False")
print("  frames held:", len(pc._watch_prev))

print()
print("-- a real change on the SECONDARY must trip the 'each' gate --")
try:
    from PIL import ImageGrab, ImageDraw
    mons = pc._list_monitors()
    if len(mons) > 1:
        pc.screen_changed(monitor="each")           # baseline
        # Simulate: overwrite the stored secondary thumbnail with a blank one,
        # so the next real grab differs sharply on that monitor only.
        prev = list(pc._watch_prev)
        prev[1] = bytes(len(prev[1]))               # secondary goes black
        pc._watch_prev = prev
        print("  each gate  :", pc.screen_changed(monitor="each"), "<- must be changed=True")

        # The same divergence must be INVISIBLE to a primary-only gate.
        pc.screen_changed(monitor="primary")        # baseline primary
        prev = list(pc._watch_prev)
        print("  primary holds %d frame(s) - secondary changes cannot reach it" % len(prev))
    else:
        print("  (only one monitor; skipped)")
except Exception as e:
    print("  error:", e)
