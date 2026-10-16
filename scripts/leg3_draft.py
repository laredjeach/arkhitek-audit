#!/usr/bin/env python3
"""
Leg 3: Claude-powered outreach email drafting for Arkhitek.

Reads leads/prospects.csv where audit_status=complete and email_draft is empty.
Loads the matching niche lens and email template.
Calls Claude to write a personalized cold email.
Writes email_subject and email_body back to CSV.
Also writes a per-company outreach-email.md to runs/audits/{slug}-outreach.md.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import date
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PROSPECTS_CSV = REPO_ROOT / "leads" / "prospects.csv"
LENSES_DIR = REPO_ROOT / "lenses"
TEMPLATES_DIR = REPO_ROOT / "templates"
AUDITS_DIR = REPO_ROOT / "runs" / "audits"

ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

CSV_FIELDNAMES = [
    "business_name", "owner_or_contact", "website", "email", "phone",
    "city", "state", "niche_category", "source", "discovered_at",
    "score", "fit_score", "opportunity_score", "platform",
    "title", "meta_description", "h1_count", "script_count",
    "has_schema", "has_llms_txt", "issues",
    "audit_status", "audit_date", "geo_score", "gap_1", "gap_2", "gap_3",
    "tech_gap", "top_competitor", "rebuild_signal", "pitch_hook",
    "service_opportunities",
    "email_subject", "email_body", "draft_status", "email_date",
]

DRAFT_PROMPT = """You are writing a cold outreach email for Jared at Arkhitek, a small digital agency that helps local businesses improve their websites and get found on Google and AI search tools.

Rules:
- 3-4 sentences maximum in the email body. No more.
- Lead with ONE specific, visible finding from the site audit — not a generic "your SEO is bad."
- The second sentence explains the plain-English business impact (leads, visibility, trust).
- The third sentence offers to send the short audit report.
- NO bullet points. NO lists. Plain paragraph.
- Friendly but professional tone. Not salesy.
- Do NOT start with "I hope this email finds you well" or any filler.
- Do NOT use the word "optimize" or "SEO strategy."
- Subject line: max 8 words, specific to this business.

BUSINESS NAME: {business_name}
CONTACT FIRST NAME: {first_name}
NICHE: {niche}
PITCH HOOK (use this as the opening finding): {pitch_hook}
GAP 1: {gap_1}
GEO SCORE: {geo_score}/100

NICHE CONTEXT (use to inform tone and ROI framing, do not quote directly):
---
{lens}
---

EMAIL TEMPLATE STRUCTURE (follow this shape):
---
{template}
---

SENDER NAME: {sender_name}
SENDER COMPANY: {sender_company}

Respond with a JSON object:
{{
  "subject": "<email subject line>",
  "body": "<email body — plain text, no markdown>"
}}
"""

FOLLOWUP_PROMPT = """Write a short 2-sentence follow-up email. Reference the first email about the specific finding.
Add a second question or brief observation. Keep it under 50 words total.

BUSINESS: {business_name}
FIRST EMAIL SUBJECT: {subject}
PITCH HOOK: {pitch_hook}
GAP 2: {gap_2}
SENDER NAME: {sender_name}

Respond with a JSON object:
{{
  "subject": "<follow-up subject line using Re: or similar>",
  "body": "<follow-up body — 2 sentences max>"
}}
"""


def slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "company"


def first_name(full_name: str) -> str:
    """Extract first name or return empty string."""
    if not full_name:
        return ""
    clean = full_name.strip().split()[0].strip(".,;:")
    return clean if len(clean) > 1 else ""


def niche_slug(niche_category: str) -> str:
    """Map niche_category field to a lenses/ filename slug."""
    nc = niche_category.lower()
    if "personal injury" in nc or "injury lawyer" in nc or "law firm" in nc:
        return "personal-injury-lawyer"
    if "dental" in nc or "dentist" in nc or "orthodont" in nc:
        return "dental"
    if "med spa" in nc or "aesthetics" in nc or "medspa" in nc or "botox" in nc:
        return "med-spa"
    # Default
    return "general"


def load_lens(niche_category: str) -> str:
    slug = niche_slug(niche_category)
    lens_path = LENSES_DIR / f"{slug}.md"
    if not lens_path.exists():
        lens_path = LENSES_DIR / "general.md"
    if lens_path.exists():
        return lens_path.read_text(encoding="utf-8")
    return ""


def load_template() -> str:
    path = TEMPLATES_DIR / "cold-first-touch.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def call_claude(prompt: str, model: str) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    message = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def parse_json_from_claude(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def load_csv(csv_path: Path) -> list[dict]:
    if not csv_path.exists():
        return []
    with open(csv_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(csv_path: Path, rows: list[dict]) -> None:
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_outreach_file(slug: str, row: dict, subject: str, body: str, followup_subject: str, followup_body: str) -> None:
    AUDITS_DIR.mkdir(parents=True, exist_ok=True)
    path = AUDITS_DIR / f"{slug}-outreach.md"
    content = f"""# Outreach Email: {row.get('business_name', '')}

- Website: {row.get('website', '')}
- Score: {row.get('score', '')}
- Email found: {row.get('email', '') or '(none found)'}
- Pitch hook: {row.get('pitch_hook', '')}

## First Touch

**Subject:** {subject}

{body}

---

