import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir, os.pardir))
import pc

print(pc.list_monitors())
print()


def summarise(sel):
    r = pc.screenshot(sel)
    if not isinstance(r, list):
        print("  %-9s -> ERROR %r" % (sel, r))
        return
    imgs = [b for b in r if b["type"] == "image"]
    txts = [b for b in r if b["type"] == "text"]
    kb = sum(len(b["source"]["data"]) for b in imgs) // 1024
    print("  %-9s -> %d image(s), ~%d KB base64" % (sel, len(imgs), kb))
    for t in txts:
        print("       " + t["text"][:170])


for s in ("primary", "2", "each", "all"):
    summarise(s)

print()
bad = pc.screenshot("7")
print("bad monitor ->", (bad[0] if isinstance(bad, tuple) else bad)[:70])

print()
print("watch gate, 1st (baseline):", pc.screen_changed())
print("watch gate, 2nd (idle)    :", pc.screen_changed())
