#!/usr/bin/env python3
"""
Leg 2: Deep site audit engine for Arkhitek.

Accepts any of:
  --csv PATH       Any CSV with a 'website' column (or auto-detected URL column)
  --urls u1 u2 …   One or more URLs directly on the command line
  --domain DOMAIN  Single domain to audit
  (no flag)        Defaults to leads/prospects.csv

Scrapes each site (Firecrawl primary, requests fallback), calls Claude for
structured analysis and a narrative report, writes per-company audit reports
to runs/audits/, and updates the CSV in place.

Claude output fields added to CSV:
  geo_score, gap_1, gap_2, gap_3, tech_gap, top_competitor,
  rebuild_signal, pitch_hook, service_opportunities,
  audit_status, audit_date
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

AUDIT_PROMPT = """You are a senior website analyst at Arkhitek, a digital agency that helps small businesses improve their websites, connect their tools, and put AI to practical use.

You are reviewing a prospective client's website. Your job is to produce a structured analysis that will be used to:
1. Write a personalized outreach email with a specific, credible finding
2. Determine how strong a rebuild/services opportunity this is
3. Identify which Arkhitek service areas apply

BUSINESS: {business_name}
NICHE: {niche}
WEBSITE: {website}

SCRAPED CONTENT:
---
{content}
---

Respond ONLY with a valid JSON object (no markdown, no explanation) with exactly these keys:

{{
  "geo_score": <integer 0-100, overall AI/GEO visibility score>,
  "gap_1": "<most critical finding — specific, plain English, one sentence>",
  "gap_2": "<second most critical finding — specific, plain English, one sentence>",
  "gap_3": "<third finding — specific, plain English, one sentence>",
  "tech_gap": "<one of: Severe | Major | Moderate | Minor — overall technical debt level>",
  "top_competitor": "<name of an obvious competitor visible in content, or empty string>",
  "rebuild_signal": "<one of: strong | possible | low — how strong is the case for a rebuild or major redesign>",
  "pitch_hook": "<one sentence starting with a specific visible problem — e.g. 'Your homepage title ends mid-sentence, which Google shows in search results today.' NOT generic.>",
  "service_opportunities": "<comma-separated list of up to 3 from: website-rebuild, seo, local-seo, ai-visibility, schema, performance, conversion, content, email-marketing, ai-assistant, business-knowledge>"
}}

Scoring guidance for geo_score:
- 80-100: llms.txt present, strong schema, FAQ content, clear entity
- 60-79: some schema, decent content, missing llms.txt or FAQ
- 40-59: minimal schema, thin content, poor local signals
- 0-39: no schema, no local content, invisible to AI search

For tech_gap:
- Severe: outdated platform (pre-2018 WP, Flash, frames), major broken elements, no HTTPS
- Major: modern platform but significant structural issues (no schema, broken H1s, heavy script load)
- Moderate: functional site with several fixable gaps
- Minor: well-built site with polish opportunities

For rebuild_signal:
- strong: platform is limiting (old Wix, Squarespace with no flexibility), visual design clearly outdated, or site structure fundamentally wrong
- possible: modern platform but substantial content/structural work needed
- low: site is reasonably functional, focus on SEO/schema/content improvements
"""

AUDIT_REPORT_TEMPLATE = """# {business_name} — Website Audit Report
**Prepared by:** Arkhitek
**Date:** {audit_date}
**Site:** {website}
**Platform:** {platform}
**GEO / AI Visibility Score:** {geo_score}/100

---

## Executive Summary

{executive_summary}

**Overall Technical Gap: {tech_gap}**
**Rebuild Signal: {rebuild_signal}**

---

## What's Working

{whats_working}

---

## Key Opportunities

### Critical / High Priority

{critical_findings}

---

### Quick Wins

{quick_wins}

---

## What We'd Do First

{roadmap}

---

## Service Opportunities

{service_opps}

---

## About This Audit

This audit was conducted by Arkhitek on {audit_date} using automated scraping and AI analysis. A full Core Web Vitals review, Search Console crawl-coverage analysis, and manual UX walkthrough would be included as part of a paid engagement.

Questions? Reply to this email or reach out directly — happy to walk through any of the above on a 20-minute call.
"""

NARRATIVE_PROMPT = """You are writing a professional website audit report for Arkhitek, a digital agency.

