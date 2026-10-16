# arkhitek-outreach

A 4-leg pipeline for discovering local service business prospects, auditing their websites, and drafting personalized cold outreach emails.

Built for Arkhitek — a digital agency helping small businesses improve their websites, connect their tools, and put AI to practical use.

---

## What It Does

| Leg | Script | What it produces |
|---|---|---|
| 1. Discover | `leg1_discovery.py` | Finds businesses by region + niche, scores their sites, appends to `leads/prospects.csv` |
| 2. Audit | `leg2_audit.py` | Scrapes each site, runs a Claude analysis, writes a full audit report + updates CSV |
| 3. Draft | `leg3_draft.py` | Drafts personalized cold emails using audit findings + niche lenses, updates CSV |
| 4. Send | (manual / n8n) | Review approved drafts, send via Gmail/Instantly/Apollo |

All state lives in `leads/prospects.csv`. Legs are independently retryable.

---

## Quick Start

```bash
# 1. Clone and set up
git clone https://github.com/laredjeach/arkhitek-outreach
cd arkhitek-outreach
pip install -r requirements.txt

# 2. Set environment variables
cp .env.example .env
# Edit .env — add ANTHROPIC_API_KEY at minimum

# 3. Run a full campaign (discovery → audit → email drafts)
python3 scripts/run_campaign.py \
  --region "Ventura County, CA" \
  --business-type "personal injury lawyer" \
  --niche personal-injury-lawyer \
  --max-results 20 \
  --audit-limit 5

# 4. Review results
open leads/prospects.csv       # Full pipeline state
open runs/audits/              # Per-company audit reports and outreach emails
```

**Minimum setup (no API keys):** Leg 1 works with zero API keys via Bing/DuckDuckGo fallback. Legs 2 and 3 require `ANTHROPIC_API_KEY`.

---

## Running Individual Legs

```bash
# Leg 1 only — discover dentists in LA
python3 scripts/leg1_discovery.py \
  --region "Los Angeles, CA" \
  --business-type "dentist" \
  --niche dental \
  --max-results 30

# Leg 2 only — audit top 10 unaudited prospects
python3 scripts/leg2_audit.py --limit 10

# Leg 3 only — draft emails for all audited prospects
python3 scripts/leg3_draft.py

# Help for any script
python3 scripts/leg1_discovery.py --help
```

---

## Configuration

Copy `config/config.example.json` and pass it with `--config`:

```bash
python3 scripts/leg1_discovery.py --config config/my-campaign.json
```

Key config fields:

| Field | Default | Notes |
|---|---|---|
| `region` | required | "Ventura County, CA" |
| `business_type` | required | "personal injury lawyer" |
| `niche` | slugified business_type | Must match a file in `lenses/` |
| `max_results` | 50 | Max prospects to discover |
| `min_score` | 45 | Min lead score to keep (0–100) |
| `audit_limit` | 10 | Max to audit per run |
| `discovery_sources` | all | `["firecrawl","serpapi","google_cse","bing","duckduckgo"]` |
| `delay_seconds` | 1.0 | Seconds between requests |
| `seed_urls` | [] | Hand-collected URLs to enrich |

---

## Lead Scoring

Each prospect gets a 0–100 score based on:

**Fit (0–50):** Does this look like a real business in the right category and region?
- Region terms found on site: +15
- Business type terms found: +15
- Phone number detected: +5
- HTTPS: +5
- Base: +20

**Opportunity (0–50):** How much SEO/audit work does this site need?
- Title tag issues: +10
- Thin/missing meta description: +10
- Wrong number of H1s: +8
- No JSON-LD schema: +8
- Missing llms.txt: +5
- Missing security headers: +8
- Heavy script count: +5

| Score | Meaning | Action |
|---:|---|---|
| 75–100 | Strong prospect | Audit + email |
| 55–74 | Good prospect | Audit |
| 45–54 | Possible | Manual review |
| <45 | Weak fit | Skip |

---

## Discovery Sources

Leg 1 tries sources in priority order until `max_results` is reached:

| Source | API Key | Quality |
|---|---|---|
| Firecrawl | `FIRECRAWL_API_KEY` | Best — structured search + contact extraction |
| SerpAPI | `SERPAPI_KEY` | Good — Google results |
| Google CSE | `GOOGLE_CSE_ID` + `GOOGLE_API_KEY` | Good — custom search engine |
| Bing | none | Free, no key |
| DuckDuckGo | none | Free, always available |

Zero-config fallback: if no API keys are set, Bing + DuckDuckGo run automatically.

---

## Niche Lenses

Leg 3 loads a niche context file to inform tone, ROI framing, and email style:

| File | Used when niche_category contains |
|---|---|
| `lenses/personal-injury-lawyer.md` | "personal injury," "injury lawyer," "law firm" |
| `lenses/dental.md` | "dental," "dentist," "orthodont" |
| `lenses/med-spa.md` | "med spa," "aesthetics," "medspa," "botox" |
| `lenses/general.md` | everything else |

Add your own lens by creating `lenses/{niche-slug}.md` and matching the existing format.

---

## Audit Reports

Leg 2 writes a full audit report to `runs/audits/{slug}-audit.md` for each prospect. The format follows the Anand Desai Law audit gold standard:

- Executive summary
- What's working
- Key opportunities (🔴 Critical / 🟠 High / 🟡 Medium / 🟢 Quick Wins)
- What we'd do first (Week 1 / Week 2–4 / Month 2–3)
- Service opportunities

These are client-ready reports. You can share them directly as the outreach deliverable.

---

## Outreach Strategy

**Lead with one specific, visible finding — not a list.**

Good pitch hooks:
- "Your attorney page title currently reads 'Crimina Defensel' — Google shows this in search results today."
- "Your homepage shows 94 Google reviews but they don't appear as stars in search results."
- "Your contact page returns a 404 — any backlinks or directory listings pointing at the old URL are dead ends."

Bad approach:
- "Your SEO needs improvement."
- "We can help optimize your digital presence."

The outreach email is 3–4 sentences:
1. The specific finding (visible, non-jargon)
2. The plain-English business impact
3. Offer to send the short audit
4. (Optional) low-pressure close

---

## Leg 4: Sending

Leg 4 is not scripted — use the tool that fits your volume:

- **Pilot (< 20 emails):** Send from Gmail directly using the email drafts in `leads/prospects.csv`
- **Scale (50+ emails/week):** Import to Instantly or Apollo for sequencing, warm-up, and reply tracking
- **Automation:** n8n workflow that reads `draft_status = ready` rows and sends via API

See `WORKFLOW.md` for the full n8n automation sequence.

---

## Compliance

- Scrapes public webpages only
- Does not log into sites, bypass paywalls, or submit forms
- Use this to select good-fit businesses and create specific audit reports — not to spam
- Respect rate limits: increase `delay_seconds` if you see slow responses or rate limiting
- For high-volume outreach, comply with CAN-SPAM (US) or GDPR (EU) requirements

---

## File Structure

```
arkhitek-outreach/
├── README.md
├── WORKFLOW.md               # Full pipeline spec
├── .env.example              # API keys reference
├── requirements.txt
├── config/
│   └── config.example.json
├── scripts/
│   ├── leg1_discovery.py     # Multi-source discovery → prospects.csv
│   ├── leg2_audit.py         # Firecrawl + Claude → audit reports + CSV
│   ├── leg3_draft.py         # Claude email drafts → CSV
│   └── run_campaign.py       # Orchestrator
├── lenses/
│   ├── personal-injury-lawyer.md
│   ├── dental.md
│   ├── med-spa.md
│   └── general.md
├── templates/
│   ├── cold-first-touch.md
│   └── follow-up-day3.md
├── engine/
│   └── audits/               # Reference audit templates from arkhitek_neo-coreteks
├── leads/
│   └── prospects.csv         # Master pipeline state (created on first run)
└── runs/
    └── audits/               # Per-company audit reports and outreach emails
```
