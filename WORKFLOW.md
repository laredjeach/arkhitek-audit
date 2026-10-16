# Arkhitek Outreach — 4-Leg Pipeline

The canonical specification for the outreach system. Each leg is independently runnable and retryable.

---

## Pipeline Overview

```
Leg 1: Discovery      →  leads/prospects.csv (new rows)
Leg 2: Audit          →  leads/prospects.csv (audit fields) + runs/audits/{slug}-audit.md
Leg 3: Email Draft    →  leads/prospects.csv (email fields) + runs/audits/{slug}-outreach.md
Leg 4: Send           →  Apollo / Instantly / Gmail (manual or n8n)
```

All state lives in `leads/prospects.csv`. Each leg reads that file, does its work, and writes back.

---

## Leg 1: Discovery + Contact Extraction

**Script:** `scripts/leg1_discovery.py`

**Purpose:** Find businesses by region and niche, check their homepage for SEO signals, score them, and append qualified leads to `leads/prospects.csv`.

**Discovery sources (tried in priority order):**

| Source | Requires | Notes |
|---|---|---|
| Firecrawl | `FIRECRAWL_API_KEY` | Best quality; uses search + extract APIs |
| SerpAPI | `SERPAPI_KEY` | Google-backed results |
| Google CSE | `GOOGLE_CSE_ID` + `GOOGLE_API_KEY` | Custom Search Engine |
| Bing | none | Free HTML scraping |
| DuckDuckGo | none | Free HTML scraping; always-on fallback |

**Scoring (0–100):**

Fit score (0–50):
- Region terms found on site: +15
- Business type terms found: +15
- Phone number detected: +5
- HTTPS: +5
- Base: +20

Opportunity score (0–50):
- Title tag missing or wrong length: +10
- Meta description thin or missing: +10
- H1 count ≠ 1: +8
- No JSON-LD schema: +8
- Missing llms.txt: +5
- Missing security headers: +8
- Heavy script count (>35): +5

| Score | Meaning | Action |
|---:|---|---|
| 75–100 | Strong prospect, obvious audit hooks | Run audit + email |
| 55–74 | Good prospect | Run audit |
| 45–54 | Possible prospect | Manual review first |
| <45 | Weak fit | Skip |

**CSV columns written:**
`business_name, owner_or_contact, website, email, phone, city, state, niche_category, source, discovered_at, score, fit_score, opportunity_score, platform, title, meta_description, h1_count, script_count, has_schema, has_llms_txt, issues`

---

## Leg 2: Site Audit

**Script:** `scripts/leg2_audit.py`

**Purpose:** Deep-scrape each prospect site, send content to Claude for structured analysis, write a full audit report, and update the CSV.

**Process per prospect:**
1. Scrape homepage + up to 3 key pages (Firecrawl primary, requests fallback)
2. Send scraped content to Claude with structured audit prompt → JSON analysis
3. Send analysis + content to Claude for narrative sections → JSON narrative
4. Write audit report to `runs/audits/{slug}-audit.md`
5. Update CSV row with audit fields

**Claude output fields:**

| Field | Type | Description |
|---|---|---|
| `geo_score` | int 0–100 | AI/GEO visibility score |
| `gap_1` | string | Most critical finding |
| `gap_2` | string | Second finding |
| `gap_3` | string | Third finding |
| `tech_gap` | Severe/Major/Moderate/Minor | Overall technical debt |
| `top_competitor` | string | Visible competitor if found |
| `rebuild_signal` | strong/possible/low | Rebuild opportunity signal |
| `pitch_hook` | string | One-sentence outreach hook (specific, visible problem) |
| `service_opportunities` | CSV string | Applicable Arkhitek service areas |

