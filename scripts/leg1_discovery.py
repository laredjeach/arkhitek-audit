#!/usr/bin/env python3
"""
Leg 1: Multi-source prospect discovery for Arkhitek outreach.

Discovers businesses by region and niche, enriches each site with lightweight
SEO/audit signals, and appends to leads/prospects.csv (deduplicated by URL).

Discovery sources (tried in priority order when keys are present):
  1. Firecrawl  — requires FIRECRAWL_API_KEY
  2. SerpAPI    — requires SERPAPI_KEY
  3. Bing       — free, no key required
  4. DuckDuckGo — free, no key required (zero-config fallback)
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, date
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
PROSPECTS_CSV = REPO_ROOT / "leads" / "prospects.csv"

USER_AGENT = (
    "Mozilla/5.0 (compatible; ArkhitekLeadResearch/1.0; "
    "+https://arkhitek.earth)"
)

DEFAULT_EXCLUDED_DOMAINS = {
    "facebook.com", "instagram.com", "linkedin.com", "youtube.com",
    "tiktok.com", "x.com", "twitter.com", "yelp.com", "avvo.com",
    "findlaw.com", "justia.com", "superlawyers.com", "lawyers.com",
    "bbb.org", "mapquest.com", "yellowpages.com", "apple.com",
    "google.com", "bing.com", "duckduckgo.com", "healthgrades.com",
    "zocdoc.com", "angi.com", "thumbtack.com", "houzz.com",
}

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


@dataclass
class Lead:
    business_name: str
    domain: str
    website: str
    score: int
    fit_score: int
    opportunity_score: int
    owner_or_contact: str = ""
    email: str = ""
    phone: str = ""
    city: str = ""
    state: str = ""
    niche_category: str = ""
    source: str = ""
    discovered_at: str = ""
    title: str = ""
    meta_description: str = ""
    platform: str = "unknown"
    status_code: int | None = None
    https: bool = False
    h1_count: int = 0
    script_count: int = 0
    has_schema: bool = False
    has_llms_txt: bool = False
    issues: list[str] = field(default_factory=list)
    audit_status: str = ""
    audit_date: str = ""
    geo_score: str = ""
    gap_1: str = ""
    gap_2: str = ""
    gap_3: str = ""
    tech_gap: str = ""
    top_competitor: str = ""
    rebuild_signal: str = ""
    pitch_hook: str = ""
    service_opportunities: str = ""
    email_subject: str = ""
    email_body: str = ""
    draft_status: str = ""
    email_date: str = ""


class HeadParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_title = False
        self.title_parts: list[str] = []
        self.meta_description = ""
        self.h1_count = 0
        self.script_count = 0
        self.has_schema = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {k.lower(): (v or "") for k, v in attrs}
        tag = tag.lower()
        if tag == "title":
            self.in_title = True
        elif tag == "meta":
            name = attrs_dict.get("name", "").lower()
            prop = attrs_dict.get("property", "").lower()
            if name == "description" or prop == "og:description":
                if not self.meta_description:
                    self.meta_description = attrs_dict.get("content", "").strip()
        elif tag == "h1":
            self.h1_count += 1
        elif tag == "script":
            self.script_count += 1
            if "ld+json" in attrs_dict.get("type", "").lower():
                self.has_schema = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)

    @property
    def title(self) -> str:
        return html.unescape(" ".join(self.title_parts)).strip()


# ---------------------------------------------------------------------------
# URL / domain helpers
# ---------------------------------------------------------------------------

def normalize_domain(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"https://{url}")
    host = parsed.netloc.lower().split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def normalize_url(url: str) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}/"


def is_excluded(url: str, excluded: set[str]) -> bool:
    domain = normalize_domain(url)
    return any(domain == d or domain.endswith("." + d) for d in excluded)


def slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "lead"


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def fetch(url: str, timeout: int = 15) -> tuple[int | None, dict, str]:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read(1_500_000)
            charset = resp.headers.get_content_charset() or "utf-8"
            text = raw.decode(charset, errors="replace")
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, text
    except HTTPError as e:
        try:
            body = e.read(300_000).decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return e.code, {k.lower(): v for k, v in e.headers.items()}, body
    except (URLError, TimeoutError, OSError):
        return None, {}, ""


def extract_result_links(search_html: str) -> list[str]:
    links: list[str] = []
    for raw in re.findall(r'href=["\']([^"\']+)["\']', search_html, flags=re.I):
        href = html.unescape(raw)
        if "uddg=" in href:
            qs = parse_qs(urlparse(href).query)
            if qs.get("uddg"):
                href = unquote(qs["uddg"][0])
        if href.startswith("/url?"):
            qs = parse_qs(urlparse(href).query)
            if qs.get("q"):
                href = qs["q"][0]
        if href.startswith(("http://", "https://")):
            links.append(href)
    return links


# ---------------------------------------------------------------------------
# Discovery sources
# ---------------------------------------------------------------------------

def discover_firecrawl(queries: list[str], excluded: set[str], max_results: int) -> dict[str, str]:
    """Use Firecrawl search API to discover prospects."""
    api_key = os.environ.get("FIRECRAWL_API_KEY", "")
    if not api_key:
        return {}
    try:
        from firecrawl import FirecrawlApp
        app = FirecrawlApp(api_key=api_key)
        found: dict[str, str] = {}
        for query in queries:
            if len(found) >= max_results:
                break
            try:
                results = app.search(query, params={"limit": 10})
                for item in (results.get("data") or []):
                    url = item.get("url", "")
                    if not url or is_excluded(url, excluded):
                        continue
                    url = normalize_url(url)
                    domain = normalize_domain(url)
                    if domain and domain not in found:
                        found[domain] = query
                    if len(found) >= max_results:
                        break
            except Exception as exc:
                print(f"[firecrawl search] {exc}", file=sys.stderr)
        return found
    except ImportError:
        print("[leg1] firecrawl-py not installed; skipping Firecrawl source", file=sys.stderr)
        return {}


def discover_serpapi(queries: list[str], excluded: set[str], max_results: int) -> dict[str, str]:
    """Use SerpAPI for Google-backed discovery."""
    api_key = os.environ.get("SERPAPI_KEY", "")
    if not api_key:
        return {}
    found: dict[str, str] = {}
    for query in queries:
        if len(found) >= max_results:
            break
        encoded = quote_plus(query)
        url = f"https://serpapi.com/search?engine=google&q={encoded}&api_key={api_key}&num=10"
        _, _, body = fetch(url, timeout=20)
        try:
            data = json.loads(body)
        except Exception:
            continue
        for result in data.get("organic_results", []):
            link = result.get("link", "")
            if not link or is_excluded(link, excluded):
                continue
            link = normalize_url(link)
            domain = normalize_domain(link)
            if domain and domain not in found:
                found[domain] = query
            if len(found) >= max_results:
                break
    return found


def discover_google_cse(queries: list[str], excluded: set[str], max_results: int) -> dict[str, str]:
    """Use Google Custom Search Engine API."""
    cse_id = os.environ.get("GOOGLE_CSE_ID", "")
    api_key = os.environ.get("GOOGLE_API_KEY", "")
    if not cse_id or not api_key:
        return {}
    found: dict[str, str] = {}
    for query in queries:
        if len(found) >= max_results:
            break
        encoded = quote_plus(query)
        url = f"https://www.googleapis.com/customsearch/v1?key={api_key}&cx={cse_id}&q={encoded}&num=10"
        _, _, body = fetch(url, timeout=20)
        try:
            data = json.loads(body)
        except Exception:
            continue
        for item in data.get("items", []):
            link = item.get("link", "")
            if not link or is_excluded(link, excluded):
                continue
            link = normalize_url(link)
            domain = normalize_domain(link)
            if domain and domain not in found:
                found[domain] = query
            if len(found) >= max_results:
                break
    return found


def discover_bing_ddg(
    queries: list[str],
    engines: list[str],
    excluded: set[str],
    max_results: int,
    delay_seconds: float,
) -> dict[str, str]:
    """Bing and DuckDuckGo HTML scraping — no API keys required."""
    found: dict[str, str] = {}
    for query in queries:
        encoded = quote_plus(query)
        search_urls = []
        if "bing" in engines:
            search_urls.append(f"https://www.bing.com/search?q={encoded}&count=30")
        if "duckduckgo" in engines:
            search_urls.append(f"https://duckduckgo.com/html/?q={encoded}")
        for search_url in search_urls:
            _, _, body = fetch(search_url, timeout=20)
            for link in extract_result_links(body):
                if len(found) >= max_results:
                    return found
                if is_excluded(link, excluded):
                    continue
                url = normalize_url(link)
                domain = normalize_domain(url)
                if domain and domain not in found:
                    found[domain] = query
            time.sleep(delay_seconds)
    return found


def discover_seed_urls(seeds: list[str], excluded: set[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for seed in seeds:
        if not seed or is_excluded(seed, excluded):
            continue
        url = normalize_url(seed)
        found[normalize_domain(url)] = "seed_url"
    return found


# ---------------------------------------------------------------------------
# Site enrichment
# ---------------------------------------------------------------------------

def check_llms(url: str) -> bool:
    parsed = urlparse(url)
    llms_url = f"{parsed.scheme}://{parsed.netloc}/llms.txt"
    status, _, body = fetch(llms_url, timeout=10)
    return status == 200 and len(body.strip()) > 20


def detect_platform(headers: dict, body: str) -> str:
    lh = " ".join(f"{k}: {v}" for k, v in headers.items()).lower()
    lb = body.lower()
    if "x-shopify" in lh or "cdn.shopify.com" in lb:
        return "Shopify"
    if "wp-content" in lb or "wp-json" in lb:
        return "WordPress"
    if "x-wix" in lh or "wixstatic" in lb:
        return "Wix"
    if "squarespace" in lh or "static1.squarespace.com" in lb:
        return "Squarespace"
    if "x-vercel" in lh or "__next" in lb:
        return "Next.js/Vercel"
    if "webflow" in lb:
        return "Webflow"
    if "x-showit" in lh or "showit" in lb:
        return "Showit"
    return "unknown"


def extract_phone(text: str) -> str:
    match = re.search(
        r"(?:\+?1[\s.\-]?)?(?:\(?\d{3}\)?[\s.\-]?)\d{3}[\s.\-]?\d{4}", text
    )
    return match.group(0).strip() if match else ""


def extract_email_from_page(text: str) -> str:
    match = re.search(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", text, re.I)
    return match.group(0).lower() if match else ""


def business_name_from_title(title: str, domain: str) -> str:
    generic = {"home", "homepage", "welcome", "index"}
    if title:
        first = re.split(r"\s[\-|:|]\s|\|", title)[0].strip()
        if 2 <= len(first) <= 80 and first.lower() not in generic:
            return first
    return domain.split(".")[0].replace("-", " ").title()


def extract_location(body: str, region: str) -> tuple[str, str]:
    """Attempt to extract city/state from page body or region string."""
    parts = [p.strip() for p in region.split(",")]
    city = parts[0] if parts else ""
    state = parts[1].strip() if len(parts) > 1 else ""
    return city, state


def score_lead(
    parser: HeadParser,
    body: str,
    headers: dict,
    url: str,
    region: str,
    business_type: str,
    has_llms: bool,
) -> tuple[int, int, int, list[str]]:
    issues: list[str] = []
    missing_security = []
    for h in ["strict-transport-security", "x-frame-options", "x-content-type-options", "referrer-policy"]:
        if h not in headers:
            missing_security.append(h)

    title = parser.title.lower()
    meta = parser.meta_description.lower()
    sample = re.sub(r"\s+", " ", body[:25000].lower())
    region_terms = [t for t in re.split(r"[\s,]+", region.lower()) if len(t) > 3]
    biz_terms = [t for t in re.split(r"[\s,/]+", business_type.lower()) if len(t) > 3]

    fit = 20
    if any(t in title or t in meta or t in sample for t in region_terms):
        fit += 15
    if any(t in title or t in meta or t in sample for t in biz_terms):
        fit += 15
    if extract_phone(body):
        fit += 5
    if url.startswith("https://"):
        fit += 5
    fit = min(fit, 50)

    opp = 0
    if not parser.title or len(parser.title) < 20 or len(parser.title) > 70:
        opp += 10
        issues.append("title tag needs review")
    if not parser.meta_description or len(parser.meta_description) < 70:
        opp += 10
        issues.append("missing or thin meta description")
    if parser.h1_count != 1:
        opp += 8
        issues.append(f"h1 count is {parser.h1_count}")
    if not parser.has_schema:
        opp += 8
        issues.append("no homepage JSON-LD schema detected")
    if not has_llms:
        opp += 5
        issues.append("missing llms.txt")
    if missing_security:
        opp += 8
        issues.append("missing security headers")
    if parser.script_count > 35:
        opp += 5
        issues.append(f"heavy script count ({parser.script_count})")
    opp = min(opp, 50)

    return min(100, fit + opp), fit, opp, issues


def enrich_firecrawl(url: str, api_key: str) -> tuple[str, str, str]:
    """Extract contact info from a page using Firecrawl extract."""
    try:
        from firecrawl import FirecrawlApp
        app = FirecrawlApp(api_key=api_key)
        result = app.extract(
            [url],
            params={
                "prompt": "Extract: owner_or_contact (owner name or main contact person if visible), email (contact email), phone (main phone number). Return JSON with those keys.",
                "schema": {
                    "type": "object",
                    "properties": {
                        "owner_or_contact": {"type": "string"},
                        "email": {"type": "string"},
                        "phone": {"type": "string"},
                    },
                },
            },
        )
        data = result.get("data", {}) if isinstance(result, dict) else {}
        return (
            str(data.get("owner_or_contact") or ""),
            str(data.get("email") or ""),
            str(data.get("phone") or ""),
        )
    except Exception:
        return "", "", ""


def enrich(
    url: str,
    source_query: str,
    region: str,
    business_type: str,
    niche: str,
    required_terms: list[str] | None = None,
) -> Lead:
    url = normalize_url(url)
    status, headers, body = fetch(url, timeout=20)
    if not body:
        status, headers, body = fetch(url.replace("https://", "http://"), timeout=20)

    parser = HeadParser()
    try:
        parser.feed(body)
    except Exception:
        pass

    has_llms = check_llms(url) if status and status < 500 else False
    score, fit, opp, issues = score_lead(
        parser, body, headers, url, region, business_type, has_llms
    )
    domain = normalize_domain(url)
    name = business_name_from_title(parser.title, domain)
    platform = detect_platform(headers, body)
    city, state = extract_location(body, region)
    phone = extract_phone(body)
    email = extract_email_from_page(body)
    owner = ""

    # Try Firecrawl contact extraction if available and no email found
    fc_key = os.environ.get("FIRECRAWL_API_KEY", "")
    if fc_key and not email:
        owner, email, phone_fc = enrich_firecrawl(url, fc_key)
        if not phone and phone_fc:
            phone = phone_fc

    required_terms = required_terms or []
    searchable = " ".join([parser.title, parser.meta_description, domain, body[:50000]]).lower()
    matches = all(t.lower() in searchable for t in required_terms)

    return Lead(
        business_name=name,
        domain=domain,
        website=url,
        score=score,
        fit_score=fit,
        opportunity_score=opp,
        owner_or_contact=owner,
        email=email,
        phone=phone,
        city=city,
        state=state,
        niche_category=niche or business_type,
        source=source_query,
        discovered_at=datetime.utcnow().isoformat(timespec="seconds"),
        title=parser.title,
        meta_description=parser.meta_description,
        platform=platform,
        status_code=status,
        https=url.startswith("https://"),
        h1_count=parser.h1_count,
        script_count=parser.script_count,
        has_schema=parser.has_schema,
        has_llms_txt=has_llms,
        issues=issues,
    )


# ---------------------------------------------------------------------------
# CSV persistence
# ---------------------------------------------------------------------------

def load_existing_websites(csv_path: Path) -> set[str]:
    if not csv_path.exists():
        return set()
    existing = set()
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            w = row.get("website", "").strip().rstrip("/")
            if w:
                existing.add(w)
    return existing


def append_leads(csv_path: Path, leads: list[Lead]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not csv_path.exists()
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        for lead in leads:
            row = asdict(lead)
            row["issues"] = "; ".join(lead.issues)
            row["website"] = lead.website
            writer.writerow(row)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config(path: str | None) -> dict:
    if not path:
        return {}
    with open(os.path.expanduser(path), encoding="utf-8") as f:
        return json.load(f)


def build_queries(config: dict, region: str, business_type: str) -> list[str]:
    templates = config.get("queries") or [
        "{business_type} {region}",
        "{region} {business_type}",
        "best {business_type} in {region}",
        "{business_type} near {region}",
    ]
    return [q.format(region=region, business_type=business_type).strip() for q in templates]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Leg 1: Discover prospects and append to leads/prospects.csv.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Discovery sources (in priority order):
  firecrawl  requires FIRECRAWL_API_KEY env var
  serpapi    requires SERPAPI_KEY env var
  google_cse requires GOOGLE_CSE_ID + GOOGLE_API_KEY env vars
  bing       free, no key required
  duckduckgo free, no key required (always-on fallback)

Examples:
  python3 scripts/leg1_discovery.py --region "Ventura County, CA" --business-type "dentist"
  python3 scripts/leg1_discovery.py --config config/config.example.json
  python3 scripts/leg1_discovery.py --region "Los Angeles, CA" --business-type "med spa" --max-results 30 --min-score 55
        """,
    )
    parser.add_argument("--config", help="Path to JSON config file")
    parser.add_argument("--region", help="Target region, e.g. 'Ventura County, CA'")
    parser.add_argument("--business-type", help="Business type for search queries, e.g. 'dentist'")
    parser.add_argument("--niche", help="Niche slug matching a file in lenses/, e.g. 'dental'")
    parser.add_argument("--max-results", type=int, help="Maximum prospects to discover (default: 50)")
    parser.add_argument("--min-score", type=int, help="Minimum lead score to keep (default: 45)")
    parser.add_argument("--delay", type=float, help="Seconds between requests (default: 1.0)")
    parser.add_argument("--seed-url", action="append", default=[], help="Add a seed URL (can repeat)")
    parser.add_argument("--required-term", action="append", default=[], help="Require term on prospect site (can repeat)")
    parser.add_argument("--target-platform", action="append", default=[], help="Filter to platform, e.g. WordPress (can repeat)")
    parser.add_argument(
        "--sources",
        help="Comma-separated discovery sources to try: firecrawl,serpapi,google_cse,bing,duckduckgo (default: all available)",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    region = args.region or cfg.get("region")
    business_type = args.business_type or cfg.get("business_type")
    if not region or not business_type:
        print("Error: --region and --business-type are required (or set in config).", file=sys.stderr)
        return 2

    niche = args.niche or cfg.get("niche", slugify(business_type))
    max_results = args.max_results or int(cfg.get("max_results", 50))
    min_score = args.min_score if args.min_score is not None else int(cfg.get("min_score", 45))
    delay = args.delay if args.delay is not None else float(cfg.get("delay_seconds", 1.0))
    required_terms = list(cfg.get("required_terms", [])) + args.required_term
    target_platforms = {p.lower() for p in (cfg.get("target_platforms", []) + args.target_platform)}
    excluded = set(DEFAULT_EXCLUDED_DOMAINS)
    excluded.update(cfg.get("excluded_domains", []))
    seeds = list(cfg.get("seed_urls", [])) + args.seed_url
    source_order = [s.strip() for s in (args.sources or "").split(",") if s.strip()] or \
                   cfg.get("discovery_sources", ["firecrawl", "serpapi", "google_cse", "bing", "duckduckgo"])

    queries = build_queries(cfg, region, business_type)
    discovered: dict[str, str] = {}

    # Seed URLs first
    discovered.update(discover_seed_urls(seeds, excluded))

    # Run sources in priority order until max_results
    for source in source_order:
        if len(discovered) >= max_results:
            break
        remaining = max_results - len(discovered)
        print(f"[leg1] trying source: {source} (need {remaining} more)", file=sys.stderr)
        new: dict[str, str] = {}
        if source == "firecrawl":
            new = discover_firecrawl(queries, excluded, remaining)
        elif source == "serpapi":
            new = discover_serpapi(queries, excluded, remaining)
        elif source == "google_cse":
            new = discover_google_cse(queries, excluded, remaining)
        elif source == "bing":
            new = discover_bing_ddg(queries, ["bing"], excluded, remaining, delay)
        elif source == "duckduckgo":
            new = discover_bing_ddg(queries, ["duckduckgo"], excluded, remaining, delay)
        for domain, query in new.items():
            if domain not in discovered:
                discovered[domain] = query

    # Load existing URLs to deduplicate
    existing_websites = load_existing_websites(PROSPECTS_CSV)

    # Enrich each discovered domain
    leads: list[Lead] = []
    total = len(discovered)
    for idx, (domain, source_query) in enumerate(discovered.items(), start=1):
        url = normalize_url(domain)
        url_key = url.rstrip("/")
        if url_key in existing_websites:
            print(f"[{idx}/{total}] SKIP {domain} (already in CSV)", file=sys.stderr)
            continue
        print(f"[{idx}/{total}] enriching {domain} ...", file=sys.stderr)
        lead = enrich(url, source_query, region, business_type, niche, required_terms)
        platform_ok = not target_platforms or lead.platform.lower() in target_platforms
        if lead.score >= min_score and platform_ok:
            leads.append(lead)
            print(f"  → score {lead.score} | {lead.business_name} | {'; '.join(lead.issues) or 'no issues detected'}", file=sys.stderr)
        else:
            print(f"  → score {lead.score} (below {min_score}) — skipped", file=sys.stderr)
        time.sleep(delay)

    leads.sort(key=lambda l: l.score, reverse=True)
    append_leads(PROSPECTS_CSV, leads)
    print(f"\n[leg1] Appended {len(leads)} new leads to {PROSPECTS_CSV}", file=sys.stderr)
    print(f"[leg1] Total in CSV after run: {len(existing_websites) + len(leads)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
