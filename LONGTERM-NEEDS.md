# Atelier — long-term needs assessment (M3-T3)

**Date:** 2026-07-16 · **Author:** research subagent · **Status:** read-only research, no repo changes, no signups, no API calls made.

Doc citations refer to `atelier_PRD_v2.docx` (PRD §n) and `atelier_TDD_v1.docx` (TDD §n); full text extracted to `prd.txt` / `tdd.txt` in this directory (239 + 1,097 paragraphs — the complete documents; the TDD's internal numbering skips §5–7, nothing is missing from the extraction). All prices retrieved **2026-07-16**; every price claim carries a URL. Anything marked *(inference)* is my estimate, not a doc or source claim.

---

## 1. TL;DR

- **The whole external surface is 6 accounts**: fal.ai, Google (Gemini API), Cloudflare (domain + DNS + R2), a host (Hetzner **or** Fly), Resend, Sentry. GitHub you already have. PhotoRoom is a 7th, currently on hold with the key already stored.
- **Fixed running cost at your scale is ~$6–10/month** (Hetzner scenario ≈ **$9.3/mo** all-in with backups and domain; lean Fly scenario ≈ **$6.2/mo** but with more moving parts). Everything else — email, errors, storage, CI, TLS — fits comfortably in free tiers for years at single-user scale.
- **Variable cost is the image models, and only when you run Wada**: the PRD's `$0.0675/edit` figure is **still exactly the current fal.ai price**, so every dollar figure in the PRD/TDD remains valid. A recommended-shape study (K=C=3) costs **$1.01**; the <$2 median target **holds** under the design's defaults. A fully-generated capped-12 K=C=4 study is **$2.16–2.70** — the guardrails in TDD §8.5–8.11 are what keep the *median* under $2.
- **Seedream 5.0 Pro is price-favored at the pipeline's native 1536px** ($0.0675 vs Nano Banana 2's $0.101 at 2K / $0.067 at only-1K). M0 remains a quality bake-off, not a price decision — the prices are within pennies.
- **Biggest one-time obligations before prod**: rotate the PhotoRoom key (it transited chat), verify a domain in Resend (SPF/DKIM), configure R2 lifecycle rules (TDD §3), set the Wada budget caps (TDD §8.11), and stand up backups (DB dump + pinned-object sync).

---

## 2. Every external dependency named in the docs

Exhaustive sweep of both documents for services, models, and operational requirements:

| Dependency | Where in docs | Role |
|---|---|---|
| **Fly.io** | TDD §1.3 (Hosting), §11 M1 | API + worker + PG hosting (default; PROGRESS.md already reopened this — Hetzner sketched as ~3–5x cheaper) |
| **Postgres 16 — "Neon or Fly PG"** | TDD §1.1, §1.3 | Primary DB |
| **Redis 7** | TDD §1.1, §1.3 | Celery queue + cache + rate limit |
| **Cloudflare R2** | TDD §1.1, §1.3, §3 (full key layout + lifecycles) | All object storage; "zero egress. This matters" |
| **"Cloudflare Images or R2 + resize worker"** | TDD §1.1 | Thumbnails — **already settled**: the built `thumbs.generate` Celery task is the resize worker (PROGRESS.md), Cloudflare Images not needed |
| **Resend** | TDD §1.3, §4; PROGRESS.md | Magic-link email delivery |
| **Sentry** | TDD §1.3 ("Both frontend and backend"), §11 M1 | Error tracking |
| **CI** | TDD §11 M1 ("repo, Docker, Fly, Postgres, migrations, magic-link auth, R2, CI, Sentry") | Regression gate (the 67-test pytest suite exists) |
| **Gemini 2.5 Flash** | TDD §1.1, §1.3, §8.1–8.2 | Segmentation: masks + labels + confidence, one call per image ever |
| **Seedream 5.0 Pro via fal.ai** | TDD §1.3, §8.9 (`fal-ai/bytedance/seedream/v5/pro/edit`), §13 | Primary image-edit adapter; "$0.0675 @1536px, $0.135 @2048px" |
| **Nano Banana 2 (gemini-3.1-flash-image)** | TDD §1.3, §8.9 | Fallback adapter, no mask API, post-hoc compositing equalizes |
| **Sanzo corpus** | TDD §2.3 — `github.com/mattdesl/dictionary-of-colour-combinations` | 159 colours / 348 combinations, seeded once, free, no account |
| **rembg** | TDD §8.1 | Optional local background removal, off by default — a Python lib, not a service |
| **PWA / domain / HTTPS** | PRD A6, §7 DoD; TDD §11 M4 | Share target requires an installed PWA on a real HTTPS origin |
| **PhotoRoom API** | PROGRESS.md added scope (not in PRD/TDD) | Background-removal derivative pipeline — **on hold**, key stored in `backend/.env` |
| **TripoSR (3D)** | PRD §3 "Explicitly out of V1" — V2 | No account needed now |
| **Shopify, cost tracking, manufacturing ops** | PRD §3 — V2+ | No account needed now |
| **Transcription of voice notes** | PRD §3 — V2 ("store the audio now, transcribe later") | No account needed now |
| **Multi-user / collaboration** | PRD §3 — V2+ (schema ready) | No new service; only email volume grows |
| **Native apps** | PRD §3 — never ("PWA only") | No Apple/Google developer accounts, ever, per contract |

