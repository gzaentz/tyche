# TYCHE provider capability catalog

This is a live capability catalog, not a routing table. Build several signal
hypotheses, pilot materially different routes one at a time, inspect the rows
and receipts, and keep only companies that pass the final account gate. The
local adapters are the only supported runtime boundary for this skill.

## Deepline wrapper

`scripts/deepline.py` adapts the installed Deepline CLI. It discovers tools,
schemas, prices, and bounded company, signal, or contact candidates. A catalog
hit is not company or contact evidence. A disconnected tool is not an empty
result.

### Capability discovery

Search the live catalog with narrow seeds that match the hypothesis. These are
search seeds, not fixed IDs; use only the tool ID returned by the current search:

- `companies with current hiring or job postings`
- `companies with recent funding, press, or product news`
- `companies with paid advertising or campaign activity`
- `companies with recent patents, facilities, openings, or expansion`
- `companies with official video or event activity`
- `company firmographic search by industry geography and size`
- `people by company domain and requested job title`
- `current title roster or leadership team by company domain`
- `ZeroBounce validate one known email address`

For each selected tool, call `describe` immediately before `execute` and record
its live input schema, connection state, and price. Prefer a title-roster tool
for nuanced roles. If that is unavailable, use broad function and seniority with
the full user-approved title family. Do not use a CEO as an automatic fallback.

```bash
python3 .agents/skills/lead-sourcing/scripts/deepline.py --input '{"operation":"search","query":"companies with current hiring or job postings"}'
python3 .agents/skills/lead-sourcing/scripts/deepline.py --input '{"operation":"describe","tool":"<id returned by search>"}'
python3 .agents/skills/lead-sourcing/scripts/deepline.py --input '{"operation":"execute","tool":"<id returned by search>","payload":{"query":"<bounded query>","limit":10}}'
```

`execute` is paid. One route pilot has one paid call and at most 10 returned
rows. Inspect the live price first; expand only when rows are relevant, diverse,
and evidentiary. The wrapper invokes `deepline tools search`, `describe`, or
`execute`, writes a temporary payload file, redacts secrets, and emits one JSON
object. Catalog calls default to 30 seconds (cap 120); execute calls
default to 240 seconds (cap 780). Never automatically retry an uncertain call.

The wrapper normalizes candidate fields to `company`, `domain`, `signal`,
`evidence_url`, `evidence_date`, `evidence_text`, `provider`, and `tool`.
Generic labels such as `search_result`, `web_page`, `company_profile`, and
`hiring` are discovery labels only. For web-search rows, `domain` can be the
source host; resolve the canonical company domain before acceptance.

### Deepline status contract

The wrapper emits exactly: `ok`, `no_results`, `partial`, `rate_limited`,
`auth_failed`, `quota_exceeded`, `timeout`, `schema_error`, `provider_error`,
or `config_error`. Only `ok` and `partial` can supply candidates. `no_results`
does not prove absence. Other statuses are unresolved provider outcomes; stop or
change route and retain `status`, `error` when present, `provider`, `operation`,
`tool`, and normalized `results` in the report.

### Deepline ZeroBounce email gate

When the effective contact fields include email, first find and verify the
person and current role. Then search the live Deepline catalog for a ZeroBounce
single-address validation capability. Select only a result whose current
description identifies ZeroBounce email validation, and call `describe` to
confirm its input, output, connection, and price. The catalog tool ID is runtime
data; do not copy a fixed ID into this skill.

```bash
python3 .agents/skills/lead-sourcing/scripts/deepline.py --input '{"operation":"search","query":"ZeroBounce validate one known email address"}'
python3 .agents/skills/lead-sourcing/scripts/deepline.py --input '{"operation":"describe","tool":"<current ZeroBounce validation tool from search>"}'
python3 .agents/skills/lead-sourcing/scripts/deepline.py --input '{"operation":"execute","tool":"<same described tool>","entity_type":"email_validation","payload":{"email":"person@example.com"},"limit":1}'
```

Use the exact payload names returned by the live description; the example
shows the current scalar shape but does not override the live schema. The
adapter preserves the provider row and exposes `email`, `email_status`, and
`email_sub_status` for a recognized scalar validation result. Link the final
receipt to a unique `email_validation` route and record actual usage.

