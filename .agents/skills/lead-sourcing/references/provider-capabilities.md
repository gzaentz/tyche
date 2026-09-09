# Researched provider capabilities

## Scope and use

Researched on 2026-09-07 against the account-visible Deepline CLI catalog,
selected live `describe` contracts, the local adapters, and official ScrapingDog
documentation. The Deepline snapshot contained 3,168 tools across 101 provider
namespaces. This is metadata research, not a paid coverage benchmark: no lead
search, enrichment, validation, or scraping endpoint was executed for this audit.
Live contracts were checked for 82 tools across 40 providers: 79 connected and
three disconnected. The remaining inventory was reviewed at catalog/family
level, not exhaustively schema-validated.

This reference expands the [evidence-gap map](tools.md#choose-by-evidence-gap).
Read only the capability section selected by that map, plus its scope/access
caveats here. This is a lookup reference, not mandatory startup reading.
It is deliberately selective; the full vendor catalog includes CRM writes,
sequencing, account administration, async status reads, and monitor types that
are not independent lead sources. Do not mistake their count for sourcing breadth.

In the tables below, **D** means the exact Deepline tool's schema, access state,
and price metadata were inspected. Unless marked otherwise, those tools were
callable and connected in the audited workspace, not necessarily yours.
**C** means catalog-listed only; inputs, connection, price, and response shape
still require description. Neither status means execution-tested or guaranteed
to normalize through the local wrapper. Inspect saved raw responses and do not
promote unknown envelopes or missing fields into evidence.

IDs below are dated search hints. Rediscover and describe before execution;
use the returned tool ID through `scripts/deepline.py`. Input hints highlight
useful fields, not a complete payload schema; `anyOf` constraints, exact enums,
native page sizes, options, and cost bounds come from the live description.
Prices are intentionally not copied into routing advice. A headline `Free`
may mean BYOK pass-through; provider quotas/charges still apply. Usage-based
pricing or a base-page price is not a conservative bound for an entire job.

## Company discovery and identity

| Capability and observed tool | Input hints | Useful evidence and boundary |
|---|---|---|
| D DiscoLike `discolike_discover` | `domain`, `icp_text`/`icp_prompt`, `phrase_match`, geography, `max_records` | Website-first niche/adjacent accounts. Semantic similarity and inferred size require verification; bound usage-based pricing. |
| D DiscoLike `discolike_bizdata` | `domain` | Firmographics, public contacts, keywords for a known site; null size remains unresolved. |
| D Exa `exa_company_search` | `query`, `numResults`, content options | Concept-driven company discovery. Do not combine entity-category search with source-scoped domain/text filters; use general web search for that. |
| D Crustdata `crustdata_v3_company_search` | `filters`, `fields`, `limit`, `sorts`, `cursor` | Indexed industry, country, size, funding, growth, investor, competitor and function-size discovery. Use catalog autocomplete for exact field values. |
| D Prospeo `prospeo_search_company` | `company_industry`, `company_keywords`, `company_headcount_range`, location, technology, `page` | Independent structured account universe; confirm actual ICP evidence and native page size. |
| D Forager `forager_organization_search` | `description`, `keywords`, `locations`, `employees_start/end`, job/funding filters, `page` | Alternate organization discovery with footprint and job-event criteria; page price is not a per-row price. |
| D Aviato `aviato_generate_map` | Company `id`, or `name` plus `website` | Similar-company market map. It starts generation; preserve the returned job and discover recovery before further work. |
| D Openmart `openmart_search_businesses` | `query`, `location`, `tags`, `ownership_type`, `open_date_after`, `limit` | Physical-store discovery, including opening and quality filters. One location is not necessarily one company. |
| D Openmart `openmart_search_brands` | `search_param`, `pagination` | Brand/company-level rows when store-level duplicates dominate. Still resolve legal operator and franchise boundaries. |
| D HarvestAPI `harvestapi_search_companies` | `search`, `companySize`, `location`/`geoId`, `industryId`, `page` | LinkedIn company discovery, not verified account fit. Name search can match another company. |
| D HarvestAPI `harvestapi_get_company` | One of `url`, `universalName`, `search` | `website`, identity, `employeeCountRange`, and `employeeCount` are declared outputs. Match the domain and distinguish company-size range from associated profile count. |
| D Limadata `limadata_find_company_linkedin` | `domain` | Resolve a company-page candidate before exact HarvestAPI/ScrapingDog lookup; verify brand/legal-entity match. |
| D Crustdata `crustdata_v3_company_identify` | Domain, company LinkedIn URL, ID, or name | Identity resolution before enrichment. Name-only matches need an independent identifier. |
| D Crustdata `crustdata_v3_company_enrich` | Described company identifiers and requested fields | Cached detailed firmographics after identity resolution. Provider recency and missing bounds matter. |
| D Prospeo `prospeo_enrich_company` | `company_website`, `company_name`, `company_linkedin_url` or supported aliases | Independent headcount/industry/technology evidence; preserve range/source semantics. |
| D Enigma `enigma_brand_revenue_search` | `name`, optional `state`, `period` | Card-spend-based private-business revenue context. Not total revenue or an employee-count substitute. |
| C Other independent company routes | Search `AI Ark company search`, `FullEnrich company search`, `PDL company search`, `Firmable company lookup`, `Datagma company SIREN` | Useful when coverage/geography differs; do not blindly execute every provider on the same domain. |

## Products, technology, and operations

| Capability and observed tool | Input hints | Useful evidence and boundary |
|---|---|---|
| D BuiltWith `builtwith_product_search` | `query`, `limit`, `page` | Discover shops selling a specific product, beyond broad industry labels. Inspect actual product and seller pages. |
| D DataForSEO `dataforseo_domain_analytics_technologies_domains_by_html_terms_live` | `search_terms`, `mode`, `filters`, `limit` | Find niche homepage terms such as materials, certification language, equipment, or service names. Homepage terms are candidates, not proof of full catalog capability. |
| D PredictLeads `predictleads_discover_products` | `sources`, first-seen range, `limit`, `page` | Product/menu/pricing-page discovery across companies. First-seen time is not necessarily launch time. |
| D BuiltWith `builtwith_domain_lookup` | `domain` or `domains`, detection ranges, `live_only` | Current/historical technologies and domain context. An installed tag does not prove a purchasing project. |
| D BuiltWith `builtwith_lists` | Exact `tech`, countries, `since`, `offset` | Reverse technology-to-company discovery. Native page volume must fit the pilot bounds; use technology-name discovery first. |
| D Bloomberry `bloomberry_get_tech_stack_changes` | Vendor/category, `new_only`/`churn_only`, date range, `limit` | Potential adoption/removal triggers. Verify actual vendor relationship and event timing; do not present a detection as confirmed churn. |
| D PredictLeads `predictleads_company_website_evolution` | `company_id_or_domain`, first-seen range, `limit` | Website changes worth inspecting for new services, regions, or capabilities. Read the changed source before writing a signal. |
| C Additional tech routes | `predictleads_discover_technology_detections`, `theirstack_technographics`, `bloomberry_get_current_customers`, `builtwith_relationships` | Technology-to-company lists, stack evidence, vendor-customer graphs, related domains. Shared infrastructure does not establish common ownership. |

## Hiring, events, and capital

| Capability and observed tool | Input hints | Useful evidence and boundary |
|---|---|---|
| D TheirStack `theirstack_job_search` | `job_description_contains_or`, title/technology/company filters, `posted_at_gte`, `limit` | Project language, implementation skills, reporting lines, and hiring. Distinguish posted from discovered dates and staffing agencies from the actual employer. |
| D Crustdata `crustdata_v3_job_search` | `filters`, `fields`, `limit`, `cursor` | Alternate indexed hiring universe. Aggregations/counts do not replace individual job evidence. |
| D HarvestAPI `harvestapi_search_jobs` | `search`, `companyId`, `location`, `postedLimit`, `page` | LinkedIn hiring discovery; retrieve exact job detail when list data is insufficient. |
| D PredictLeads `predictleads_discover_job_openings` | `onet_codes`, `location`, `limit`, `page` | Occupation-based discovery when title keywords miss industry-specific language. |
| D PredictLeads `predictleads_discover_news_events` | `categories`, `company_location`, `limit`, `page` | Expansion, partnerships, acquisitions and other event categories; inspect source event and all company relationships. |
| D PredictLeads `predictleads_discover_financing_events` | `financing_types_normalized`, `company_location`, `limit` | Funding-event-first discovery. Deal identity, event date, and recipient must be verified. |
| D PredictLeads `predictleads_company_connections` | `company_id_or_domain`, `categories`, first-seen range | Relationship evidence for a known company; a customer, partner, investor, and supplier are different roles. |
| D PredictLeads `predictleads_discover_portfolio_company_connections` | First/last-seen ranges, `limit`, `page` | VC/accelerator portfolio discovery. Portfolio appearance is not a newly closed funding round. |
| D PredictLeads `predictleads_startup_platform_posts` | `post_types`, `published_at_from/until`, `limit` | Launch/hiring posts on startup platforms. Verify current product and operating entity. |
| D SEC EDGAR `sec_edgar_list_filings` | `ticker` or `cik`, `forms`, `filed_from/to`, `limit` | Filing discovery for financial, ownership, leadership, and material-event research. Inspect filing index/document, not just submission metadata. |
| D DataForSEO `dataforseo_serp_google_events_live_advanced` | `keyword`, location, `date_range`, bounded depth | Public events/exhibitions as a discovery route; English-only per current descriptor. Event participation is not purchase intent. |
| C Other event routes | `aviato_get_company_funding_rounds`, `leadmagic_jobs_finder`, `openwebninja_jsearch_search`, `forager_job_search` | Alternative capital/job coverage after a materially relevant gap. |

## Advertising, public statements, and reviews

| Capability and observed tool | Input hints | Useful evidence and boundary |
|---|---|---|
| D Adyntel `adyntel_facebook_ad_search` | `keyword`, optional `country_code` | Keyword-first Meta advertiser discovery when domains are unknown. Verify the advertiser before company enrichment. |
| D Adyntel `adyntel_domain_keywords` | `company_domain` | Paid/organic keyword posture and advertising proxies. Estimates do not prove a spending amount or intent. |
| D HarvestAPI `harvestapi_search_ads` | `keyword`/`accountOwner`, countries/date options, cursor | LinkedIn ad-library discovery and activity dates. Do not infer conversion performance or available budget. |
| C Other ad routes | `adyntel_facebook`, `adyntel_google`, `adyntel_linkedin`, `adyntel_tiktok_search`, `leadmagic_b2b_ads_search` | Known-account and additional-network creative research, not a compulsory network sweep. |
| D HarvestAPI `harvestapi_search_posts` | `search`, `company`/`profile`, `postedLimit`, `sortBy`, cursor | Public launches, operational changes, explicit supplier requests, and pain language. Verify original author, company, and date. |
| C HarvestAPI follow-up reads | `harvestapi_get_company_posts`, `harvestapi_get_post`, `harvestapi_get_post_comments`, `harvestapi_get_profile_posts` | Inspect an identified source or conversation rather than repeating broad post search. Reactions alone are weak evidence. |
| D ScrapeCreators `scrapecreators_reddit_search` | `query`, sort/time fields, `after` | Community problem and recommendation discovery. Pseudonymous users are not company contacts. |
| D ScrapeCreators `scrapecreators_reddit_post_comments` | Exact post `url`, optional cursor | Recover the discussion and objections; quoted/reposted claims need attribution. |
| D ScrapeCreators `scrapecreators_instagram_profile` | `handle` | A Deepline alternative despite no local ScrapingDog Instagram operation. Profile identity does not establish a dated business event. |
| D ScrapeCreators `scrapecreators_instagram_user_posts` | `handle`, optional `next_max_id` | Inspect posts from a resolved business profile for launches, locations or service changes. Verify content, date and the account's company relationship. |
| D ScrapeCreators `scrapecreators_instagram_reels_search` | `query`, `date_posted`, `page` | Keyword-first discovery of public Reels and captions. Resolve the author/business and inspect the original post before treating it as company evidence. |
| D TwitterAPI `twitterapi_advanced_search` | `query`, `queryType`, `cursor` | Niche public statements with search operators; verify employer and timestamp. |
| D Hacker News `hackernews_search` | `query`, `sort`, `tags`, `numericFilters`, `hitsPerPage` | Developer/product discussions. Link the actual story/comment and never infer private company identity. |
| D Bluesky `bluesky_search_posts` | `q`, `since`/`until`, `author`, `limit`, cursor | Another public conversation source; public mention is not automatically account intent. |
| D OpenWebNinja `openwebninja_localbusiness_business_posts` | `business_id`, optional cursor/language | Owner updates on a Google Maps listing can reveal openings, services, and events. Resolve listing ID first. |
| D OpenWebNinja `openwebninja_glassdoor_company_reviews` | `company_id`, query/sort/page filters | Employer pain or operating-change hypotheses, not verified claims. Resolve Glassdoor employer identity first. |
| D Podscan `podscan_episodes_search` | `query`, language, `per_page`, `include_transcript` | Transcript-based executive statements, projects, partners, and industry language. Verify guest/speaker/current role and publication date. |

## Buyers and contact data

Only after the company passes. Discover the requested role before email lookup;
limit phone/personal-contact fields to the user's requested scope. Do not infer
private traits from social activity or turn anonymous community authors into leads.

| Capability and observed tool | Input hints | Useful evidence and boundary |
|---|---|---|
| D HarvestAPI `harvestapi_search_leads` | `currentCompanies`, `currentJobTitles`, seniority/function, `recentlyChangedJobs`, `page` | Current employees or role-change candidates. Profile results are not total headcount; verify employment from exact profiles. |
| D HarvestAPI `harvestapi_search_services` | `search`, location, `page` | Independent specialists and service providers missing from company databases. Resolve their actual business entity. |
| D Crustdata `crustdata_v3_person_search` | `filters`, `fields`, `limit`, `cursor` | Indexed title/employer/skills search. Use exact-field autocomplete when needed. |
| D Forager `forager_person_role_search` | `organization_domains`, `role_title`, `role_is_current`, role dates, `page` | Role history and current-role resolution across a different person graph. Match organization identity. |
| D Datagma `datagma_find_people` | `domain`/`currentCompanies`, `currentJobTitle` | Up to ten matching people in a known account; respect requested title families. |
| D Leadmagic `leadmagic_role_finder` | `job_title`, company domain/name/profile | Specific role-holder lookup when broad people search is noisy. |
| D Aviato `aviato_get_company_founders` | Company identifier, required `page` and `perPage` | Founder discovery when founders are requested. Historical founder status does not prove current management or ownership. |
| D Exa `exa_people_search` | `query`, `company_name`, `numResults` | Public team/profile evidence for thinly indexed companies. Search results require current-role verification. |
| D Wiza `wiza_search_prospects` | `filters`, `size` | Masked prospect discovery before selected reveal. A masked result is not a usable email. |
| D Datagma `datagma_job_change_detection` | Person name, previous company/title context | Freshness check for a known person; establish the new role before using contact details. |
| D Hunter `hunter_domain_search` | `domain` or `company`, job-title/department/seniority filters, `limit` | Named addresses with source/confidence information. Generic inboxes do not satisfy a requested individual contact. |
| D Hunter `hunter_email_finder` | `domain`/`company`, `first_name`/`last_name` or `full_name` | Exact-person work-email route; finder confidence is not final validation. |
| D Datagma `datagma_find_email` | Person name plus domain/company, or supported LinkedIn identifier | Independent work-email lookup; retain provider receipt and validate through the existing gate. |
| D ContactOut `contactout_linkedin_contact_info` | `profile`, `email_type`, `include_phone` | Selected LinkedIn profile reveal. Request work email only and no phone unless that extra data is in scope. |
| C Additional contact coverage | `fullenrich_people_search`, `bettercontact_enrich`, `findymail_find_from_name`, `forager_person_contacts_lookup_work_emails`, `dropleads_search_people` | Different person graphs/waterfalls. Inspect async recovery, per-page billing, and masked/reveal behavior before use. |
| Live validation discovery | Search `ZeroBounce single email validation`, then use `BounceBan verify single email` for any non-hard-rejection ZeroBounce issue | Follow the [Deepline email gate](deepline-adapter.md#email-validation); other catalog validators do not replace or extend it. |

## Registries, vertical sources, and source retrieval

| Capability and observed tool | Input hints | Useful evidence and boundary |
|---|---|---|
| D GovFiles `govfiles_search_companies_v2` | `q`, jurisdictions, status, `limit` | US legal-company records, filings, identifiers and parties. Confirm jurisdiction availability and exact record; it is not a global register. |
| D OpenSOSData `opensosdata_business_lookup` | `entity_name`, `state` | US officer/registration evidence where covered. Some jurisdictions return entities but no officers; none establishes total staff. |
| D Enformion `enformion_business_search` | `name`, optional `city_state`, `results_per_page` | Another US business/officer route for thin B2B coverage. Separately entitled despite connected metadata; do not automatically follow it into personal-address/phone lookup. |
| D DataForSEO `dataforseo_serp_google_dataset_search_live_advanced` | `keyword`, format/topic/freshness fields, bounded depth | Discover exact public datasets for specialized industries. Read license, geographic scope, publisher, and actual artifact before using rows. |
| D DataForSEO `dataforseo_app_data_apple_app_listings_search_live` | App `title`, `description`, `categories`, `limit` | Mobile-software publisher discovery. Resolve app publisher to the canonical company; ratings/download proxies are not buying intent. |
| D Serper `serper_google_search` | `query`, `gl`, `hl`, `location`, `tbs`, `num` | Public web source discovery across industries and local languages. Read exact sources, not snippets alone. |
| D Firecrawl `firecrawl_map` | `url`, `search`, `limit`, sitemap options | Locate product, team, legal, job, or news URLs without blindly crawling all content. Its price metadata mixes a per-call headline with per-discovered-page settlement; bound requested pages. |
| D Firecrawl `firecrawl_scrape` | `url`, formats, `waitFor`, bounded read-only actions | Exact-page rendered content and supported document parsing. Option/PDF-page costs matter; do not disable TLS verification or perform form submissions. |
| D Exa `exa_contents` | `urls`/`ids`, text, `livecrawl`, cache-age options | Alternative extraction of known pages. Cached/summary text is not necessarily current or a direct quote. |
| D Firecrawl `firecrawl_extract` | `urls`, `schema`, `prompt`, `showSources` | Structured extraction from known sources; extracted claims still need source passages and bounded usage. |
| D Parallel `parallel_run_task` | `input`, processor, `task_spec`, `source_policy` | Hard multi-source research when simpler routes leave a specific gap. Usage-based/async: confirm cost and recovery before dispatch; avoid duplicate jobs. |
| D Apify `apify_run_actor_sync` | Exact `actorId`, actor-specific `input`, timeout/options | Source-specific extraction only after actor input, output, effects, and total cost are vetted. Catalog presence does not make arbitrary actors approved. |
| D Deepline `generic_http_request` | Public `url`, method, query or read-only search body | Public registries/APIs without a native integration. Verify destination, schema, authentication and cost separately; free transport does not make the target free. Never send secrets, private datasets, mutations, or arbitrary authenticated requests through this route. |

For example, EU procurement has an official read-only
[TED search API](https://docs.ted.europa.eu/api/latest/search.html),
`POST https://api.ted.europa.eu/v3/notices/search`, with query/fields/limit and
published notices. This is a generic-route candidate, not a tested native
integration. An open procurement notice, an award, and a supplier listing are
different signals. The US
[SAM.gov opportunities API](https://open.gsa.gov/api/get-opportunities-public-api/)
requires an API key; do not paste that key into a wrapper payload. Use an
approved secret-binding integration or public-page evidence instead.

## Access-limited and excluded routes

- D `zoominfo_search_intent` and `zoominfo_search_scoops` were callable but
  **not connected**. Their described inputs include `data`, page and sort;
  discover topic/filter contracts after BYOK access exists. Do not equate an
  advertising proxy with these intent products or promise that either proves
  a purchase. Deepline's free BYOK transport does not waive provider charges.
- D `sumble_search_priority_signals` was **not connected** and needs the
  workspace's Sumble key; its input is `filter`. Other Sumble organization,
  technology, team, job and signal tools remain catalog-only choices here.
- CRM/warehouse/call/support reads require relevant user-authorized data and
  scope. They are not public prospecting fallbacks. CRM writes, sends,
  sequencing, audience uploads, contact/list creation, and monitor deployment
  are outside this skill even when connected.
- Monitor types such as Deepline's company-job or company-social streams are
  not callable one-shot searches. Status/result tools recover an existing job;
  they are not new discovery sources. Do not create subscriptions or new jobs
  just to replace a missing read.
- Personality scoring, sensitive personal-data expansion, bulk exports, broad
  crawls, and account administration are not automatic shortfall routes.

## ScrapingDog coverage

The local adapter exposes **25 canonical operations**, not every vendor API.
Use the exact endpoint/input/price tables in the [ScrapingDog adapter](scrapingdog-adapter.md#scrapingdog-wrapper)
for execution. All rows here are documentation/code checks, not live coverage
tests; plan entitlements and response shapes still need a bounded pilot.

| Evidence family | Implemented local operations |
|---|---|
| Web/company/local discovery | `google_search`, `universal_search`, `scrape`, `google_maps`, `google_maps_place`, `google_local` |
| Company/person/post identity | `linkedin_company`, `linkedin_person`, `linkedin_post` |
| Hiring | `linkedin_jobs`, `linkedin_job`, `google_jobs` |
| News and answer-assisted discovery | `google_news`, `google_ai_mode` |
| Advertising | `google_ads_transparency`, `tiktok_ads` |
| Innovation | `google_patents`, `google_patent_details` |
| Social and long-form sources | `x_profile`, `x_post`, `tiktok_profile`, `tiktok_post`, `youtube_search`, `youtube_video`, `youtube_transcript` |

### Documented only, not local operations

These are vendor capabilities, **not instructions to call endpoints directly**.
Use a supported Deepline alternative where available. Otherwise record a local
adapter gap; adding support needs a separate bounded implementation and tests.
All paths below are under `https://api.scrapingdog.com`; authentication is
omitted from input hints and must never be put in the skill's JSON payload.

| Official source and path | Input hints | Sourcing use and present alternative |
|---|---|---|
| [Maps posts](https://www.scrapingdog.com/documentation/google-maps-posts-api/) `/google_maps/posts` | `data_id`, optional cursor | Owner updates/openings; use OpenWebNinja business posts through Deepline today. |
| [Maps reviews](https://www.scrapingdog.com/documentation/google-maps-reviews-api/) `/google_maps/reviews` | `data_id`, sort/page fields | Operational/customer pain hypotheses; discover OpenWebNinja business reviews. |
| [Trends](https://www.scrapingdog.com/documentation/google-trends-api/) `/google_trends` | Query/comparison and geography/time controls | Aggregate demand and keyword hypotheses, not account intent; discover DataForSEO trends tools. |
| [News v2](https://www.scrapingdog.com/documentation/google-news-api/) `/google_news/v2` | Query or supported topic/publication/story tokens | Alternative news surface; current adapter's `google_news` uses `/google_news`, not v2. |
| [Shopping](https://www.scrapingdog.com/documentation/google-shopping-api/) `/google_shopping` | `query` or complete Google URL | Seller/product discovery; use BuiltWith product search or supported web discovery. |
| [Indeed](https://www.scrapingdog.com/documentation/indeed-scraper-api/) `/indeed` | Full Indeed search URL | Additional hiring universe; use supported jobs tools or exact permitted page extraction. |
| [Yelp](https://www.scrapingdog.com/documentation/yelp-scraper-api/) `/yelp/search` | `find_loc`, optional keyword/category | Local account discovery; use Openmart or supported Maps/Local routes. |
| [YouTube comments](https://www.scrapingdog.com/documentation/youtube-comment-api/) `/youtube/comments` | Video ID `v` | Audience questions/objections, not verified company contacts. Existing transcript route is different evidence, not comment support. |
| [YouTube channel](https://www.scrapingdog.com/documentation/youtube-channel-api/) `/youtube/channel` | `channel_id` | Channel identity/activity; existing video/search routes provide narrower evidence. |
| [Facebook Ads](https://www.scrapingdog.com/documentation/facebook-ads-scraper-api/) `/facebook` | `query`, `page_id`, or Ads Library URL | Dedicated page exists but is absent from the reviewed index; not runtime-tested. Use Adyntel Meta discovery instead. |

### Broader vendor families

The [official documentation index](https://www.scrapingdog.com/documentation/)
also lists the families below. This is family-level classification, not a claim
that every leaf schema was validated or that these APIs work in our adapter.

| Family | Useful role or reason not to prioritize |
|---|---|
| Maps photos; Trends autocomplete/trending; Google AI Overview | Visual/local corroboration, vocabulary and demand discovery, source-finding. AI summaries are not primary evidence. |
| Scholar search/profiles/author/citations/cite | Researcher, institution and emerging-technology discovery; link to papers and resolve commercial entities. |
| Google images/videos/shorts/autocomplete/Lens; immersive products | Product/source identification and query expansion. Visual similarity is not seller identity. |
| Bing, Bing Shopping, DuckDuckGo, Brave, Baidu | Different search indexes/locales when initial discovery is repetitive. Retrieve actual result sources. |
| Amazon product/search/reviews/autocomplete/offers/bestsellers | Marketplace sellers, assortment and distribution. The [reviews page](https://www.scrapingdog.com/documentation/amazon-reviews-api/) reported temporary unavailability; do not assume the entire family is executable. |
| Apple App Store/product/reviews | App publishers, products and customer feedback. Use described Deepline App Store discovery where suitable. |
| Walmart autocomplete/product/search/reviews; eBay, Flipkart, Myntra search/product | Marketplace and regional-commerce discovery; brands, sellers and legal companies need separate resolution. |
| Zillow; Google Hotels, Flights, Finance | Property, hospitality and market context only when relevant to the ICP. Prices, listings and routes are not account-level intent. |
| POST/screenshot, rendering, headers, proxies, sessions, geotargeting | Retrieval infrastructure, not extra company universes. The wrapper only forwards documented local fields; do not silently add vendor-only flags. |
| ChatGPT scraper | Generated answers, not an independent source of company facts; low priority here. |
| Webhook, rotating proxy, account API | Delivery/access/usage utilities, not lead evidence. The local wrapper does not expose them. |

No `instagram_*` ScrapingDog operation is supported here; the reviewed official
index did not establish one. Deepline ScrapeCreators is a distinct available
provider, not an alias for missing ScrapingDog support.

## Refresh and evidence

For maintenance, use Deepline's exhaustive `tools list --json` inventory;
ranked `tools search` is not a complete inventory. Then describe selected IDs
and compare them with the local adapter and official vendor docs. Do not filter
only to `research`: useful reads also appear in `admin` and other categories.
Source retrieval and paid execution during sourcing still use the local wrappers.

Refresh descriptions, access, native limits and pricing when actually selecting
a route. Record whether a claim was catalog-listed, schema-checked, or observed
in a paid pilot. This reference does not authorize new external actions, relax
the ICP, replace the email gate, or prove that a shortfall is unavoidable.
