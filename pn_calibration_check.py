#!/usr/bin/env python3
"""Offline PN calibration check.

Scans audio/*.txt (examiner + system scores) and the matching
<name>_raw_data.json / <name>.json, builds a comparison table, computes
correlations vs examiner PN, and flags alignment-collapsed recordings.

No API calls. Run: python3 pn_calibration_check.py
"""
import json
import os
import re
import glob
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
AUDIO_DIR = os.path.join(ROOT, "audio")
sys.path.insert(0, os.path.join(ROOT, "app"))
from speechsuper import generate_speech_super_report_json  # noqa: E402


def _last_num(line):
    nums = re.findall(r"-?\d+\.?\d*", line)
    return float(nums[-1]) if nums else None


def parse_txt(path):
    """Return {'unmod':PN, 'boss':PN, 'exam':PN} parsed from a result txt."""
    secs = {"unmod": None, "boss": None, "exam": None}
    cur = None
    for raw in open(path, encoding="utf-8"):
        l = raw.strip()
        if not l:
            continue
        low = l.lower()
        if "無修改" in l:
            cur = "unmod"
            continue
        if "老闆" in l or "boss" in low:
            cur = "boss"
            continue
        if "examier" in low or "examiner" in low:
            cur = "exam"
            continue
        if low.startswith("pronunciation") and cur:
            v = _last_num(l)
            if v is not None:
                secs[cur] = v
    return secs


def collect():
    rows = []
    for txt in sorted(glob.glob(os.path.join(AUDIO_DIR, "*.txt"))):
        base = txt[:-4]
        rawp = base + "_raw_data.json"
        if not os.path.exists(rawp):
            print("NO raw for", os.path.basename(txt))
            continue
        secs = parse_txt(txt)
        r = json.load(open(rawp))["result"]
        # Regenerate the report fresh so anchor/intel always reflect live code.
        m = generate_speech_super_report_json(r)["metadata"]
        rows.append(
            {
                "name": os.path.basename(base)[:24],
                "examPN": secs["exam"],
                "anchor": m.get("pn_band_anchor"),
                "sysUnmod": secs["unmod"],
                "sysBoss": secs["boss"],
                "speed": r.get("speed"),
                "intel": m.get("clarity_intelligibility_pct"),
                "miss": m.get("phoneme_missing_rate"),
                "link": m.get("linking_rate"),
                "collapse": "COLLAPSE" if m.get("alignment_collapsed") else "",
            }
        )
    return rows


def pearson(xs, ys):
    pts = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    n = len(pts)
    if n < 2:
        return None
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    cov = sum((p[0] - mx) * (p[1] - my) for p in pts)
    vx = sum((p[0] - mx) ** 2 for p in pts) ** 0.5
    vy = sum((p[1] - my) ** 2 for p in pts) ** 0.5
    return round(cov / (vx * vy), 3) if vx * vy else None


def main():
    rows = collect()
    cols = ["name", "examPN", "anchor", "sysUnmod", "sysBoss", "speed",
            "intel", "miss", "link", "collapse"]
    print(" | ".join((f"{c:24}" if c == "name" else f"{c:>9}") for c in cols))
    for row in sorted(rows, key=lambda r: (r["examPN"] is None, r["examPN"] or 0)):
        print(" | ".join(
            (f"{row[c]:24}" if c == "name" else f"{str(row[c]):>9}") for c in cols))

    valid = [r for r in rows if not r["collapse"]]
    ex = [r["examPN"] for r in valid]
    print(f"\n=== correlation vs examiner PN (n={len(valid)}, collapsed excluded) ===")
    for label, key in [("anchor", "anchor"), ("intel", "intel"),
                       ("speed", "speed"), ("sysUnmod", "sysUnmod"),
                       ("sysBoss", "sysBoss")]:
        print(f"  {label:9} vs examiner: {pearson([r[key] for r in valid], ex)}")

    print("\n=== bias / MAE vs examiner ===")
    for key in ["anchor", "sysUnmod", "sysBoss"]:
        errs = [r[key] - r["examPN"] for r in valid
                if r[key] is not None and r["examPN"] is not None]
        if errs:
            mae = sum(abs(e) for e in errs) / len(errs)
            print(f"  {key:9}: MAE={mae:.2f}  bias={sum(errs)/len(errs):+.2f}  n={len(errs)}")


if __name__ == "__main__":
    main()