Apply TYCHE's current gate to the explicit ZeroBounce status after trimming and
case normalization. Only `invalid` fails. Every other explicit status passes,
including `catch-all`, `spamtrap`, `abuse`, `do_not_mail`, and `unknown`. This
is intentionally more permissive than ZeroBounce's normal outbound guidance.
A missing status, `no_results`, provider error, timeout, or other uncertain
call is unresolved and cannot support an accepted email. Do not retry an
uncertain paid validation call automatically. Deepline owns the provider
credential; do not require or read a direct ZeroBounce key.

## ScrapingDog wrapper

`scripts/scrapingdog.py` is the only supported local ScrapingDog adapter here.
It makes one bounded GET request, redacts secrets, and returns normalized rows.
Its current supported canonical operation set is 25 operations. Aliases are
accepted for compatibility, but use canonical names in new requests and
receipts. Do not call an endpoint directly from this skill.

### Request envelope and bounds

- The request key is `operation` or the compatibility key `op`. The value is
  case-insensitive and must be a canonical operation or an alias listed below.
- The API key is read only from `SCRAPINGDOG_API_KEY`. `api_key` in the JSON
  request and a `base_url` override are rejected.
- `limit` is optional, defaults to `10`, and is capped at `20`. It caps returned
  normalized rows and bounds provider `results` or `num` where those inputs
  exist. Single-object operations still accept this common control.
- `timeout_seconds` is optional, defaults to `30`, and is capped at `60`.
- The tables list operation-specific keys. Every operation also accepts the
  common `limit` and `timeout_seconds` keys. An omitted optional key is not sent
  to the provider, except where the adapter always sends a bounded `results` or
  `num` value as noted.

| Canonical operation (aliases) | Exact endpoint | Required request keys | Optional forwarded keys | Planning credits and conservative output note |
|---|---|---|---|---|
| `google_search` | `GET https://api.scrapingdog.com/google` | `query` or compatibility `q` | `results`, `page`, `country`, `language`, `domain`, `advance_search`, `mob_search` | 5 standard; 10 with advanced or mobile search. Search URLs need exact-page verification. |
| `universal_search` | `GET https://api.scrapingdog.com/search` | `query` or `q` | `country`, `language` | 20. A search result is not evidence until its source is verified. |
| `scrape` | `GET https://api.scrapingdog.com/scrape` | `url` or compatibility `target_url` (HTTP(S)) | `dynamic`, `premium`, `wait`, `country` | 1 base; public estimates are 5 for JS rendering, 10 for premium proxy, 25 for both, and 10 with country (confirm combinations/current plan). Returns bounded normalized evidence, not a full raw payload. |
| `linkedin_company` | `GET https://api.scrapingdog.com/profile` (`type=company`, `id`) | `id` or `company_id`, or `url`/`company_url` with a LinkedIn company URL | none | Profile API is plan/field dependent (plan for 10–100). Profile facts can support identity or fit, not a buying signal. |
| `linkedin_person` (`linkedin_profile`, `linkedin_person_profile`) | `GET https://api.scrapingdog.com/profile` (`type=profile`, `id`) | `id`, `profile_id`, or `public_identifier`, or `url`, `profile_url`, `person_url`, or `linkedin_url` with a LinkedIn person URL | `premium`, `webhook` | Plan for 50 credits normally or 100 for a protected profile, then confirm the current plan. This is exact-profile current-role verification, not broad contact discovery. A `webhook=202` response is unsupported polling and remains unresolved. |
| `linkedin_job` (`linkedin_job_details`, `linkedin_job_overview`) | `GET https://api.scrapingdog.com/jobs` (`job_id`) | `job_id` or `id`, or `url`, `job_url`, `job_link`, or `linkedin_url` with a LinkedIn job URL | none | 5. One exact job detail; normalizes hiring URL/text/date when present. |
| `google_jobs` | `GET https://api.scrapingdog.com/google_jobs` | `query` or `q` | `country`, `language`, `uule`, `domain`, `next_page_token`, `chips`, `lrad`, `ltype`, `uds` | 5. Normalized job/company/link/date fields may be null; relative provider dates stay as returned. |
| `linkedin_jobs` | `GET https://api.scrapingdog.com/jobs` (`field`) | `field`, or compatibility `query`/`q` (copied to `field`) | `geoid`, `location`, `page`, `sort_by`, `job_type`, `exp_level`, `work_type`, `filter_by_company` | 5. One bounded page; only recognized response shapes become rows. |
| `google_maps` (`google_maps_search`, `google_maps_lookup`) | `GET https://api.scrapingdog.com/google_maps` | `query` or `q` | `ll`, `domain`, `language`, `country`, `data`, `place_id`, `type`, `page`; `page` requires `ll` | 5. Place IDs and listing facts are metadata, not proof without source verification. |
| `google_maps_place` (`google_place`, `google_places`, `google_maps_places`) | `GET https://api.scrapingdog.com/google_maps/places` | one of `data_id`, `place_id`, `ludocid` | `country` | 5. One place detail; a place ID is not a fabricated source URL. |
| `google_local` | `GET https://api.scrapingdog.com/google_local` | `query` or `q` | `location`, `uule`, `country`, `language`, `domain`, `ludocid`, `tbs`, `page` | 5. Local listing fields are candidates and need identity/source verification. |