**Audit report structure** (matches Anand Desai Law gold standard):
- Executive summary (what's working + biggest opportunity)
- What's working (2–4 positives)
- Key opportunities (🔴 Critical / 🟠 High / 🟡 Medium / 🟢 Quick Wins)
- What we'd do first (Week 1 / Week 2–4 / Month 2–3 roadmap)
- Service opportunities

---

## Leg 3: Email Drafting

**Script:** `scripts/leg3_draft.py`

**Purpose:** Generate a personalized cold email for each audited prospect using the pitch_hook, niche lens, and email template.

**Process per prospect:**
1. Load niche lens from `lenses/{niche_slug}.md` (auto-detected from niche_category)
2. Load `templates/cold-first-touch.md`
3. Call Claude: write a 3–4 sentence email leading with the pitch_hook
4. Call Claude: write a 2-sentence Day 3 follow-up
5. Update CSV with email_subject, email_body, draft_status
6. Write `runs/audits/{slug}-outreach.md` with both emails

**Niche slug detection:**
- "personal injury," "injury lawyer," "law firm" → `personal-injury-lawyer`
- "dental," "dentist," "orthodont" → `dental`
- "med spa," "aesthetics," "medspa," "botox" → `med-spa`
- everything else → `general`

**Email rules (enforced in prompt):**
- 3–4 sentences max
- Opens with the specific pitch_hook finding
- No "optimize," "SEO strategy," "digital marketing services"
- No filler openers
- Ends with an offer to send the audit, no pressure

---

## Leg 4: Send

**Not scripted — handled manually or via n8n.**

**Recommended flow:**

1. Review `leads/prospects.csv` — filter `draft_status = ready`
2. Check each `runs/audits/{slug}-outreach.md` for quality before sending
3. For pilot (< 20 emails): send via Gmail directly
4. For scale (> 50 emails/week): import to Instantly or Apollo for sequencing and reply tracking

**n8n automation (when ready):**
- Trigger: new row in CSV with `draft_status = ready`
- Action: send via Gmail SMTP / Apollo API / Instantly API
- Update: set `draft_status = sent`, log send timestamp

---

## Master CSV Schema

`leads/prospects.csv` — single source of truth, append-deduplicated by `website`.

| Column | Added by | Notes |
|---|---|---|
| business_name | Leg 1 | |
| owner_or_contact | Leg 1 | Owner name if found via Firecrawl extract |
| website | Leg 1 | Normalized URL, dedup key |
| email | Leg 1 | Contact email from site |
| phone | Leg 1 | Main phone |
| city | Leg 1 | |
| state | Leg 1 | |
| niche_category | Leg 1 | Used for lens selection in Leg 3 |
| source | Leg 1 | Which discovery source found this |
| discovered_at | Leg 1 | ISO timestamp |
| score | Leg 1 | 0–100 composite |
| fit_score | Leg 1 | 0–50 |
| opportunity_score | Leg 1 | 0–50 |
| platform | Leg 1 | WordPress, Wix, etc. |
| title | Leg 1 | Homepage title |
| meta_description | Leg 1 | |
| h1_count | Leg 1 | |
| script_count | Leg 1 | |
| has_schema | Leg 1 | true/false |
| has_llms_txt | Leg 1 | true/false |
| issues | Leg 1 | Semicolon-separated list |
| audit_status | Leg 2 | empty / complete / failed |
| audit_date | Leg 2 | |
| geo_score | Leg 2 | 0–100 |
| gap_1 | Leg 2 | |
| gap_2 | Leg 2 | |
| gap_3 | Leg 2 | |
| tech_gap | Leg 2 | Severe/Major/Moderate/Minor |
| top_competitor | Leg 2 | |
| rebuild_signal | Leg 2 | strong/possible/low |
| pitch_hook | Leg 2 | One-sentence outreach hook |
| service_opportunities | Leg 2 | Comma-separated service areas |
| email_subject | Leg 3 | |
| email_body | Leg 3 | |
| draft_status | Leg 3 | empty / ready / sent |
| email_date | Leg 3 | |

---

## Service Areas (for service_opportunities field)

| Slug | Description |
|---|---|
| website-rebuild | Full site rebuild (new platform, new design) |
| seo | Technical and on-page SEO |
| local-seo | Local search signals, NAP, Google Business |
| ai-visibility | llms.txt, schema, FAQ, GEO optimization |
| schema | Structured data / JSON-LD implementation |
| performance | Core Web Vitals, script optimization |
| conversion | CTA clarity, form friction, booking flow |
| content | Service pages, city pages, blog |
| email-marketing | Email flows and campaigns |
| ai-assistant | Business knowledge + AI assistant setup |
| business-knowledge | Brand data organization and documentation |

---

## Automation Sequence (Future)

1. Cron (weekly): run Leg 1 for target niche/region → append to CSV
2. Cron (daily): run Leg 2 on unaudited rows → audit reports
3. n8n trigger: new audited rows → run Leg 3
4. Human review: approve email drafts in CSV or Notion
5. n8n: send approved emails via Instantly, handle replies
