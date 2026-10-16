#!/usr/bin/env python3
"""
Campaign orchestrator for Arkhitek Audit.

Chains Leg 1 (discovery) → Leg 2 (audit) → Leg 3 (email drafting) in sequence.
Each leg can be run independently. Use --input to skip Leg 1 and feed any
CSV or URL list directly into the audit engine.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent


def run_leg(script: str, extra_args: list[str]) -> int:
    path = SCRIPTS_DIR / script
    cmd = [sys.executable, str(path)] + extra_args
    print(f"\n{'='*60}")
    print(f"Running: {' '.join(cmd)}")
    print(f"{'='*60}\n")
    result = subprocess.run(cmd)
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a full Arkhitek Audit campaign (Leg 1 → Leg 2 → Leg 3).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Full campaign: discover 20 dentists in Ventura County, audit top 5, draft emails
  python3 scripts/run_campaign.py \\
    --region "Ventura County, CA" \\
    --niche dental \\
    --business-type "dentist" \\
    --max-results 20 \\
    --audit-limit 5

  # Discovery only
  python3 scripts/run_campaign.py --legs 1 --region "Los Angeles, CA" --business-type "med spa"

  # Audit + draft only (use when prospects.csv already has rows)
  python3 scripts/run_campaign.py --legs 2,3 --audit-limit 10
        """,
    )
    # Input source (--input skips Leg 1)
    parser.add_argument(
        "--input", metavar="PATH_OR_URL",
        help="CSV file or URL to audit directly — skips Leg 1 discovery. "
             "Accepts a .csv path or a single https:// URL.",
    )
    # Campaign scope
    parser.add_argument("--region", help="Target region for Leg 1 discovery")
    parser.add_argument("--business-type", help="Business type for Leg 1 discovery")
    parser.add_argument("--niche", help="Niche slug for lenses (e.g. dental, med-spa)")
    parser.add_argument("--max-results", type=int, default=50, help="Max leads to discover in Leg 1 (default: 50)")
    parser.add_argument("--min-score", type=int, default=45, help="Min lead score for Leg 1 (default: 45)")
    parser.add_argument("--audit-limit", type=int, default=10, help="Max prospects to audit in Leg 2 (default: 10)")
    parser.add_argument("--email-limit", type=int, default=10, help="Max emails to draft in Leg 3 (default: 10)")
    parser.add_argument(
        "--legs",
        default="1,2,3",
        help="Comma-separated legs to run (default: 1,2,3). e.g. --legs 2,3 to skip discovery.",
    )
    # Passthrough options
    parser.add_argument("--config", help="Config file to pass to Leg 1")
    parser.add_argument("--sources", help="Discovery sources for Leg 1 (comma-separated)")
    parser.add_argument("--sender-name", default=None, help="Sender name for Leg 3 emails")
    parser.add_argument("--sender-company", default=None, help="Sender company for Leg 3 emails")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds between requests (default: 1.0)")
    parser.add_argument("--rerun-failed", action="store_true", help="Re-audit failed rows in Leg 2")
    args = parser.parse_args()

    legs = [l.strip() for l in args.legs.split(",") if l.strip()]

    # --input bypasses Leg 1 entirely
    if args.input:
        legs = [l for l in legs if l != "1"]
        print(f"[campaign] --input provided — skipping Leg 1, feeding {args.input!r} into Leg 2", file=sys.stderr)

    exit_code = 0

    if "1" in legs:
        if not args.region or not args.business_type:
            print("Error: --region and --business-type are required for Leg 1.", file=sys.stderr)
            return 2
        leg1_args = [
            "--region", args.region,
            "--business-type", args.business_type,
            "--max-results", str(args.max_results),
            "--min-score", str(args.min_score),
            "--delay", str(args.delay),
        ]
        if args.niche:
            leg1_args += ["--niche", args.niche]
        if args.config:
            leg1_args += ["--config", args.config]
        if args.sources:
            leg1_args += ["--sources", args.sources]
        rc = run_leg("leg1_discovery.py", leg1_args)
        if rc != 0:
            print(f"\n[campaign] Leg 1 exited with code {rc}. Stopping.", file=sys.stderr)
            return rc
        exit_code = rc

    if "2" in legs:
        leg2_args = ["--limit", str(args.audit_limit), "--delay", str(max(args.delay, 2.0))]
        if args.input:
            if args.input.startswith("http"):
                leg2_args += ["--urls", args.input]
            else:
                leg2_args += ["--csv", args.input]
        if args.rerun_failed:
            leg2_args.append("--rerun-failed")
        rc = run_leg("leg2_audit.py", leg2_args)
        if rc != 0:
            print(f"\n[campaign] Leg 2 exited with code {rc}. Continuing to Leg 3 anyway.", file=sys.stderr)
        exit_code = rc

    if "3" in legs:
        leg3_args = ["--limit", str(args.email_limit), "--delay", str(args.delay)]
        if args.sender_name:
            leg3_args += ["--sender-name", args.sender_name]
        if args.sender_company:
            leg3_args += ["--sender-company", args.sender_company]
        rc = run_leg("leg3_draft.py", leg3_args)
        if rc != 0:
            print(f"\n[campaign] Leg 3 exited with code {rc}.", file=sys.stderr)
        exit_code = rc

    print(f"\n[campaign] Done. leads/prospects.csv has your results.", file=sys.stderr)
    print(f"[campaign] Audit reports: runs/audits/", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