```bash
python3 .agents/skills/lead-sourcing/scripts/scrapingdog.py --input '{"operation":"google_search","query":"<company> funding announcement","limit":10}'
python3 .agents/skills/lead-sourcing/scripts/scrapingdog.py --input '{"operation":"universal_search","query":"<company> hiring","limit":10}'
python3 .agents/skills/lead-sourcing/scripts/scrapingdog.py --input '{"operation":"scrape","url":"https://example.com/news/<article>","dynamic":false,"limit":1}'
python3 .agents/skills/lead-sourcing/scripts/scrapingdog.py --input '{"operation":"linkedin_company","url":"https://www.linkedin.com/company/<slug>","limit":1}'
python3 .agents/skills/lead-sourcing/scripts/scrapingdog.py --input '{"operation":"linkedin_person","url":"https://www.linkedin.com/in/<public-id>","limit":1}'
python3 .agents/skills/lead-sourcing/scripts/scrapingdog.py --input '{"operation":"linkedin_job","job_id":"<linkedin-job-id>","limit":1}'
python3 .agents/skills/lead-sourcing/scripts/scrapingdog.py --input '{"operation":"google_jobs","query":"<company> engineer","country":"us","limit":10}'
python3 .agents/skills/lead-sourcing/scripts/scrapingdog.py --input '{"operation":"linkedin_jobs","field":"<company> engineer","geoid":"90000084","page":1,"limit":10}'
python3 .agents/skills/lead-sourcing/scripts/scrapingdog.py --input '{"operation":"google_maps","query":"<company> offices","country":"us","limit":10}'
python3 .agents/skills/lead-sourcing/scripts/scrapingdog.py --input '{"operation":"google_maps_place","data_id":"<maps-data-id>","limit":1}'
python3 .agents/skills/lead-sourcing/scripts/scrapingdog.py --input '{"operation":"google_local","query":"<industry> in <city>","country":"us","limit":10}'
```

The wrapper reads only `SCRAPINGDOG_API_KEY` from the environment. Never put a
key in JSON, shell history, or an artifact. Paid calls are one-call pilots with
no automatic retry. Treat listed prices as planning estimates: confirm the
current plan before spending, and record the live estimate in the route receipt.
The wrapper does not report invoice usage.

When a response includes a provider page token, the wrapper returns it as
`continuation_cursor`. It searches the top level and nested `pagination`,
`scrapingdog_pagination`, `data`, `result`, or `response` objects. Pass a cursor
as `next_page_token` only after current rows justify another explicitly budgeted
paid call; this is expansion, not retry.

### Implemented conditional operations

These operations are implemented by the local adapter and use the same response
shape, evidence, budget, and no-retry rules as the core routes.

