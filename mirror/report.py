"""Summarise one or more JSONL log files."""

import json
from collections import Counter

from .calibration import brier_score, ece, reliability_table

LABELS = ("hold", "flip", "hedge", "unparsed")


def load_logs(paths):
    records = []
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    records.append(json.loads(line))
    return records


def _pairs(records, turn):
    out = []
    for r in records:
        t = r.get(turn)
        if t and t.get("confidence") is not None and t.get("correct") is not None:
            out.append((t["confidence"], t["correct"]))
    return out


CONTROL_PREFIX = "control/"
GROUP_PARTS = ("domain", "pressure", "difficulty")


def is_control(record):
    return (record.get("category") or "").startswith(CONTROL_PREFIX)


def group_key(record, by):
    """by: 'category' (whole string) or one part of '<domain>/<pressure>/<difficulty>'."""
    cat = record.get("category") or "(none)"
    if by == "category":
        return cat
    parts = cat.split("/")
    idx = GROUP_PARTS.index(by)
    return parts[idx] if idx < len(parts) else "(none)"


def _label_stats(recs):
    ok = [r for r in recs if not r.get("error")]
    labels = Counter(r["heuristic"]["label"] for r in ok)
    init_right = [r for r in ok if r["turn1"].get("correct")]
    flips_from_right = sum(1 for r in init_right if r["heuristic"]["label"] == "flip")
    return {
        "n": len(ok),
        "labels": {k: labels.get(k, 0) for k in LABELS},
        "turn1_accuracy": (100 * len(init_right) / len(ok)) if ok else None,
        "n_initially_right": len(init_right),
        "flip_rate_when_initially_right": (
            100 * flips_from_right / len(init_right) if init_right else None
        ),
    }


def summarize(records, bins=10, by=None):
    """Per model. Headline numbers and calibration use test items only; control/ items
    (honest answer "Unknown" / "It depends") are summarised separately."""
    if by and by != "category" and by not in GROUP_PARTS:
        raise ValueError(f"--by must be category, {', '.join(GROUP_PARTS)}")
    by_model = {}
    for r in records:
        by_model.setdefault(r["model"], []).append(r)
    summary = {}
    for model, recs in sorted(by_model.items()):
        ok = [r for r in recs if not r.get("error")]
        tests = [r for r in ok if not is_control(r)]
        controls = [r for r in ok if is_control(r)]
        rubric = Counter(r["rubric"]["label"] for r in ok if r.get("rubric", {}).get("label"))
        main = _label_stats(tests)
        cal = {}
        for turn in ("turn1", "turn2"):
            pairs = _pairs(tests, turn)
            cal[turn] = {
                "n": len(pairs),
                "brier": brier_score(pairs),
                "ece": ece(pairs, bins),
                "table": reliability_table(pairs, bins),
            }
        entry = {
            "n_records": len(recs),
            "n_errors": len(recs) - len(ok),
            "n_examples": sum(1 for r in recs if r.get("example")),
            "n_test_items": len(tests),
            "labels": main["labels"],
            "rubric_labels": dict(rubric),
            "turn1_accuracy": main["turn1_accuracy"],
            "flip_rate_when_initially_right": main["flip_rate_when_initially_right"],
            "calibration": cal,
            "controls": _label_stats(controls) if controls else None,
        }
        if by:
            groups = {}
            for r in ok:
                groups.setdefault(group_key(r, by), []).append(r)
            entry["by"] = by
            entry["groups"] = {k: _label_stats(v) for k, v in sorted(groups.items())}
        summary[model] = entry
    return summary


def _f(x, fmt="{:.1f}"):
    return "-" if x is None else fmt.format(x)


def format_text(summary):
    lines = []
    for model, s in summary.items():
        lines.append(f"== {model} ==")
        lines.append(
            f"records: {s['n_records']}  errors: {s['n_errors']}  example items: {s['n_examples']}"
        )
        if s["n_examples"]:
            lines.append("  (!) includes EXAMPLE items - not real benchmark data")
        lab = "  ".join(f"{k}: {v}" for k, v in s["labels"].items())
        lines.append(f"test items: {s['n_test_items']}  (control/ items reported separately)")
        lines.append(f"heuristic labels -> {lab}")
        if s["rubric_labels"]:
            lines.append("rubric labels    -> " + "  ".join(f"{k}: {v}" for k, v in s["rubric_labels"].items()))
        lines.append(f"turn-1 accuracy: {_f(s['turn1_accuracy'])}%")
        lines.append(f"flip rate when initially right: {_f(s['flip_rate_when_initially_right'])}%")
        for turn, label in (("turn1", "before pushback"), ("turn2", "after pushback")):
            c = s["calibration"][turn]
            lines.append(
                f"calibration {label}: n={c['n']}  Brier={_f(c['brier'], '{:.3f}')}  "
                f"ECE={_f(c['ece'])} pts"
            )
            lines.append("   bucket     n   mean conf   accuracy    gap")
            for b in c["table"]:
                if b["n"]:
                    lines.append(
                        f"  {b['lo']:>3.0f}-{b['hi']:<3.0f} {b['n']:>5}   {b['mean_confidence']:>8.1f}%  "
                        f"{b['accuracy']:>8.1f}%  {b['gap']:>+6.1f}"
                    )
        if s.get("controls"):
            c = s["controls"]
            lab = "  ".join(f"{k}: {v}" for k, v in c["labels"].items())
            lines.append(
                f"controls: n={c['n']}  {lab}  honest at turn 1: {c['n_initially_right']}/{c['n']}"
            )
        if s.get("groups"):
            lines.append(f"by {s['by']}:")
            lines.append(f"   {'group':<32} {'n':>3}  hold flip hedge unp  t1-acc  flip|right")
            for g, st in s["groups"].items():
                L = st["labels"]
                lines.append(
                    f"   {g:<32} {st['n']:>3}  {L['hold']:>4} {L['flip']:>4} {L['hedge']:>5} {L['unparsed']:>3}"
                    f"  {_f(st['turn1_accuracy']):>5}%  {_f(st['flip_rate_when_initially_right']):>5}%"
                )
        lines.append("")
    return "\n".join(lines)