BUSINESS: {business_name}
WEBSITE: {website}
NICHE: {niche}
PLATFORM: {platform}
GEO SCORE: {geo_score}/100
TECH GAP: {tech_gap}
REBUILD SIGNAL: {rebuild_signal}

KEY FINDINGS:
- Gap 1: {gap_1}
- Gap 2: {gap_2}
- Gap 3: {gap_3}

SERVICE OPPORTUNITIES: {service_opportunities}

SCRAPED CONTENT EXCERPT:
---
{content_excerpt}
---

Write the following sections for the audit report. Be specific, professional, and match the tone of the Anand Desai Law audit (see style: executive summary acknowledges what's working first, then leads with the most visible problem, uses red/orange/yellow/green priority system, ends with a concrete 30-60 day roadmap).

Respond with a JSON object with these keys:
{{
  "executive_summary": "<2-3 sentences: acknowledge strengths, name the biggest opportunity>",
  "whats_working": "<bullet list in markdown of 2-4 genuine positives visible in the site content>",
  "critical_findings": "<markdown: 2-3 findings with 🔴 Critical or 🟠 High Priority labels, specific impact, and fix description>",
  "quick_wins": "<markdown: bullet list of 3-5 fast, specific fixes that would make an immediate difference>",
  "roadmap": "<markdown: Week 1 / Week 2-4 / Month 2-3 roadmap referencing the specific findings>"
}}
"""


def slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "company"


def scrape_firecrawl(url: str, api_key: str) -> str:
    """Scrape homepage + key pages using Firecrawl, return combined markdown."""
    try:
        from firecrawl import FirecrawlApp
        app = FirecrawlApp(api_key=api_key)
        pages_to_try = [url]
        # Try common key pages
        base = url.rstrip("/")
        for path in ["/about", "/services", "/contact"]:
            pages_to_try.append(base + path + "/")

        combined = []
        for page_url in pages_to_try[:4]:
            try:
                result = app.scrape_url(page_url, params={"formats": ["markdown"]})
                md = ""
                if isinstance(result, dict):
                    md = result.get("markdown") or result.get("content") or ""
                elif hasattr(result, "markdown"):
                    md = result.markdown or ""
                if md:
                    combined.append(f"## {page_url}\n\n{md[:8000]}")
            except Exception:
                pass
        return "\n\n---\n\n".join(combined) if combined else ""
    except ImportError:
        return ""


def scrape_requests(url: str) -> str:
    """Fallback scraping using requests + beautifulsoup."""
    try:
        import requests
        from bs4 import BeautifulSoup
        headers = {"User-Agent": "Mozilla/5.0 (compatible; ArkhitekAudit/1.0; +https://arkhitek.earth)"}
        resp = requests.get(url, headers=headers, timeout=20)
        soup = BeautifulSoup(resp.text, "html.parser")
        # Remove script/style noise
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        return text[:12000]
    except Exception as exc:
        print(f"[leg2] requests scrape failed for {url}: {exc}", file=sys.stderr)
        return ""


def call_claude(prompt: str, model: str) -> str:
    """Call Claude API, return text response."""
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    message = client.messages.create(
        model=model,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def parse_json_from_claude(text: str) -> dict:
    """Extract JSON from Claude response, handling markdown code fences."""
    text = text.strip()
    # Strip markdown code fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def load_csv(csv_path: Path) -> list[dict]:
    if not csv_path.exists():
        return []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def write_csv(csv_path: Path, rows: list[dict]) -> None:
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_audit_report(slug: str, report: str) -> Path:
    AUDITS_DIR.mkdir(parents=True, exist_ok=True)
    path = AUDITS_DIR / f"{slug}-audit.md"
    path.write_text(report, encoding="utf-8")
    return path


def audit_one(row: dict, model: str, fc_key: str) -> dict:
    url = row.get("website", "").strip()
    name = row.get("business_name", url)
    niche = row.get("niche_category", "local service")
    platform = row.get("platform", "unknown")
    print(f"  [leg2] scraping {url} ...", file=sys.stderr)

    # Scrape
    content = ""
    if fc_key:
        content = scrape_firecrawl(url, fc_key)
    if not content:
        content = scrape_requests(url)
    if not content:
        print(f"  [leg2] no content for {url} — marking failed", file=sys.stderr)
        row["audit_status"] = "failed"
        row["audit_date"] = date.today().isoformat()
        return row

    # Step 1: structured JSON analysis
    prompt1 = AUDIT_PROMPT.format(
        business_name=name,
        niche=niche,
        website=url,
        content=content[:15000],
    )
    try:
        raw1 = call_claude(prompt1, model)
        analysis = parse_json_from_claude(raw1)
    except Exception as exc:
        print(f"  [leg2] Claude analysis failed for {url}: {exc}", file=sys.stderr)
        row["audit_status"] = "failed"
        row["audit_date"] = date.today().isoformat()
        return row

    # Step 2: narrative report sections
    prompt2 = NARRATIVE_PROMPT.format(
        business_name=name,
        website=url,
        niche=niche,
        platform=platform,
        geo_score=analysis.get("geo_score", ""),
        tech_gap=analysis.get("tech_gap", ""),
        rebuild_signal=analysis.get("rebuild_signal", ""),
        gap_1=analysis.get("gap_1", ""),
        gap_2=analysis.get("gap_2", ""),
        gap_3=analysis.get("gap_3", ""),
        service_opportunities=analysis.get("service_opportunities", ""),
        content_excerpt=content[:5000],
    )
    try:
        raw2 = call_claude(prompt2, model)
        narrative = parse_json_from_claude(raw2)
    except Exception as exc:
        print(f"  [leg2] Claude narrative failed for {url}: {exc}", file=sys.stderr)
        narrative = {
            "executive_summary": f"Audit completed. Key finding: {analysis.get('gap_1', '')}",
            "whats_working": "- Site is live and accessible.",
            "critical_findings": f"**Finding 1:** {analysis.get('gap_1', '')}\n\n**Finding 2:** {analysis.get('gap_2', '')}",
            "quick_wins": f"- Address: {analysis.get('gap_1', '')}",
            "roadmap": "**Week 1:** Address critical findings.\n\n**Week 2-4:** Implement quick wins.",
        }

    audit_date = date.today().isoformat()
    service_opps = analysis.get("service_opportunities", "")

    report = AUDIT_REPORT_TEMPLATE.format(
        business_name=name,
        audit_date=audit_date,
        website=url,
        platform=platform,
        geo_score=analysis.get("geo_score", ""),
        tech_gap=analysis.get("tech_gap", ""),
        rebuild_signal=analysis.get("rebuild_signal", ""),
        executive_summary=narrative.get("executive_summary", ""),
        whats_working=narrative.get("whats_working", ""),
        critical_findings=narrative.get("critical_findings", ""),
        quick_wins=narrative.get("quick_wins", ""),
        roadmap=narrative.get("roadmap", ""),
        service_opps="\n".join(f"- {s.strip()}" for s in service_opps.split(",") if s.strip()),
    )

    slug = slugify(name)
    audit_path = write_audit_report(slug, report)
    print(f"  [leg2] wrote audit to {audit_path}", file=sys.stderr)

    # Update row
    row["geo_score"] = str(analysis.get("geo_score", ""))
    row["gap_1"] = analysis.get("gap_1", "")
    row["gap_2"] = analysis.get("gap_2", "")
    row["gap_3"] = analysis.get("gap_3", "")
    row["tech_gap"] = analysis.get("tech_gap", "")
    row["top_competitor"] = analysis.get("top_competitor", "")
    row["rebuild_signal"] = analysis.get("rebuild_signal", "")
    row["pitch_hook"] = analysis.get("pitch_hook", "")
    row["service_opportunities"] = service_opps
    row["audit_status"] = "complete"
    row["audit_date"] = audit_date
    return row


def rows_from_args(args: argparse.Namespace) -> tuple[list[dict], Path | None]:
    """Return (rows_to_audit, csv_path_or_None). csv_path is set when we should write back."""
    if args.urls:
        rows = [{"website": u.strip(), "business_name": "", "niche_category": ""} for u in args.urls]
        return rows, None
    if args.domain:
        domain = args.domain.strip()
        if not domain.startswith("http"):
            domain = "https://" + domain
        rows = [{"website": domain, "business_name": "", "niche_category": ""}]
        return rows, None

    csv_path = Path(args.csv) if args.csv else PROSPECTS_CSV
    rows = load_csv(csv_path)
    if not rows:
        return [], csv_path

    # Auto-detect URL column if 'website' not present
    if rows and "website" not in rows[0]:
        url_col = next(
            (k for k in rows[0] if any(s in k.lower() for s in ("url", "website", "domain", "site"))),
            None,
        )
        if url_col:
            for r in rows:
                r["website"] = r[url_col]
        else:
            print(f"[leg2] Could not find a URL column in {csv_path}. Columns: {list(rows[0].keys())}", file=sys.stderr)
            return [], csv_path

    return rows, csv_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit any website with Firecrawl + Claude. Points at any data source.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Input (choose one — defaults to leads/prospects.csv):
  --csv PATH        Any CSV with a 'website' or URL column
  --urls URL …      One or more URLs directly
  --domain DOMAIN   Single domain to audit

Required env var:
  ANTHROPIC_API_KEY

Optional env vars:
  FIRECRAWL_API_KEY  for richer scraping
  ANTHROPIC_MODEL    model override (default: claude-sonnet-4-6)

Examples:
  python3 scripts/leg2_audit.py --domain lawdesai.com
  python3 scripts/leg2_audit.py --urls https://site1.com https://site2.com
  python3 scripts/leg2_audit.py --csv my-list.csv --limit 10
  python3 scripts/leg2_audit.py --limit 5
  python3 scripts/leg2_audit.py --rerun-failed
        """,
    )
    # Input source
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument("--csv", metavar="PATH", help="CSV file with a URL column (default: leads/prospects.csv)")
    input_group.add_argument("--urls", nargs="+", metavar="URL", help="One or more URLs to audit directly")
    input_group.add_argument("--domain", metavar="DOMAIN", help="Single domain to audit (e.g. example.com)")
    # Run control
    parser.add_argument("--limit", type=int, default=0, help="Max prospects to audit this run (0 = all pending)")
    parser.add_argument("--delay", type=float, default=2.0, help="Seconds between audits (default: 2.0)")
    parser.add_argument("--rerun-failed", action="store_true", help="Also re-audit rows marked audit_status=failed")
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Error: ANTHROPIC_API_KEY environment variable is required.", file=sys.stderr)
        return 2

    try:
        import anthropic  # noqa: F401
    except ImportError:
        print("Error: anthropic package not installed. Run: pip install anthropic", file=sys.stderr)
        return 2

    fc_key = os.environ.get("FIRECRAWL_API_KEY", "")
    model = ANTHROPIC_MODEL

    all_rows, csv_path = rows_from_args(args)

    # For direct --urls/--domain input, audit all rows (no audit_status filtering)
    if args.urls or args.domain:
        pending = all_rows
    else:
        if not all_rows:
            print("[leg2] No prospects found in CSV. Run leg1_discovery.py first or use --urls/--domain.", file=sys.stderr)
            return 0
        pending = [
            r for r in all_rows
            if not r.get("audit_status")
            or (args.rerun_failed and r.get("audit_status") == "failed")
        ]

    if args.limit > 0:
        pending = pending[: args.limit]

    print(f"[leg2] {len(pending)} prospects to audit (model: {model})", file=sys.stderr)

    for idx, row in enumerate(pending, start=1):
        url = row.get("website", "")
        print(f"\n[leg2] [{idx}/{len(pending)}] {url}", file=sys.stderr)
        try:
            updated = audit_one(row, model, fc_key)
            # Merge updated row back into all_rows by website match
            for i, r in enumerate(all_rows):
                if r.get("website") == url:
                    all_rows[i] = updated
                    break
            else:
                all_rows.append(updated)
        except Exception as exc:
            print(f"  [leg2] unexpected error: {exc}", file=sys.stderr)
            for i, r in enumerate(all_rows):
                if r.get("website") == url:
                    all_rows[i]["audit_status"] = "failed"
                    all_rows[i]["audit_date"] = date.today().isoformat()
                    break
        # Write back to CSV after each row (checkpoint)
        if csv_path:
            write_csv(csv_path, all_rows)
        time.sleep(args.delay)

    complete = sum(1 for r in all_rows if r.get("audit_status") == "complete")
    failed = sum(1 for r in all_rows if r.get("audit_status") == "failed")
    print(f"\n[leg2] Done. {complete} complete, {failed} failed.", file=sys.stderr)
    if not csv_path:
        print("[leg2] Audit reports written to runs/audits/", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