| Canonical operation (aliases) | Exact endpoint | Required request keys | Optional forwarded keys and constraints | Planning credits and conservative output note |
|---|---|---|---|---|
| `google_ai_mode` | `GET https://api.scrapingdog.com/google/ai_mode` | `query` or `q` | `country`, `language`, `uule`, `location`, `safe`, `html`; `uule` and `location` cannot both be set | 10. Normalizes answer/text blocks and references into common evidence fields when recognized; text without a source URL is not accepted account evidence. |
| `google_news` | `GET https://api.scrapingdog.com/google_news` | `query` or `q` | `results`, `country`, `page`, `domain`, `language`, `lr`, `uule`, `tbs`, `safe`, `nfpr`, `html`; `results` is always bounded by `limit` | 5. Normalizes headline/snippet/link/date fields; relative provider dates stay as returned. |
| `linkedin_post` | `GET https://api.scrapingdog.com/profile/post` (`id`) | `id` or `post_id` | none | 5. The public post schema is not fixed. Only recognized content/identity shapes become rows; unknown successful shapes are `schema_error`. |
| `x_profile` | `GET https://api.scrapingdog.com/x/profile` (`profileId`) | `profileId`, `profile_id`, or `id` | none | 5. Normalizes profile text and source URL when present; it does not establish a company or current contact role by itself. |
| `x_post` | `GET https://api.scrapingdog.com/x/post` (`tweetId`) | `tweetId`, `tweet_id`, or `id` | none | 5. Normalizes post text and source URL when present; post identity is not company identity without corroboration. |
| `youtube_search` | `GET https://api.scrapingdog.com/youtube/search` | `search_query` | `country`, `language`, `sp` (filters or provider next-page token) | 5. Combines recognized `channel_results`, `video_results`, `shorts_results`, and `movie_results` arrays before applying `limit`; common title/link/date/text fields may be null. |
| `youtube_video` | `GET https://api.scrapingdog.com/youtube/video` | `v` or `video_id`, or a supported YouTube `url` | `country`, `language` | 5. Normalizes video title/description/link and selected metadata; a video is not a company signal without dated, company-linked evidence. |
| `youtube_transcript` (`youtube_transcripts`) | `GET https://api.scrapingdog.com/youtube/transcripts` | `v` or `video_id`, or a supported YouTube `url` | `country`, `language` | 1. Normalizes transcript text from a recognized transcript shape; text alone may lack a date or company URL. |
| `google_ads_transparency` | `GET https://api.scrapingdog.com/google/ads_transparency` | at least one of `advertiser_id` or `text` | `platform` only `PLAY`, `MAPS`, `SEARCH`, `SHOPPING`, `YOUTUBE`; `political_ads`, `region`, `start_date`, `end_date`, `creative_format`, `next_page_token`, `html`, `num`; truthy `political_ads` requires `region`; `num` is always sent bounded by `limit` | 5. Reads the official `ad_creatives` envelope; converts `last_shown` or `first_shown` Unix time to an ISO UTC date and retains the creative ID and format. |
| `google_patents` | `GET https://api.scrapingdog.com/google_patents` | `query` or `q` | `num`, `page`, `sort`, `clustered`, `dups`, `patents`, `scholar`, `before`, `after`, `inventor`, `assignee`, `country`, `language`, `status`, `type`, `litigation`; `num` is always sent bounded by `limit` | 5. Normalizes patent title, abstract, assignee, identifier, URL, and date when present; a patent result is not a buying signal without current company-linked evidence. |
| `google_patent_details` | `GET https://api.scrapingdog.com/google_patents/details` | `patent_id` | `language`, `html` | 5. One exact patent detail; unknown or empty detail envelopes remain `no_results` or `schema_error`, not inferred facts. |
| `tiktok_profile` | `GET https://api.scrapingdog.com/tiktok/profile` | `username` | none | 5. Normalizes profile text and URL when present; profile data does not prove a company buying signal. |
| `tiktok_post` | `GET https://api.scrapingdog.com/tiktok/post` | `url`, or both `username` and `post_id` | none | 5. A URL request sends only `url`; the ID form sends `username` and `post_id`. Normalizes post text and URL, and converts a Unix `created_at` value to an ISO UTC date. |
| `tiktok_ads` | `GET https://api.scrapingdog.com/tiktok/ads` | at least one of `query` or `advertiser_id` | `query_type` only `1` or `2`, `country`, `time_period`, `sort_by`, `next_page_token`; type 1 requires `query`, type 2 requires `advertiser_id`; advertiser-only input defaults to type 2 | 5. Normalizes the official ad name, first/last shown dates, ID, type, audience/spend/impression metadata, and first supplied video or image URL. Empty ad envelopes are `no_results`. |

