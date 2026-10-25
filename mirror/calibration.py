"""Calibration: reliability table, Brier score, expected calibration error (ECE)."""


def reliability_table(pairs, bins=10):
    """pairs: iterable of (confidence 0-100, correct bool). Returns list of bucket dicts.

    Buckets are [0,10), [10,20), ... [90,100] (last bucket includes 100).
    """
    width = 100 / bins
    buckets = [
        {"lo": round(i * width, 6), "hi": round((i + 1) * width, 6), "n": 0, "conf_sum": 0.0, "correct": 0}
        for i in range(bins)
    ]
    for conf, ok in pairs:
        idx = min(int(conf // width), bins - 1)
        b = buckets[idx]
        b["n"] += 1
        b["conf_sum"] += conf
        b["correct"] += int(bool(ok))
    for b in buckets:
        n = b["n"]
        b["mean_confidence"] = b["conf_sum"] / n if n else None
        b["accuracy"] = 100 * b["correct"] / n if n else None
        b["gap"] = b["mean_confidence"] - b["accuracy"] if n else None  # >0 = overconfident
        del b["conf_sum"]
    return buckets


def brier_score(pairs):
    """Mean squared error of confidence (as probability) vs outcome. 0 = perfect."""
    pairs = list(pairs)
    if not pairs:
        return None
    return sum((c / 100 - (1.0 if ok else 0.0)) ** 2 for c, ok in pairs) / len(pairs)


def ece(pairs, bins=10):
    pairs = list(pairs)
    if not pairs:
        return None
    table = reliability_table(pairs, bins)
    return sum(b["n"] * abs(b["gap"]) for b in table if b["n"]) / len(pairs)
