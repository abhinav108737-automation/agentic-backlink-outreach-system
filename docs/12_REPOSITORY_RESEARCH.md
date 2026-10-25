# Reference Repository Strategy

High-priority research/provider inputs:
- pydantic/pydantic-ai — application operator and typed agent patterns
- eracle/OpenOutreach — outreach architecture reference
- eracle/OpenOutFind — discovery/qualification reference
- eracle/OpenOutSend — sender separation/guards reference
- prospectengine/Outbound-Engine — evidence-driven messaging methodology
- unclecode/Crawl4AI — crawling/research candidate
- searxng/searxng — self-hosted discovery candidate
- browser-use/browser-use — selective browser fallback
- qdrant/qdrant — semantic retrieval
- ollama/ollama — local model runtime
- langchain-ai/langgraph — workflow reference only if a concrete gap remains

For each external repo record URL, exact SHA, license, files studied, adopted/rejected concepts, copied/adapted/independently-reimplemented status, and security/licensing notes. Unclear licensing = idea-level study only.

---

## Provenance record — Bomx/qwoted-seo-backlinks-skill (SEC-08)

Studied 2026-10-25 for the proposed Digital PR / Earned Media channel (ADR-0021, docs/15).

| Field | Value |
|---|---|
| URL | `https://github.com/Bomx/qwoted-seo-backlinks-skill` |
| Pinned SHA | `3ef97c52e9f1305d3a1e5544a554befa8b929805` |
| Default branch | `main` |
| Created / last push | 2026-04-23 / **2026-05-02** |
| Activity | 150 stars, 18 forks, 0 open issues. **No pushes for ~6 months** at time of study; effectively dormant, single-author |
| Archived / disabled | no / no |

### License status — NOT ESTABLISHED. Idea-level study only.

Verified independently rather than taken from the README:

- GitHub's repository API reports **`license: null`** — no license detected.
- The **complete** file tree at the pinned SHA (API reports `truncated: false`) contains
  **no `LICENSE`, `LICENCE`, `COPYING` or `NOTICE` file** in any form.
- The README asserts MIT. A bare prose assertion with no license text and no detected license is
  **not a verified grant**.

**Conclusion: default "all rights reserved" must be assumed.** Per this document's standing rule
("Unclear licensing = idea-level study only"), **no source code, no HTML template and no prose
was or may be copied or adapted.** Methodology and ideas were studied; every component in
docs/15 is to be independently implemented against our own frozen contracts. If reuse rights are
ever needed, they must be obtained in writing from the author and recorded here before any code
is taken.

### Files studied (read-only, via raw HTTPS at the pinned SHA)

`README.md` (metadata/description only) · `SKILL.md` · `STATISTICS_PAGE_PLAYBOOK.md` ·
`qwoted_common.py` · `qwoted_search.py` · `requirements.txt`.
Not fetched in detail: `qwoted_login.py`, `qwoted_profile.py`, `qwoted_pitch.py`,
`templates/statistics_page_example.html`, `.env.example` — their behaviour was established from
`SKILL.md` and `qwoted_common.py`, and fetching the template was deliberately avoided given the
license position.

**Nothing was installed. No script was executed. No Qwoted login occurred. No pitch was sent.**

### Patterns adopted (reimplemented independently)

1. Four-stage channel shape: onboard an expert profile → find journalist requests → build a
   sourced research asset → pitch against a specific request.
2. Weighted opportunity scoring over explicit signals (publication tier, deadline proximity,
   competition/pitch-count, topical match to declared expertise) with a threshold deciding
   whether building a research asset is justified.
3. An approved expert/source profile as first-class state, with completeness checks and **no
   overwrite without explicit approval**.
4. Pitch composition that opens with why this source fits *this* request, carries concrete
   figures, and cites the research asset.
5. A sourced-statistics page as a durable **linkable asset**, reused as the cited URL across
   multiple relevant pitches rather than rebuilt per pitch.
6. Rigorous statistic sourcing discipline: named source, year, specific figure, and a URL that
   was actually fetched; a source-quality preference order; drop anything unverifiable; prefer
   recent figures; require source-domain diversity; keep conflicting reputable figures side by
   side with both attributions rather than silently choosing one.
7. A dry-run/draft path that does not notify the journalist, plus a duplicate guard of one pitch
   per request.

Item 6 maps almost exactly onto our existing `evidence_items` model and the
claims→`evidence_ids` rule, which is why this channel fits our spine with little new machinery.

### Patterns rejected (and why)

| Rejected | Reason |
|---|---|
| Playwright login writing session cookies to `~/.qwoted/storage_state.json` | Plaintext, unencrypted, no file permissions set; a user-level shared directory. Backend services must never hold scraped browser sessions (CLAUDE.md `.claude/rules/security.md`, ADR-0018) |
| **Algolia credentials scraped at runtime from React props** in the authenticated page's HTML, with requests referer-locked to qwoted.com | Credential extraction plus origin spoofing against an undocumented search backend. Explicitly out of scope and a ToS/account-termination risk |
| Qwoted internal server-rendered endpoints, CSRF-token and user-ID scraping from HTML | Undocumented, brittle (silent breakage on markup change), and not an API we are authorised to use |
| File-based authoritative state (`sent_pitches.json`, `profile_state.json`, `opportunities/`) | PostgreSQL is authoritative (CLAUDE.md). A JSON file cannot give us transactional dedupe, audit or idempotency |
| Unrestricted agent-controlled submission | Submission is a consequential outward action: it requires the deterministic policy gate, an approval and `REAL_SEND_ENABLED` |
| Zero-outbound-links rule on the research asset | Asserted on link-equity grounds; it also contradicts the playbook's own transparency rationale, since a plain-text attribution a reader cannot follow is *less* verifiable. Our evidence model exists to make claims checkable, so source links stay. Treat outbound-link policy as a configurable editorial choice, never a mandate |
| Quantified outcome claims ("10-50+ cumulative backlinks", rankings that "keep earning") | Unsupported projections. Only `VERIFIED` backlinks count as acquired, and nothing in our reporting may promise acquisition |
| Unthrottled scraping with a spoofed browser User-Agent, no backoff, no robots/ToS handling | Fails our provider discipline: rate limiting, typed error taxonomy and robots-awareness are required of every provider |

### Dependency risks

`requests>=2.31.0`, `python-dotenv>=1.0.0`, `playwright>=1.40.0` — all **lower-bound ranges with
no upper bound**, so a fresh resolve can pull an untested major version. We adopt none of these
for this channel: `requests` is redundant (we use `httpx`), `python-dotenv` is redundant
(pydantic-settings), and Playwright would only ever enter via the existing `BrowserProvider`
seam, which is `Null`/disabled today.

### Operational and security concerns

- Single-account, single-directory design collides under concurrent workers; no multi-tenancy.
- Session cookies and tokens returned in-band risk leaking into logs or serialised state — our
  `REDACTOR` discipline and ADR-0018 forbid that shape.
- Regex/HTML extraction fails silently, which in our system would surface as fabricated or empty
  evidence; unacceptable against ADR-0015 (never pass on missing data).
- A machine-parsed `RESULT:` stdout contract is corruptible by any other output.
- Journalist request text is **untrusted content** and must be handled under ADR-0020: data,
  never instructions.