```bash
python3 .agents/skills/lead-sourcing/scripts/scrapingdog.py --input '{"operation":"google_ai_mode","query":"<company> expansion","limit":10}'
python3 .agents/skills/lead-sourcing/scripts/scrapingdog.py --input '{"operation":"google_news","query":"<company> funding","limit":10}'
python3 .agents/skills/lead-sourcing/scripts/scrapingdog.py --input '{"operation":"linkedin_post","post_id":"<post-id>","limit":1}'
python3 .agents/skills/lead-sourcing/scripts/scrapingdog.py --input '{"operation":"x_profile","profile_id":"<profile-id>","limit":1}'
python3 .agents/skills/lead-sourcing/scripts/scrapingdog.py --input '{"operation":"x_post","tweet_id":"<tweet-id>","limit":1}'
python3 .agents/skills/lead-sourcing/scripts/scrapingdog.py --input '{"operation":"youtube_search","search_query":"<company> launch","limit":10}'
python3 .agents/skills/lead-sourcing/scripts/scrapingdog.py --input '{"operation":"youtube_video","video_id":"<video-id>","limit":1}'
python3 .agents/skills/lead-sourcing/scripts/scrapingdog.py --input '{"operation":"youtube_transcript","video_id":"<video-id>","limit":1}'
python3 .agents/skills/lead-sourcing/scripts/scrapingdog.py --input '{"operation":"google_ads_transparency","advertiser_id":"<advertiser-id>","limit":10}'
python3 .agents/skills/lead-sourcing/scripts/scrapingdog.py --input '{"operation":"google_patents","query":"<company>","limit":10}'
python3 .agents/skills/lead-sourcing/scripts/scrapingdog.py --input '{"operation":"google_patent_details","patent_id":"<patent-id>","limit":1}'
python3 .agents/skills/lead-sourcing/scripts/scrapingdog.py --input '{"operation":"tiktok_profile","username":"<username>","limit":1}'
python3 .agents/skills/lead-sourcing/scripts/scrapingdog.py --input '{"operation":"tiktok_post","url":"https://www.tiktok.com/@<user>/video/<id>","limit":1}'
python3 .agents/skills/lead-sourcing/scripts/scrapingdog.py --input '{"operation":"tiktok_ads","query":"<company>","limit":10}'
```

The accepted operation aliases are: `linkedin_profile` and
`linkedin_person_profile` → `linkedin_person`; `linkedin_job_details` and
`linkedin_job_overview` → `linkedin_job`; `google_maps_search` and
`google_maps_lookup` → `google_maps`; `google_place`, `google_places`, and
`google_maps_places` → `google_maps_place`; and `youtube_transcripts` →
`youtube_transcript`. The output `operation` echoes the normalized request key;
use canonical names in new receipts.

`instagram_*` operations are not in the adapter's operation set and are
rejected. The dedicated Instagram contract is incomplete, so do not call an
Instagram endpoint directly or claim Instagram support. If no supported
operation or live Deepline capability exists for a route, record
`route_not_connected` as unresolved. Use search-index results to discover URLs
only; scrape the exact source page before using its text as evidence. A public
profile can support identity/current role only when it names the person, role,
and company; it does not prove a buying signal by itself. A CEO is not an
automatic contact fallback.

