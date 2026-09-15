# Tool selection

Choose by the missing evidence, company identity, geography and budget. These
are useful starting points, not a fixed sequence or exhaustive allowlist.
Read the matching row; follow its detail link only when needed.

## Choose by evidence gap

### Signals and qualification facts

Funding stage is a company fact; a newly announced round is an event. PR and
social media are [source channels](#source-channels), not automatic intent.
Interpret all evidence against the actual request.

| Need | Providers and strengths | Catalog search seed / details |
|---|---|---|
| **Funding stage/history** | **Aviato:** rounds, stages, dates, investors. **Crustdata:** funding-filtered company discovery. **Leadmagic:** detailed funding profiles. | `Aviato funding rounds` · [Capital](provider-capabilities.md#hiring-events-and-capital) |
| **Recent financing** | **PredictLeads:** financing events and source links. **Aviato:** recorded rounds. Distinguish equity, debt and project financing. | `PredictLeads financing` · [Capital](provider-capabilities.md#hiring-events-and-capital) |
| **Hiring/staffing demand** | **TheirStack:** job-description search. **HarvestAPI:** LinkedIn jobs. **Crustdata:** indexed hiring. **PredictLeads:** occupation filters. **ScrapingDog:** Google/LinkedIn jobs. | `TheirStack job search` · [Hiring](provider-capabilities.md#hiring-events-and-capital) |
| **Partnerships/customer wins** | **PredictLeads:** connections and news events. **HarvestAPI posts:** company/partner announcements; confirm each company's relationship. | `PredictLeads company connections` · [Events](provider-capabilities.md#hiring-events-and-capital) |
| **Expansion/facilities/capacity** | **PredictLeads:** news-event discovery. **Openmart:** location/opening filters. Company announcements establish planned versus completed activity. | `PredictLeads news events` · [Events](provider-capabilities.md#hiring-events-and-capital) |
| **Product/service launches** | **PredictLeads:** products and news discovery. Official product pages/changelogs establish launch dates; first detection is not launch. | `PredictLeads products` · [Products](provider-capabilities.md#products-technology-and-operations) |
| **Acquisitions/ownership changes** | **PredictLeads:** news and relationships. Announcements/filings confirm buyer, target and transaction status. **SEC EDGAR:** relevant US filings. | `PredictLeads acquisitions` · [Events](provider-capabilities.md#hiring-events-and-capital) |
| **Leadership changes** | Announcements identify changes. **HarvestAPI:** current profiles. **Forager:** role history. **Datagma:** job-change checks. | `Forager role search` · [Roles](provider-capabilities.md#buyers-and-contact-data) |
| **Contracts/regulatory milestones** | Public procurement, regulator records and company announcements; **SEC EDGAR** where applicable. Check jurisdiction and award/approval status. | `contract regulatory news` · [Registries](provider-capabilities.md#registries-vertical-sources-and-source-retrieval) |
| **Technology adoption/removal** | **BuiltWith:** installed technology/history. **Bloomberry:** changes. **PredictLeads/TheirStack:** detections. Presence alone does not prove a new purchase. | `Bloomberry tech changes` · [Technology](provider-capabilities.md#products-technology-and-operations) |
| **Advertising/channel investment** | **Adyntel:** advertiser/creative discovery. **HarvestAPI:** LinkedIn ads. **ScrapingDog:** Google/TikTok ads. Activity is not verified spend. | `Adyntel ads` · [Advertising](provider-capabilities.md#advertising-public-statements-and-reviews) |

### Source channels

Use these across signals, including explicit supplier requests, operational
pain, layoffs or other request-specific activity missing from the table.

| Source | Useful providers | Details |
|---|---|---|
| **PR/news/web** | **Limadata, Serper, ScrapingDog:** discover announcements/articles. **Exa:** semantic discovery and source contents. | [Retrieval](provider-capabilities.md#registries-vertical-sources-and-source-retrieval) |
| **Social media** | **HarvestAPI:** LinkedIn posts/comments. **ScrapeCreators:** supported Reddit/Instagram reads. **TwitterAPI/Bluesky:** post search. **ScrapingDog:** supported X/TikTok reads. | [Social](provider-capabilities.md#advertising-public-statements-and-reviews) |
| **Reviews/community** | **OpenWebNinja:** local-business updates/reviews and Glassdoor. **ScrapeCreators/Hacker News:** discussions. Verify authorship and company attribution. | [Reviews](provider-capabilities.md#advertising-public-statements-and-reviews) |
| **Interviews/video** | **Podscan:** podcast transcripts. **ScrapingDog/ScrapeCreators:** supported video/transcript reads. | [Statements](provider-capabilities.md#advertising-public-statements-and-reviews) |
| **Patents/specialist datasets** | **ScrapingDog:** patents. **DataForSEO:** datasets, catalogs and app discovery. Official registries supply jurisdiction-specific evidence. | [Specialist sources](provider-capabilities.md#registries-vertical-sources-and-source-retrieval) |
| **Exact-page retrieval** | **Firecrawl:** map/scrape/extract. **Exa:** contents. **ScrapingDog:** rendered pages. **Deepline generic HTTP:** approved public APIs. **Parallel:** bounded research for difficult gaps. | [Retrieval](provider-capabilities.md#registries-vertical-sources-and-source-retrieval) |

### Company and contact checks

| Need | Providers and strengths | Details |
|---|---|---|
| **Company discovery** | **Crustdata/Prospeo/Forager:** structured filters. **DiscoLike/Exa:** niche/semantic discovery. **Aviato:** company search/lookalikes. **Deepline corpus:** bounded SQL discovery. | [Companies](provider-capabilities.md#company-discovery-and-identity) |
| **Local sites/branches** | **Openmart:** businesses versus brands. **ScrapingDog/Serper/OpenWebNinja:** Maps/local discovery. Resolve the actual operator; branches are not separate companies. | [Companies](provider-capabilities.md#company-discovery-and-identity) |
| **Identity/location/size** | **Crustdata:** identity resolution. **Limadata:** domain-to-LinkedIn. **HarvestAPI:** company/profile fields. Use Harvest LinkedIn employee ranges; distinguish contact location from HQ. Name-only matches need corroboration. | [LinkedIn contract](output-contract.md#linkedin-location-and-company-size) |
| **Business/product fit** | **DiscoLike:** website context. **BuiltWith:** product search. **DataForSEO:** homepage terms. Official product/service pages support qualification. | [Products](provider-capabilities.md#products-technology-and-operations) |
| **Legal identity** | **GovFiles/OpenSOSData:** covered US registries. **SEC EDGAR:** filings. Relevant official registries elsewhere; a registered agent is not necessarily an owner. | [Registries](provider-capabilities.md#registries-vertical-sources-and-source-retrieval) |
| **Buyers/current roles** | **HarvestAPI/Crustdata/Forager:** people/roles. **Datagma/Leadmagic:** targeted titles. **Exa:** public-profile gaps. **Aviato:** founders when relevant to requested roles. | [Buyers](provider-capabilities.md#buyers-and-contact-data) |
| **Work email/validation** | **Hunter/Datagma/ContactOut:** selected-person email. Reuse verified profiles. **ZeroBounce:** required validation; **BounceBan:** eligible fallback only. | [Email gate](deepline-adapter.md#deepline-zerobounce-email-gate) |

## Use the guide without extra work

- Search a short provider/capability phrase with `tyche_inspect(query=...)`.
  Seeds above are searches, not executable IDs. Choose the returned tool;
  `tyche_inspect(tool=...)` supplies cached inputs, access and pricing.
- Start with sources suited to the missing fact. Reuse successful receipts.
  Inspect saved raw detail when summaries omit fields; do not pay again for it.
- Preserve source identity, dates and claim strength. No results means unknown,
  not failed fit. Catalog metadata and generated summaries are not evidence.

Use existing [native tools](adapter-io.md#native-tools),
[Deepline contracts](deepline-adapter.md) and
[ScrapingDog operations](scrapingdog-adapter.md#scrapingdog-wrapper).
Schemas, prices, native limits and access are checked live, not copied here.
Categories are not permissions; disconnected, monitor-only or
[vendor-only](provider-capabilities.md#documented-only-not-local-operations)
capabilities are not executable alternatives. Other useful approved reads can
be discovered dynamically. Preserve qualification, budget, uncertain-charge,
identity-before-email and stopping rules; no outreach or new subscriptions.
