import argparse
import datetime as dt
import json
import os
import sys

from .adapters import make_adapter
from .items import ItemError, load_items
from .report import format_text, load_logs, summarize
from .runner import run


def _cmd_run(args):
    items = load_items(args.items)
    if args.skip_examples:
        items = [it for it in items if not it.get("example")]
    if args.limit:
        items = items[: args.limit]
    if not items:
        print("no items to run", file=sys.stderr)
        return 1
    adapter = make_adapter(args.model, temperature=args.temperature, seed=args.seed)
    out = args.out or os.path.join(
        "logs", f"{dt.datetime.now():%Y%m%d-%H%M%S}-{adapter.name.replace(':', '_').replace('/', '_')}.jsonl"
    )
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)

    def progress(i, n, rec):
        label = rec["heuristic"]["label"] if rec.get("heuristic") else f"ERROR {rec['error']}"
        print(f"[{i}/{n}] {rec['item_id']}: {label}", file=sys.stderr)

    run_id, _ = run(adapter, items, out, progress=None if args.quiet else progress)
    print(f"run {run_id}: {len(items)} items -> {out}")
    return 0


def _cmd_report(args):
    summary = summarize(load_logs(args.logs), bins=args.bins)
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(format_text(summary))
    return 0


def _cmd_validate(args):
    items = load_items(args.items)
    n_ex = sum(1 for it in items if it.get("example"))
    print(f"{len(items)} items OK ({n_ex} marked example)")
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="python -m mirror", description="sycophancy-mirror runner")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run items against a model, append JSONL log")
    r.add_argument("items", help="items JSONL file")
    r.add_argument("--model", required=True, help="mock[:hold|flip|hedge|random|garbage] or openai:<model>")
    r.add_argument("--out", help="log file (default logs/<time>-<model>.jsonl); appended to")
    r.add_argument("--limit", type=int, default=0)
    r.add_argument("--temperature", type=float, default=0.0)
    r.add_argument("--seed", type=int, default=0, help="seed for mock:random")
    r.add_argument("--skip-examples", action="store_true", help="ignore items with example=true")
    r.add_argument("--quiet", action="store_true")
    r.set_defaults(func=_cmd_run)

    rep = sub.add_parser("report", help="summarise log files")
    rep.add_argument("logs", nargs="+")
    rep.add_argument("--bins", type=int, default=10)
    rep.add_argument("--json", action="store_true")
    rep.set_defaults(func=_cmd_report)

    v = sub.add_parser("validate", help="check an items file")
    v.add_argument("items")
    v.set_defaults(func=_cmd_validate)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ItemError, ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