## Follow-up (Day 3)

**Subject:** {followup_subject}

{followup_body}
"""
    path.write_text(content, encoding="utf-8")


def draft_one(row: dict, model: str, sender_name: str, sender_company: str) -> dict:
    name = row.get("business_name", "")
    contact = row.get("owner_or_contact", "")
    fn = first_name(contact) or "[First Name]"
    niche = row.get("niche_category", "local service")
    pitch = row.get("pitch_hook", "") or row.get("gap_1", "")
    gap_1 = row.get("gap_1", "")
    gap_2 = row.get("gap_2", "")
    geo = row.get("geo_score", "")

    if not pitch:
        print(f"  [leg3] no pitch_hook for {name} — skipping", file=sys.stderr)
        return row

    lens = load_lens(niche)
    template = load_template()

    prompt = DRAFT_PROMPT.format(
        business_name=name,
        first_name=fn,
        niche=niche,
        pitch_hook=pitch,
        gap_1=gap_1,
        geo_score=geo,
        lens=lens[:3000] if lens else "(no lens loaded)",
        template=template[:1500] if template else "(use standard cold email structure)",
        sender_name=sender_name,
        sender_company=sender_company,
    )

    try:
        raw = call_claude(prompt, model)
        result = parse_json_from_claude(raw)
    except Exception as exc:
        print(f"  [leg3] Claude draft failed for {name}: {exc}", file=sys.stderr)
        return row

    subject = result.get("subject", "")
    body = result.get("body", "")

    # Follow-up
    followup_subject, followup_body = "", ""
    if subject and gap_2:
        fp = FOLLOWUP_PROMPT.format(
            business_name=name,
            subject=subject,
            pitch_hook=pitch,
            gap_2=gap_2,
            sender_name=sender_name,
        )
        try:
            fraw = call_claude(fp, model)
            fresult = parse_json_from_claude(fraw)
            followup_subject = fresult.get("subject", f"Re: {subject}")
            followup_body = fresult.get("body", "")
        except Exception:
            followup_subject = f"Re: {subject}"
            followup_body = ""

    slug = slugify(name)
    write_outreach_file(slug, row, subject, body, followup_subject, followup_body)

    row["email_subject"] = subject
    row["email_body"] = body
    row["draft_status"] = "ready"
    row["email_date"] = date.today().isoformat()
    return row


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Leg 3: Draft outreach emails for audited prospects using Claude.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Reads rows where audit_status=complete and draft_status is empty.
Writes email drafts back to CSV and to runs/audits/{slug}-outreach.md.

Required env var:
  ANTHROPIC_API_KEY

Optional env vars:
  ANTHROPIC_MODEL  model override (default: claude-sonnet-4-6)
  SENDER_NAME      sender name for email signatures (default: Jared)
  SENDER_COMPANY   sender company for email signatures (default: Arkhitek)

Examples:
  python3 scripts/leg3_draft.py
  python3 scripts/leg3_draft.py --limit 5
  python3 scripts/leg3_draft.py --sender-name "Jared" --sender-company "Arkhitek"
        """,
    )
    parser.add_argument("--limit", type=int, default=0, help="Max drafts to generate this run (0 = all pending)")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds between API calls (default: 1.0)")
    parser.add_argument("--sender-name", default=None, help="Sender name for email signatures")
    parser.add_argument("--sender-company", default=None, help="Sender company for email signatures")
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Error: ANTHROPIC_API_KEY environment variable is required.", file=sys.stderr)
        return 2

    try:
        import anthropic  # noqa: F401
    except ImportError:
        print("Error: anthropic package not installed. Run: pip install anthropic", file=sys.stderr)
        return 2

    sender_name = args.sender_name or os.environ.get("SENDER_NAME", "Jared")
    sender_company = args.sender_company or os.environ.get("SENDER_COMPANY", "Arkhitek")
    model = ANTHROPIC_MODEL

    rows = load_csv(PROSPECTS_CSV)
    if not rows:
        print("[leg3] No prospects found. Run leg1 and leg2 first.", file=sys.stderr)
        return 0

    pending = [
        r for r in rows
        if r.get("audit_status") == "complete" and not r.get("draft_status")
    ]

    if not pending:
        print("[leg3] No audited prospects without email drafts. Run leg2 first.", file=sys.stderr)
        return 0

    if args.limit > 0:
        pending = pending[: args.limit]

    print(f"[leg3] {len(pending)} prospects to draft emails for (model: {model})", file=sys.stderr)

    for idx, row in enumerate(pending, start=1):
        name = row.get("business_name", row.get("website", ""))
        email_addr = row.get("email", "")
        if not email_addr:
            print(f"[leg3] [{idx}/{len(pending)}] {name} — no email address found, drafting anyway", file=sys.stderr)
        else:
            print(f"[leg3] [{idx}/{len(pending)}] {name} ({email_addr})", file=sys.stderr)

        updated = draft_one(row, model, sender_name, sender_company)
        url = row.get("website", "")
        for i, r in enumerate(rows):
            if r.get("website") == url:
                rows[i] = updated
                break

        write_csv(PROSPECTS_CSV, rows)
        time.sleep(args.delay)

    ready = sum(1 for r in rows if r.get("draft_status") == "ready")
    print(f"\n[leg3] Done. {ready} email drafts ready in CSV.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
