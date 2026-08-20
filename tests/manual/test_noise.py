import sys, time
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir, os.pardir))
import pc
from PIL import ImageGrab

mons = pc._list_monitors()
print("monitors:", len(mons))

# Per-monitor scores, so we can see WHICH screen is noisy rather than only the max.
prev = None
print("\nsample   per-monitor score        max     would fire?")
for i in range(8):
    frames = [ImageGrab.grab(bbox=b, all_screens=True).convert("L").resize((64, 36)).tobytes()
              for b in mons]
    if prev:
        scores = [pc._thumb_diff(p, c) for p, c in zip(prev, frames)]
        mx = max(scores)
        print("  %d      %s   %.4f   %s"
              % (i, "  ".join("%.4f" % s for s in scores), mx,
                 "YES" if mx >= 0.035 else "no"))
    prev = frames
    time.sleep(1.5)
