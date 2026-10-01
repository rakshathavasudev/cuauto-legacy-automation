"""Scripted stand-in for a human operator, used by the demo and e2e tests.

It uses exactly the operator path a person would: wait for an open intervention,
claim the live session (lease -> human), act on it over CDP, hand control back.
Usage: python scripts/operator_bot.py --as alice --fill "Override Code=2468" --click Authorize
"""
from __future__ import annotations

import argparse
import sys
import time

from cuauto.handoff.act import parse_ops, perform
from cuauto.handoff.operator import claim, release
from cuauto.runtime import open_repo
from cuauto.settings import load_settings


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--as", dest="op", default="alice")
    p.add_argument("--click", action="append", default=[])
    p.add_argument("--fill", action="append", default=[])
    p.add_argument("--resolution", default="resume")
    p.add_argument("--wait", type=int, default=90)
    a = p.parse_args()
    repo = open_repo(load_settings())
    deadline = time.monotonic() + a.wait
    while time.monotonic() < deadline:
        open_ = [i for i in repo.list_interventions(("open",))]
        if open_:
            iv = open_[0]
            print(f"[operator {a.op}] saw {iv['id']} ({iv['kind']}): {iv['reason']}", file=sys.stderr)
            time.sleep(1.0)  # "reading the context"
            if a.click or a.fill:
                claim(repo, iv["id"], a.op)
                print(f"[operator {a.op}] claimed live session {iv['session_endpoint']}", file=sys.stderr)
                done = perform(repo, iv["id"], a.op, parse_ops(a.click, a.fill, []))
                print(f"[operator {a.op}] performed {done}", file=sys.stderr)
            release(repo, iv["id"], a.op, a.resolution, "operator bot")
            print(f"[operator {a.op}] handed back with '{a.resolution}'", file=sys.stderr)
            return 0
        time.sleep(0.5)
    print("[operator] no intervention appeared", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