Budget signals in the docs: M0 "**Under $10 spent**" (TDD §11); Wada "**Under $2 median**" per study (PRD §6); per-study cap **$10**, daily cap **$25**, hard freeze **$50** (PRD W10, TDD §8.11).

---

## 3. Current pricing (all retrieved 2026-07-16)

| Service | Free tier | Paid rate | Source |
|---|---|---|---|
| **fal.ai — Seedream 5.0 Pro edit** | none (prepaid credits) | **$0.0675**/output ≤1536×1536; **$0.135** ≤2048×2048; first input image free, +$0.0045 per extra input image | [fal.ai model page](https://fal.ai/models/bytedance/seedream/v5/pro/edit) (primary) |
| **Gemini API — Nano Banana 2** (`gemini-3.1-flash-image`) | no free image-output tier (AI Studio free interactive use exists) | **$0.045**/0.5K img, **$0.067**/1K, **$0.101**/2K, **$0.151**/4K (= $60/1M image tokens) | [ai.google.dev pricing](https://ai.google.dev/gemini-api/docs/pricing) (primary) |
| **Gemini API — 2.5 Flash** (segmentation) | **free tier exists** (rate-limited) | $0.30/1M input tokens (text/image), $2.50/1M output | [ai.google.dev pricing](https://ai.google.dev/gemini-api/docs/pricing) (primary) |
| **Resend** | **3,000 emails/mo** (100/day), 1 domain | Pro $20/mo (50k emails) | [resend.com/pricing](https://resend.com/pricing) (primary) |
| **Cloudflare R2** | **10 GB-month, 1M Class A, 10M Class B ops/mo; egress always $0** | $0.015/GB-mo, $4.50/M Class A, $0.36/M Class B | [developers.cloudflare.com/r2/pricing](https://developers.cloudflare.com/r2/pricing/) (primary) |
| **Tigris** (Fly-native alternative) | 5 GB storage, 10k Class A / 100k Class B req | $0.02/GB-mo, no egress fees | [tigrisdata.com/pricing](https://www.tigrisdata.com/pricing/) (located via search; per-GB and free-tier figures corroborated by [Tigris docs repo](https://github.com/tigrisdata/tigris-os-docs/blob/main/docs/pricing/index.md)) |
| **Sentry** | Developer: **5k errors/mo, 1 user**, 30-day lookback | Team $26/mo | [sentry.io/pricing](https://sentry.io/pricing/) (primary) |
| **PhotoRoom API** | **1,000 sandbox calls/mo** (watermarked) + 10 free prod calls | Basic (remove-bg) **$0.02/img**; Plus (AI editing) $0.10/img | [photoroom.com/api/pricing](https://www.photoroom.com/api/pricing) (primary) |
| **Hetzner Cloud CX23** | — | **€5.49/mo ($6.49)** — 2 vCPU, 4 GB RAM, 40 GB SSD, 20 TB traffic; **+~€0.50/mo IPv4**; price effective 15 Jun 2026 | price: [Hetzner price-adjustment notice](https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/) (primary); specs: [hetzner.com/cloud/cost-optimized](https://www.hetzner.com/cloud/cost-optimized); IPv4 €0.50: [bitdoze.com](https://www.bitdoze.com/hetzner-cloud-cost-optimized-plans/) (secondary) |
| **Hetzner backups/snapshots** | — | automated backups = **20% of plan** (~$1.30/mo on CX23); snapshots €0.0143/GB-mo | [hetsnap.com comparison](https://hetsnap.com/blog/hetzner-cloud-backup-vs-snapshot-pricing-comparison) (secondary — Hetzner's own info box wasn't fetchable) |
| **Fly.io machines** | **no free allowances for new customers** | shared-cpu-1x: 256MB **$2.02/mo**, 512MB **$3.32**, 1GB **$5.92** (AMS); volumes $0.15/GB-mo; snapshots $0.08/GB-mo (first 10GB free) | [fly.io/docs/about/pricing](https://fly.io/docs/about/pricing/) (primary) |
| **Fly Managed Postgres** | — | Basic **$38/mo** (shared-2x, 1GB) + $0.28/provisioned GB | [fly.io/docs/mpg](https://fly.io/docs/mpg/) (primary) |
| **Neon Postgres** | **0.5 GB storage + 100 CU-hrs per project** | usage-based: $0.106/CU-hr, $0.35/GB-mo, no minimum | [neon.com/pricing](https://neon.com/pricing) (primary) |
| **Upstash Redis** | **256 MB, 500K commands/mo** | $0.20/100K commands | [upstash.com/pricing](https://upstash.com/pricing) (primary) |
| **Domain (.com)** | — | **$10.46/yr** at cost via Cloudflare Registrar | [cfdomainpricing.com](https://cfdomainpricing.com/) (secondary tracker of Cloudflare's at-cost prices; Cloudflare Registrar is [no-markup by policy](https://www.cloudflare.com/products/registrar/)) |
| **GitHub Actions CI** | **2,000 min/mo** on private repos (unlimited public) | — | [github.com/pricing](https://github.com/pricing) (primary) |
| **TLS** | Let's Encrypt via Caddy/certbot (Hetzner) or automatic on Fly | $0 | common knowledge, not separately cited |

Key validation: **the TDD's `$0.0675 @1536px / $0.135 @2048px` (§1.3, §8.9) matches fal.ai's current price exactly**, so every cost table in PRD §4.1 and TDD §8.4 is still arithmetically current as of today.

---

## 4. Per-milestone needs (what's needed, and not before)

| M | Scope (TDD §11) | External needs — first time they're actually needed | Cost | Free-tier viable? | Setup effort |
|---|---|---|---|---|---|
| **M0** | Bake-off (blocked on keys) | **fal.ai account + key** (prepaid credit); **Gemini API key with billing enabled** (NB2 has no free image-output tier); 3 real product photos | one-time **~$2–5** *(inference: ~3 photos × 2 models × ~5–8 edits ≈ 30–50 calls × ~$0.07)* — comfortably under the $10 cap (TDD §11) | segmentation experiments: yes (2.5 Flash free tier); NB2 generations: no | ~30 min: two consoles, set spend alerts |
| **M1** | Foundation (DONE except "real URL" DoD) | **Hosting** (Hetzner CX23 *or* Fly), **domain**, **R2 bucket + API token** (or Tigris), **Resend key + verified domain**, **Sentry** (2 DSNs), **GitHub Actions CI** | recurring: see §6 scenarios; domain $10.46/yr | Resend/Sentry/R2/CI: yes. Host: no (but ≤$10/mo) | ~half a day for the full deploy stack |
| **M2** | Archive core (DONE + verified) | none beyond M1. Local dev runs on Docker MinIO/PG/Redis with $0 external | $0 | — | done |
| **M3** | Gallery + share link (in flight) | none new. Share links only become *useful* once the M1 real URL exists — deploy is the gating item, not a new account | $0 | — | — |
| **M4** | PWA + inbox triage | **real HTTPS domain required** (share target only works on an installed PWA over HTTPS — PRD A6, §7 DoD); `pillow-heif` (free lib) for iPhone HEIC (PROGRESS.md) | $0 incremental | yes | none if M1 deploy done |
| **M5** | Segmentation + Slot Composer | **Gemini key** (already exists from M0); **Sanzo corpus seed** — free one-time import from [mattdesl/dictionary-of-colour-combinations](https://github.com/mattdesl/dictionary-of-colour-combinations) (TDD §2.3) | segmentation ~$0.01–0.25/image *(inference — see §5)*, once per image ever | partially (2.5 Flash free tier may cover it) | corpus seed script ~1 hr |
| **M6** | Permutation engine + cache | **none** — pure math + Postgres/Redis. The estimate endpoint spends nothing (TDD §9) | $0 | — | — |
| **M7** | Generation + contact sheet | **fal.ai key** (exists from M0) with real credit; **R2 lifecycle rules configured** (node/ 7d, cw/ 30d-unless-pinned, export/ 7d — TDD §3); **budget caps set** (TDD §8.11) | per-study $0.5–4.5 (see §5) | no (model spend) | lifecycle rules ~30 min |
| **M8** | Palette library + shortlist + export | none new. Export cost depends on an open product decision — see §5 note | $0–0.135/export | — | — |
| **Added scope** | PhotoRoom cutouts (ON HOLD) | key already stored in `backend/.env`; sandbox free for dev; prod billing when unblocked | $0.02/image (Basic remove-bg — the correct tier) | dev: yes (1,000 sandbox calls/mo) | key rotation before prod (see §7) |
| **V2+ (out of V1)** | TripoSR 3D, transcription, Shopify, multi-user | **nothing now.** When they come: TripoSR = GPU inference (fal/Replicate); transcription = e.g. Whisper-class API; Shopify = own subscription; multi-user only grows email volume (Resend free tier = 3,000/mo, fine for many users) | $0 today | — | zero — do not create these accounts |

---

## 5. Wada model-cost reality check (V1.5)

**Inputs, all from cited current prices:** Seedream edit = $0.0675/call @1536px ([fal.ai](https://fal.ai/models/bytedance/seedream/v5/pro/edit)); NB2 = $0.067/1K, $0.101/2K image ([Google](https://ai.google.dev/gemini-api/docs/pricing)). The pipeline works at 1536px long edge (TDD §8.1); a colorway = a chain of K (or C) calls cached in a trie (TDD §8.8).

Trie node counts are exact combinatorics (verified against TDD §8.4's own table):

| Study shape | Perms | Model calls (trie) | Cost @ $0.0675 | Verdict vs <$2 median (PRD §6) |
|---|---|---|---|---|
| K=C=3, all 6 (the "recommended shape", PRD §4.2) | 6 | 15 | **$1.01** | ✅ matches TDD §8.4 and PRD DoD line exactly |
| K=C=4, **capped at 12** (default policy, TDD §8.6) | 12 of 24 | 32–40 *(computed: 12 leaves + 12 depth-3 + 6–12 depth-2 + 2–4 depth-1; diversity sampling pushes toward the high end)* | **$2.16–$2.70** | ⚠️ marginally over $2 if fully generated |
| K=C=4, full space | 24 | 64 | **$4.32** | matches TDD §8.4; within the $10 study cap |
| K=C=4, eager-2 only (progressive default, TDD §8.6 `eager: 2`) | 2 up front | ≤8 | **≤$0.54** | ✅ the rest generate on demand |
| K=4, C=3 (worst common shape) capped at 12 | 12 of 36 | ≤~40 *(inference)* | ~$2.4–2.7 | ⚠️ same story |
| Plus per study: segmentation | 1 Gemini 2.5 Flash call per image, ever (TDD §8.1) | — | ~$0.001 input; output masks are base64 PNGs whose token count I could not verify — **$0.01–$0.25/image** *(inference; possibly $0 on the free tier)* | negligible either way |

**Does <$2 median hold? Yes, with the design's own guardrails.** The recommended K=C=3 shape is $1.01. The eager-2 + on-demand-ghost pattern (TDD §8.13.2) means a typical study spends $0.5–1.5 unless the user deliberately generates a full capped sheet. A user who always generates all 12 of a K=C=4 study will average ~$2.4 — slightly over. The target is a *median*, and the Slot Composer's whole pedagogy (PRD §4.2) pushes toward K=3. **The $10/study and $25/day caps (TDD §8.11) bound the tail regardless.** Sanity check passes; no doc number needs revision.

**Which model is price-favored today?** **Seedream 5.0 Pro, narrowly, at the pipeline's native resolution.** NB2 has no 1536px tier: to match the working resolution you'd generate 2K at **$0.101** (50% more than Seedream's $0.0675) and downsample, or accept 1K at **$0.067** (a price tie, but below working resolution). Seedream also accepts masks natively (TDD §8.9), reducing reliance on compositing to fix boundaries. NB2 becomes price-favored only for 2K+ output ($0.101 vs $0.135). So M0 stays a *quality* bake-off — price does not decide it. *(Batch-API 50% discounts exist for Gemini text models per the official page; whether batch applies to NB2 image output I could not confirm from a primary source — and Wada's interactive/progressive design mostly can't use batch anyway.)*

**Open product decision affecting M8 cost** *(inference)*: W11 "2K PNG export, cost shown before confirming" (PRD) vs TDD §3 "export/… regenerable from the colorway". If export = Pillow upscale of the stored 1536 colorway, it's ~$0; if it's a re-generation at 2048, it's $0.135 (Seedream) or $0.101 (NB2) per export. Worth pinning down before M8.

**M0 budget check:** ~30–50 edit calls ≈ $2–4 across both models — the "<$10" constraint (TDD §11) holds with 2–3x headroom.

---

## 6. Monthly cost projection at Beezy's scale

Assumed scale *(inference from PRD §6 targets)*: single user, ~5 captures/week (~22 media/mo ≈ ~0.1 GB/mo originals + thumbs), occasional Wada (call it 2 studies/mo median shape). Storage grows ~1.2–1.5 GB/yr — **R2's 10 GB free tier covers roughly 6+ years**; ops counts are thousands/mo vs millions free. Storage = $0 in every scenario.

| Line item | **A: Hetzner CX23 + R2** (recommended in PROGRESS.md) | **B: Fly lean** (machines + Neon free + Upstash free + R2) | **B′: Fly self-hosted PG** (closer to TDD topology) | **C: Fly + Managed PG** |
|---|---|---|---|---|
| Compute | CX23 $6.49 + IPv4 ~$0.60 (runs API+worker+PG+Redis, the existing compose stack as-is) | API 512MB $3.32 + worker 256MB $2.02 | + PG machine 1GB $5.92 + 10GB volume $1.50 | + MPG Basic $38.00 |
| Postgres | on-box $0 | Neon free ($0; 0.5 GB cap — fine for metadata-only DB for a long time) | self-managed on volume | managed |
| Redis | on-box $0 | Upstash free $0 | on worker machine $0 | Upstash free $0 |
| Object storage | R2 free $0 | R2 or Tigris free $0 | R2 free $0 | R2 free $0 |
| Backups | Hetzner auto-backups (20%) ~$1.30 + pg_dump-to-R2 cron $0 | Neon has PITR built in; $0 | volume snapshots (first 10GB free) ~$0 + pg_dump cron | included in MPG |
| Email / errors / CI / TLS | $0 / $0 / $0 / $0 (Caddy+LE) | $0 (Fly TLS automatic) | $0 | $0 |
| Domain (amortized) | $0.87 | $0.87 | $0.87 | $0.87 |
| **Fixed total / mo** | **≈ $9.3** | **≈ $6.2** | **≈ $13.6** | **≈ $44** |
| **+ model usage** (2 median studies + segmentation) | **~$2–5/mo, only in Wada months** | same | same | same |

Prices per §3 citations. Scenario B's low sticker hides two extra vendor relationships (Neon, Upstash) and Fly's pay-as-you-go egress; scenario A is one box you already know how to run (`docker compose up -d` is literally the current stack). **C is listed to show what you're *not* buying** — Fly Managed Postgres alone costs 4x scenario A. This is consistent with, and sharpens, the PROGRESS.md hosting sketch (Fly $8–25 vs Hetzner ~3–5x less).

**Realistic annual all-in (scenario A): ~$110 infra + ~$25–60 of model spend + $10.46 domain ≈ $150–180/year.**

---

## 7. Operational obligations (the recurring human work)

1. **Backups — DB**: nightly `pg_dump` cron shipped to R2 (free tier absorbs it) **plus** Hetzner automated backups (~$1.30/mo, 7 slots) on scenario A. On Fly: Neon PITR (B) or volume snapshots + pg_dump (B′). Test a restore once before calling it done.
2. **Backups — bucket**: R2 is the *primary* store for originals and pinned colorways ("Never expire", TDD §3). Periodic `rclone sync` of `src/`, `audio/`, and `cw/pinned/` prefixes to a second location (second bucket or the Hetzner box's disk). *(Inference: the docs specify lifecycles but no bucket backup; originals are irreplaceable.)*
3. **R2 lifecycle rules** must actually be configured at deploy: `node/` 7d after last use, `cw/` 30d unless pinned, `export/` 7d (TDD §3). And the **pin-copies-the-object** rule (TDD §3) is an operational correctness requirement, not a nicety.
4. **Monitoring**: Sentry free tier, both frontend and backend DSNs (TDD §1.3). Add a free uptime ping (healthchecks.io/UptimeRobot) on the API **and a Celery-worker liveness check** — a dead worker silently stops thumbs and Wada.
5. **CI**: GitHub Actions free tier; the 67-test pytest suite (~2.5s) needs service containers (PG/Redis/MinIO) — trivially within 2,000 min/mo.
6. **Domain/TLS**: Cloudflare Registrar renewal ($10.46/yr); TLS auto-renews (Caddy/LE or Fly). PWA share target (M4) is dead without HTTPS on the real domain.
7. **Email deliverability**: verify the domain in Resend and publish SPF + DKIM (+DMARC) records — a magic link that lands in spam is a login outage. Volume (a few links/week) is ~0.1% of the free tier.
8. **Key rotation before prod**: the **PhotoRoom key transited chat** before landing in `backend/.env` (PROGRESS.md) — rotate it. Same rule for any fal.ai/Gemini keys that get pasted into chats during M0. Generate a fresh prod `JWT_SECRET` and `SECRET`s per environment; scope the R2 API token to the one bucket.
9. **Budget guard config**: set `study_cap`/`daily_cap`/`hard_freeze` (TDD §8.11) and spend alerts inside the fal.ai and Google consoles *as well* — the in-app ledger only guards what goes through the app; a leaked key doesn't respect it.
10. **Model-price watch**: the adapter interface exists because "the image-model landscape is moving monthly" (TDD §8.9). Re-check fal/Gemini prices at each Wada milestone; `cost_cents_per_call` is config.

---

## 8. What you do NOT need (at this scale)

| Looks needed | Why you don't need it |
|---|---|
| **Fly Managed Postgres ($38/mo)** | 4x the cost of the entire Hetzner scenario for a metadata DB measured in megabytes |
| **Cloudflare Images** | TDD §1.1 offered it as an *alternative*; the built `thumbs.generate` worker is the chosen path (PROGRESS.md) |
| **Sentry Team ($26/mo) / Resend Pro ($20/mo) / Neon paid** | single-user volumes are 0.1–1% of the free tiers |
| **PhotoRoom Plus tier ($0.10/img)** | the use case is background removal → Basic at $0.02/img; sandbox is free for all dev |
| **2 API replicas + workers autoscaling to 6** (TDD §1.1) | that's the tenancy-ready *architecture*; deploy 1 API + 1 worker. Stateless design means scaling later is a knob, not a rewrite |
| **A CDN** | R2 egress is $0 and Hetzner includes 20 TB; a personal portfolio link needs neither |
| **Both R2 and Tigris** | pick one at deploy; the S3 adapter makes it an env-var change (PROGRESS.md) |
| **Kubernetes/Terraform/managed CI beyond GitHub Actions** | one compose file on one box is the whole system |
| **Paid uptime/APM/log platforms** | Sentry free + a free pinger covers a single-user app |
| **TripoSR GPU hosting, transcription API, Shopify** | V2/V2+ by contract (PRD §3); creating them now is pure carrying cost |
| **Apple/Google developer accounts** | "Native apps — PWA only" (PRD §3) |
| **Multi-region / HA anything** | recovery target is "restore last night's dump", not five nines |

---

## 9. Recommended setup order

1. **Now — unblock M0** (~30 min, one-time ~$5 total): create **fal.ai** account, buy minimum credit, set spend alert. Create **Google AI Studio / Gemini API** key, enable billing, set budget alert. Hand both keys over via env files, never chat. Shoot the 3 product photos. *(These two keys then carry M5–M8 too.)*
2. **Deploy week — finish M1's "real URL" DoD (do before or with M3, since share links need a public origin)**: **Cloudflare** account → register domain ($10.46/yr) → create R2 bucket + scoped token + lifecycle rules; **Hetzner** account → CX23 + backups (or Fly, if managed deploys win the trade-off — decision already framed in PROGRESS.md); **Resend** → verify domain, SPF/DKIM; **Sentry** → free org, 2 DSNs; **GitHub Actions** workflow for the pytest suite. Rotate every key that ever touched a chat (PhotoRoom included). Fresh prod JWT secret.
3. **M4**: nothing new — it consumes the domain/HTTPS from step 2.
4. **M5–M8**: seed the Sanzo corpus (free, one-time); set TDD §8.11 budget caps + provider-console spend alerts before the first real generation.
5. **When un-held**: PhotoRoom Basic billing ($0.02/img) — after rotating the key.
6. **Never (until V2 is a real plan)**: TripoSR/GPU, transcription, Shopify, native-app accounts.

---

## 10. Source index (all retrieved 2026-07-16)

| # | Claim | URL | Type |
|---|---|---|---|
| 1 | Seedream 5.0 Pro edit $0.0675/≤1536², $0.135/≤2048², +$0.0045 extra input | https://fal.ai/models/bytedance/seedream/v5/pro/edit | primary |
| 2 | NB2 $0.045–0.151/img by resolution; 2.5 Flash $0.30/$2.50 per 1M tok; free tier exists for 2.5 Flash | https://ai.google.dev/gemini-api/docs/pricing | primary |
| 3 | Resend free 3,000/mo (100/day), Pro $20 | https://resend.com/pricing | primary |
| 4 | R2 free 10GB/1M A/10M B; $0.015/GB-mo; $0 egress | https://developers.cloudflare.com/r2/pricing/ | primary |
| 5 | Sentry Developer free 5k errors; Team $26 | https://sentry.io/pricing/ | primary |
| 6 | PhotoRoom sandbox 1,000/mo free; Basic $0.02; Plus $0.10 | https://www.photoroom.com/api/pricing | primary |
| 7 | CX23 €5.49/$6.49 from 15 Jun 2026 | https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/ | primary |
| 8 | CX23 specs 2vCPU/4GB/40GB | https://www.hetzner.com/cloud/cost-optimized | primary |
| 9 | IPv4 +€0.50/mo | https://www.bitdoze.com/hetzner-cloud-cost-optimized-plans/ | secondary |
| 10 | Hetzner backups 20% of plan; snapshots €0.0143/GB | https://hetsnap.com/blog/hetzner-cloud-backup-vs-snapshot-pricing-comparison | secondary |
| 11 | Fly machines $2.02/$3.32/$5.92; volumes $0.15/GB; no new-customer free tier | https://fly.io/docs/about/pricing/ | primary |
| 12 | Fly Managed Postgres Basic $38/mo | https://fly.io/docs/mpg/ | primary |
| 13 | Tigris 5GB free, $0.02/GB, no egress | https://www.tigrisdata.com/pricing/ + https://github.com/tigrisdata/tigris-os-docs/blob/main/docs/pricing/index.md | primary-adjacent (docs repo) |
| 14 | Neon free 0.5GB/100 CU-hrs; usage rates | https://neon.com/pricing | primary |
| 15 | Upstash Redis free 256MB/500K cmds | https://upstash.com/pricing | primary |
| 16 | .com $10.46/yr via Cloudflare Registrar | https://cfdomainpricing.com/ (tracker) + https://www.cloudflare.com/products/registrar/ (at-cost policy) | secondary + primary policy |
| 17 | GitHub Free: 2,000 Actions min/mo private | https://github.com/pricing | primary |
| 18 | CX23 20TB included traffic | https://www.hetzner.com/cloud/cost-optimized (also corroborated by search results incl. https://comparedge.com/tools/hetzner/pricing) | primary + secondary |

**Explicitly unverified / flagged:** Gemini segmentation *output*-token cost per image (mask base64 volume unknown — bounded estimate given); whether Gemini Batch pricing applies to NB2 image output; whether M8 export re-generates at 2K or upscales locally (product decision, cost noted both ways).
