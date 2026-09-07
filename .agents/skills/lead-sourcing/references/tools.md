# Tool selection

This is a decision index, not a fixed waterfall or exhaustive allowlist.
Choose by the missing evidence, available identifiers, geography, and budget.
Reuse verified evidence unless stale or untrusted; do not reread every linked
reference or repeat completed research.

## Read only what this step needs

| When | Reference |
|---|---|
| Before the first provider call | [Shared adapter I/O](adapter-io.md): credentials, saved responses, uncertain outcomes. Read once. |
| Deepline discovery or execution | [Deepline adapter](deepline-adapter.md): discovery first, core contract before execution, email sections only at validation. |
| ScrapingDog execution | [ScrapingDog adapter](scrapingdog-adapter.md): common bounds/status handling plus the chosen operation row. |
| A route needs more options | The relevant section of [provider capabilities](provider-capabilities.md), not its entire catalog. |

The workflow's qualification, spending, receipt and stopping rules remain
mandatory. The detailed references define exact contracts; this index does
not replace them. Locate named headings with `rg -n` and read bounded sections
when a reference is long. Keep already loaded rules in context instead of
reloading them for every call.

## Choose by evidence gap

These are discovery seeds, not executable Deepline IDs. Search the live
catalog, then describe the selected tool immediately before execution.
Connection state, schema, native result limits, and conservative cost bounds
must be checked live. Catalog categories are not permissions: useful reads
can be labeled `admin`, and research jobs can have side effects or unknown costs.

| Missing fact or route | Provider/tool choices | Capability section |
|---|---|---|
| Niche companies missed by industry filters | DiscoLike website discovery, Exa company search, Crustdata/Prospeo filters, Forager, Aviato lookalikes | [Companies](provider-capabilities.md#company-discovery-and-identity) |
| Local operators, branches, franchises | Openmart businesses vs brands; ScrapingDog Maps/Local; Serper/OpenWebNinja Maps | [Companies](provider-capabilities.md#company-discovery-and-identity) |
| Headcount or company LinkedIn page | HarvestAPI company lookup; Crustdata identify/enrich; Prospeo; Limadata domain-to-LinkedIn; ScrapingDog company profile | [Identity](provider-capabilities.md#company-discovery-and-identity) |
| Ownership or legal identity | GovFiles, OpenSOSData, SEC EDGAR, official registries through public search/extraction | [Registries](provider-capabilities.md#registries-vertical-sources-and-source-retrieval) |
| Exact products, materials, services, certification | BuiltWith product search, DataForSEO homepage terms, PredictLeads products, exact catalog/configurator pages | [Products](provider-capabilities.md#products-technology-and-operations) |
| Hiring or operational projects | TheirStack descriptions, Crustdata jobs, PredictLeads occupations, HarvestAPI jobs, ScrapingDog jobs | [Hiring](provider-capabilities.md#hiring-events-and-capital) |
| Funding, expansion, partnerships, acquisitions | PredictLeads events/connections, SEC filings, ScrapingDog News, official announcements | [Events](provider-capabilities.md#hiring-events-and-capital) |
| Technology adoption/removal or vendor customers | Bloomberry changes, BuiltWith lists/history, PredictLeads detections, TheirStack technographics | [Technology](provider-capabilities.md#products-technology-and-operations) |
| Ads and channel investment | Adyntel keyword/domain ads, HarvestAPI LinkedIn ads, ScrapingDog Google/TikTok ads | [Advertising](provider-capabilities.md#advertising-public-statements-and-reviews) |
| Public pain, launches, supplier requests | HarvestAPI posts/comments, ScrapeCreators Reddit/Instagram, TwitterAPI, Hacker News, Bluesky | [Statements](provider-capabilities.md#advertising-public-statements-and-reviews) |
| Local changes or customer/employer pain | OpenWebNinja business posts/reviews and Glassdoor; official business pages | [Reviews](provider-capabilities.md#advertising-public-statements-and-reviews) |
| Executive interviews and long-form evidence | Podscan transcripts; ScrapingDog YouTube search/video/transcript; ScrapeCreators fallback | [Statements](provider-capabilities.md#advertising-public-statements-and-reviews) |
| Research, manufacturing, specialized datasets | ScrapingDog patents; DataForSEO dataset/event/app search; public procurement and vertical registries | [Vertical sources](provider-capabilities.md#registries-vertical-sources-and-source-retrieval) |
| Inaccessible or incomplete source | ScrapingDog rendered scrape, Firecrawl map then exact scrape, Exa contents, approved public API via Deepline generic HTTP | [Retrieval](provider-capabilities.md#registries-vertical-sources-and-source-retrieval) |
| Requested buyer or current role | HarvestAPI leads, Crustdata people, Forager roles, Datagma titles, Leadmagic roles, Aviato founders, Exa people | [Buyers](provider-capabilities.md#buyers-and-contact-data) |
| Work email | Hunter domain/name lookup, Datagma, ContactOut work-email reveal, other live-catalog finders | [Contacts](provider-capabilities.md#buyers-and-contact-data) |

## Decide whether to continue

Choose the next bounded test that can resolve the missing gate. Change the
source family or evidence type when useful, not only the provider name.
While below target, add promising affordable alternatives before claiming
exhaustion. Unused budget does not justify repeating unproductive calls.

Keep these distinctions when comparing routes: a branch is not a company;
linked profiles and revenue are not total headcount; discovery/update dates
are not event dates; installed software, ads, patents and reviews are not
confirmed purchasing projects. A registry agent is not automatically an owner,
and a social author is not automatically a current employee. Verify the actual
source and requested criteria; missing evidence is unresolved, not failed fit.

## Access and support

Deepline tools are schema-checked or catalog-only hints in the capability
reference, not guaranteed live coverage. ScrapingDog has 25 local operations;
[vendor-only endpoints](provider-capabilities.md#documented-only-not-local-operations)
are not callable through its adapter. Use a supported alternative or record
the specific gap. A disconnected or unknown-price route does not block all
other research. Never bypass access controls, introduce new authenticated
actions, deploy monitors, start outreach, or relax the ICP to fill a shortfall.

Contact identity and current role must pass before contact-data lookup.
Finder confidence never replaces the existing ZeroBounce gate or its single
conditional BounceBan fallback. Preserve all budget caps and uncertain-call
handling in the workflow and adapter references.
