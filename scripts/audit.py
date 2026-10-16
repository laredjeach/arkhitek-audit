#!/usr/bin/env python3
"""
audit.py — One-command audit entry point for Arkhitek Audit.

Point this at any website or CSV and get a full audit report + email draft.
No discovery pipeline needed. This is the core engine exposed directly.

Usage:
  python3 scripts/audit.py --url https://example.com
  python3 scripts/audit.py --domain lawdesai.com
  python3 scripts/audit.py --csv my-list.csv
  python3 scripts/audit.py --csv my-list.csv --no-email

Required env var:
  ANTHROPIC_API_KEY

Optional env vars:
  FIRECRAWL_API_KEY   richer scraping (multi-page)
  ANTHROPIC_MODEL     model override (default: claude-sonnet-4-6)
  SENDER_NAME         your name for outreach emails
  SENDER_COMPANY      your company name for outreach emails
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent


def run(script: str, extra_args: list[str]) -> int:
    cmd = [sys.executable, str(SCRIPTS_DIR / script)] + extra_args
    result = subprocess.run(cmd)
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit any website with Arkhitek Audit — full report + email draft.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Audit a single domain
  python3 scripts/audit.py --domain lawdesai.com

  # Audit a URL
  python3 scripts/audit.py --url https://avradental.com/

  # Audit a CSV list (any CSV with a URL column)
  python3 scripts/audit.py --csv prospect-list.csv

  # Audit without generating email drafts
  python3 scripts/audit.py --domain example.com --no-email

  # Audit a CSV, limit to 5, skip email
  python3 scripts/audit.py --csv my-list.csv --limit 5 --no-email

Outputs:
  runs/audits/{slug}-audit.md    Full audit report per company
  leads/prospects.csv            Updated with audit fields (CSV input only)
        """,
    )
    # Input
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--url", metavar="URL", help="Single URL to audit")
    input_group.add_argument("--domain", metavar="DOMAIN", help="Single domain to audit (e.g. example.com)")
    input_group.add_argument("--csv", metavar="PATH", help="CSV file with a URL column")

    # Options
    parser.add_argument("--limit", type=int, default=0, help="Max rows to audit from CSV (0 = all)")
    parser.add_argument("--no-email", action="store_true", help="Skip email draft generation (Leg 3)")
    parser.add_argument("--rerun-failed", action="store_true", help="Re-audit rows previously marked failed")
    parser.add_argument("--delay", type=float, default=2.0, help="Seconds between requests (default: 2.0)")
    parser.add_argument("--sender-name", default=None, help="Sender name for email drafts")
    parser.add_argument("--sender-company", default=None, help="Sender company for email drafts")
    args = parser.parse_args()

    # Build leg2 args
    leg2_args: list[str] = ["--delay", str(args.delay)]
    if args.url:
        leg2_args += ["--urls", args.url]
    elif args.domain:
        leg2_args += ["--domain", args.domain]
    elif args.csv:
        leg2_args += ["--csv", args.csv]
    if args.limit:
        leg2_args += ["--limit", str(args.limit)]
    if args.rerun_failed:
        leg2_args.append("--rerun-failed")

    print(f"\n[audit] Running audit engine...", file=sys.stderr)
    rc = run("leg2_audit.py", leg2_args)
    if rc != 0:
        print(f"[audit] Audit step failed (exit {rc}).", file=sys.stderr)
        return rc

    if args.no_email:
        print(f"\n[audit] Done. Reports in runs/audits/", file=sys.stderr)
        return 0

    # Only draft emails when we have a CSV (need a row to update)
    if not args.csv:
        print(f"\n[audit] Done. Reports in runs/audits/", file=sys.stderr)
        print(f"[audit] Tip: use --csv to also generate outreach email drafts.", file=sys.stderr)
        return 0

    print(f"\n[audit] Drafting outreach emails...", file=sys.stderr)
    leg3_args: list[str] = ["--csv", args.csv, "--delay", str(args.delay)]
    if args.limit:
        leg3_args += ["--limit", str(args.limit)]
    if args.sender_name:
        leg3_args += ["--sender-name", args.sender_name]
    if args.sender_company:
        leg3_args += ["--sender-company", args.sender_company]

    rc = run("leg3_draft.py", leg3_args)
    if rc != 0:
        print(f"[audit] Email draft step failed (exit {rc}).", file=sys.stderr)
        return rc

    print(f"\n[audit] Done. Reports in runs/audits/ · Email drafts updated in {args.csv}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