Official references: [Documentation overview](https://www.scrapingdog.com/documentation/),
[Google Search](https://www.scrapingdog.com/documentation/google-search-api/),
[Universal Search](https://www.scrapingdog.com/documentation/universal-search-api/),
[web scraping options](https://www.scrapingdog.com/documentation/request-customization/),
[LinkedIn company profile](https://www.scrapingdog.com/documentation/company-profile-scraper/),
[LinkedIn person profile](https://www.scrapingdog.com/documentation/person-profile-scraper/),
[LinkedIn post](https://www.scrapingdog.com/documentation/post-scraper/),
[LinkedIn Jobs](https://www.scrapingdog.com/documentation/scrape-jobs-search-results/),
[LinkedIn job details](https://www.scrapingdog.com/documentation/scrape-job-overview/),
[Google Jobs](https://www.scrapingdog.com/documentation/google-jobs-api/),
[Google Maps search](https://www.scrapingdog.com/documentation/google-maps-search-api/),
[Google Maps place details](https://www.scrapingdog.com/documentation/google-maps-places-api/),
[Google Local](https://www.scrapingdog.com/documentation/google-local-api/),
[Google AI Mode](https://www.scrapingdog.com/documentation/google-ai-mode-api/),
[Google News](https://www.scrapingdog.com/documentation/google-news-search-api/),
[X profile](https://www.scrapingdog.com/documentation/x-profile-scraper-api/),
[X post](https://www.scrapingdog.com/documentation/x-post-scraper-api/),
[YouTube Search](https://www.scrapingdog.com/documentation/youtube-search-api/),
[YouTube Video](https://www.scrapingdog.com/documentation/youtube-video-api/),
[YouTube Transcripts](https://www.scrapingdog.com/documentation/youtube-transcripts-api/),
[Google Ads Transparency](https://www.scrapingdog.com/documentation/google-ads-transparency-api/),
[Google Patents](https://www.scrapingdog.com/documentation/google-patents-api/),
[Google Patent Details](https://www.scrapingdog.com/documentation/google-patent-details-api/),
[TikTok profile](https://www.scrapingdog.com/documentation/tiktok-profile-api/),
[TikTok post](https://www.scrapingdog.com/documentation/tiktok-post-scraper-api/), and
[TikTok Ads](https://www.scrapingdog.com/documentation/tiktok-ads-scraper-api/).

### Normalized output and conservative parsing

Every successful row contains these normalized keys, which may be `null`:
`company`, `domain`, `signal`, `evidence_url`, `evidence_date`,
`evidence_text`, `provider`, `operation`, and `provider_metadata`. The wrapper
does not promise provider fields that it does not normalize. The allowlisted
metadata keys are `rank`, `job_id`, `linkedin_id`, `profile_id`, `profileId`,
`tweet_id`, `video_id`, `patent_id`, `advertiser_id`, `ad_id`, `ad_format`,
`first_shown`, `last_shown`, `estimated_audience`, `spend`, `impressions`,
`username`, `post_id`,
`company_url`, `place_id`, `data_id`, `ludocid`, `title`, `location`, `industry`,
`company_size`, `address`, `phone`, `rating`, `reviews`, and
`gps_coordinates`, when supplied by the provider. `linkedin_person` can also
normalize `contact`, `contact_name`, `full_name`, `contact_url`,
`contact_title`, `current_title`, and `contact_email`; this remains role
verification, not contact discovery. Missing fields are not inferred.

For YouTube search, recognized `channel_results`, `video_results`,
`shorts_results`, and `movie_results` arrays are combined before `limit` is
applied. A recognized response may expose `continuation_cursor` from
`next_page_token`, `nextPageToken`, or `next_token` at the top level or inside
`pagination`, `scrapingdog_pagination`, `data`, `result`, or `response`. Use a
cursor only for a new, explicitly budgeted paid call after inspecting current
rows; this is expansion, not retry.

### ScrapingDog wrapper status contract

The wrapper emits the same status set as the Deepline adapter: `ok`,
`no_results`, `partial`, `rate_limited`, `auth_failed`, `quota_exceeded`,
`timeout`, `schema_error`, `provider_error`, and `config_error`. `ok` means
recognized normalized rows were returned; `no_results` means a valid response had none;
`partial` means the provider marked the response partial. HTTP 429 maps to
`rate_limited`, and 401/403 maps to `auth_failed`. Explicit quota or credit text
maps to `quota_exceeded`. An unexplained HTTP 402 stays `provider_error` because
the public ScrapingDog error documentation does not list it. Invalid input and
malformed or unknown successful response
shapes are `schema_error`; missing credentials are `config_error`; HTTP 202 is
an unresolved `provider_error` because this adapter does not poll queued work.
Other transport or HTTP failures, including HTTP 410, are also
`provider_error`. Only `ok` and `partial` may supply candidates; `no_results` is
not proof of absence, and no non-`ok` status is final evidence.

The adapter redacts API keys and other secret-like values from emitted JSON and
does not return a full raw provider response. Never put a key in JSON, shell
history, or an artifact. Each paid call is a bounded pilot or explicitly
budgeted expansion; do not automatically retry a timeout, 202, provider error,
or uncertain paid outcome. Keep Deepline and ScrapingDog credit caps separate.
Treat the estimates above as planning values, record live estimates and
provider statuses in the route receipt, and set unknown usage to `null` rather
than `0`.

## Credentials and artifacts

Use the environment or connected credential store. Record only availability,
never a value:

- `DEEPLINE_API_KEY` and optional `DEEPLINE_HOST_URL`.
- `DEEPLINE_BIN` (optional path to the Deepline CLI binary).
- `SCRAPINGDOG_API_KEY`.

For each run, write exactly `reports/<run-id>/report.md`,
`reports/<run-id>/results.json`, and `reports/<run-id>/leads.xlsx`. Include the
request, hypotheses, route commands and filters, pilot observations, statuses,
costs, accepted evidence, contact selection, and rejected or unresolved rows
with stable reasons. Keep provider receipts separate from output state. Never
infer evidence from memory or present an unverified company or contact as final.
