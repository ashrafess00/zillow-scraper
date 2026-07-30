# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Django REST Framework API that scrapes Zillow.com real estate data in real time. It is deployed behind RapidAPI (RapidAPI handles auth and billing; this API has no authentication of its own). Endpoints cover real-estate agents and property listings/searches.

## Commands

Everything runs through Docker Compose (Postgres + Redis + web + celery + celery-beat). The web app listens on **port 8112**.

```bash
docker-compose up --build            # Start all services
docker-compose exec web python manage.py migrate
docker-compose exec web python manage.py test            # Run tests
docker-compose exec web python manage.py test api        # Test a single app
docker-compose exec web python manage.py test api.tests.ClassName.test_method  # Single test
docker-compose exec web python manage.py canary          # Live checks against real Zillow
docker-compose exec web python manage.py canary --json   # Same, for alerting
docker-compose logs -f web           # Tail web logs
docker-compose logs -f celery        # Tail worker logs
```

Local (non-Docker) dev: `pip install -r requirements.txt`, then `python manage.py runserver`. Requires a reachable Postgres and Redis (defaults in `settings.py` point at Docker hostnames `db` / `redis`).

Docs: Swagger at `/api/docs/`, ReDoc at `/api/redoc/`, raw OpenAPI at `/api/schema/`. `zillow-openapi-schema.yaml` is the RapidAPI-facing schema — regenerate it with `make schema` after changing any serializer or `@extend_schema` block, since RapidAPI imports that file.

**The schema shape matters to RapidAPI's importer**, which builds its endpoint groups from the root-level `tags` declaration and falls back to one group per `operationId` prefix for anything it cannot place. Two settings keep that import clean and both must stay: `SPECTACULAR_SETTINGS['TAGS']` declares Properties/Agents/Utilities at root (drf-spectacular otherwise tags only the operations, not the document), and the `api.schema.clean_operation_ids` postprocessing hook strips drf-spectacular's `_retrieve`/`_list` suffixes. Without them an import yields the three real groups plus ~30 empty ones named after each endpoint. `OpenAPISchemaTests` asserts both, so a new endpoint can't quietly regress it. When overriding `POSTPROCESSING_HOOKS`, keep `drf_spectacular.hooks.postprocess_schema_enums` in the list or enum components stop generating.

Config is via `.env` (python-decouple). Key vars: `PROXIES`, `PROXIES_LIVE_DATA`, `DEBUG`, `POSTGRES_*`, `REDIS_URL`, `RATE_LIMIT_PER_MINUTE`, `RATE_LIMIT_PER_HOUR`, `REQUEST_DELAY_MIN/MAX`, `MAX_RETRIES`, `RAPIDAPI_PROXY_SECRET`, `RAPIDAPI_EXEMPT_PATHS`.

A `Makefile` wraps the common Compose/Django commands (`make up`, `make logs S=celery`, `make test ARGS=...`, `make health`); `make` alone lists them.

## Architecture

Request flow: `api/urls.py` → `api/views.py` (function-based `@api_view` views) → a scraper singleton in `scrapers/` → `api/serializers.py` → JSON response.

### Layers

- **`api/`** — HTTP layer. Views are thin: parse query params, call a scraper, serialize. `api/urls.py` wraps every endpoint in `cache_page(60*15)` (15-min Redis cache). `api/exceptions.py` is the linchpin error contract (see below).
- **`scrapers/`** — scraping logic. `agent_scraper` and `property_scraper` are module-level singletons subclassing `BaseScraper` (`scrapers/base.py`). `scrapers/utils.py` holds the parsing/URL helpers (`parse_property_card`, `build_search_url`, `extract_json_from_script`, `slugify_location`, etc.). Scrapers extract data from Zillow's embedded JSON (Next.js `__NEXT_DATA__` / Apollo state in `<script>` tags), not by CSS scraping; property search parses `listResults` out of `searchResults`. Some paths call Zillow JSON endpoints directly (`/zg-graph`, `profile-page/api/public/v1/`).
- **Property details by zpid** — `/property`, `/zestimate`, `/priceHistory`, `/taxHistory`, `/photos`, `/schools`, `/similarHomes`, `/openHouses`, `/listingAgent`, `/monthlyCost`, `/homeFacts`, `/taxAssessment`, `/nearbyAreas`, `/listingStatus` all take a `zpid` and are backed by `PropertyScraper._get_property_data(zpid)`, which fetches `homedetails/{zpid}_zpid/` **once**, extracts the `property` object from `gdpClientCache`, and caches it in Redis (`property:{zpid}`, 15 min). So the first of those endpoints for a given zpid scrapes (~10–20s cold); the rest read cache (~0.01s). When adding a field, pull it from that one cached object rather than adding a new fetch. Parsers live in `property_scraper.py`; `api/fixtures/property_homedetails.json` documents the expected shape and the tests assert parser output **and** that it serializes (a string field like `resoFacts.hoaFee` into a `FloatField` is the classic break — clean it in the parser).
- **What is and isn't on the `property` object** — verified against live homedetails responses (2026-07): `attributionInfo`, `listedBy`, `openHouseSchedule`, `mortgageZHLRates`, `propertyTaxRate`, `monthlyHoaFee`, `resoFacts` (~187 keys, mostly null), `listing_sub_type`, `priceChange`, `nearbyCities`/`nearbyNeighborhoods`/`nearbyZipcodes`, `parcelId`, `countyFIPS` are all present. **`climate` (First Street risk), `walkScore`/`transitScore`/`bikeScore`, and `nearbyHomes` are NOT** — Zillow loads those via separate calls, so any endpoint exposing them needs its own fetch, not a parser.
- **`/similarHomes` is rebuilt from search, not parsed** — `nearbyHomes` is gone from the property object, and Zillow's own comps API is behind `zg-graph`, which rejects anything outside its persisted-query safelist (`QUERY_NOT_IN_SAFELIST`) — so there is no comps endpoint to scrape. `get_similar_homes` instead runs a tight map-bounds search around the subject (`SIMILAR_TIGHT_DELTA_DEG`, widened once to `SIMILAR_WIDE_DELTA_DEG` if thin), drops the subject, and ranks candidates with `_similarity_score` (distance/sqft/beds/baths/price/home type, weights in `SIMILARITY_WEIGHTS`, lower is closer). `list_type` defaults to the subject's own status, so comps for a sold home are sold homes. Costs one search fetch on top of the cached subject; cached separately as `similar:{zpid}:{list_type}:{count}`.
- **Sold listings have no sale price in the search index** — `soldPrice` is an empty string on every sold card and `price`/`lastSoldPrice` are absent (verified live 2026-07-30); Zillow only ships the sale price on the homedetails page. `parse_property_card` therefore also surfaces `zestimate` and `date_sold`, which is what makes a sold result (and a sold comp) usable. Don't "fix" a null price on sold results by substituting the zestimate.
- **HOA fees carry their own period** — `monthlyHoaFee` is already monthly and is preferred; the `resoFacts.hoaFee` fallback is a string like `"$529 semi-annually"`. `PropertyScraper._hoa_monthly` normalizes via `HOA_PERIOD_DIVISORS`, matching **longest label first** (`"annually"` is a substring of `"semi-annually"`).
- **`/byAddress`** — `PropertyScraper.search_by_address` resolves an address via `search_by_location` (Zillow redirects an exact address to `/homedetails/`), takes the best-match zpid, and returns the rich `get_property_details` payload. Two fetches on a cold cache, but the detail half is `property:{zpid}`-cached.
- **Search `listType` + `sort`** — the geo searches build a `searchQueryState` with `filterState`. `_apply_list_type` toggles for-sale (Zillow default, untouched) / for-rent (`isForRent`) / sold (`isRecentlySold`); before this, `search_by_map_bounds` (and thus coordinates/polygon) silently ignored `list_type`. `sort` maps friendly names via `SORT_MAP`/`resolve_sort` to a `sortSelection` token, passing unknown tokens through (Zillow ignores bad ones). `search_by_location` is path-based, so `sort` is appended as `?searchQueryState=` (region still from the slug); only `days`/`globalrelevanceex` tokens are high-confidence — verify the rest against a live response.
- **`/marketStats` reads the home-values page** — `https://www.zillow.com/{slug}/home-values/` carries an `odpMarketAnalytics` block in `__NEXT_DATA__`. Plain page fetch, so no safelist problem. Three traps live here, all covered by tests: (1) `medianDaysToPending` and the sale-to-list figures are **only** on the `mrktListingRange`/`mrktSaleRange` arrays, never on the `*Latest` objects — read `[0]`; (2) listing and sale aggregates are published on different monthly cycles, hence separate `listings_as_of` / `sales_as_of` rather than one reconciled date; (3) `parentage` is misnamed — it holds the region's *children* too (73 zipcodes for Austin), so `_region_ancestors` filters by `REGION_HIERARCHY` rank. Region slugs are unforgiving: `/austin-tx/` works, `/90210/` 404s. `_fetch_market_page` tries the literal slug first and only falls back to an autocomplete lookup (building `los-angeles-ca-90210`) on a miss — resolving eagerly would double the request count on the common path. Cached 6h, not 15min, since the figures are monthly. `zhvfIndex1` (forecast) exists in the payload but is empty in practice — don't advertise it.
- **`/autocomplete` uses the CDN suggestions service** — `https://www.zillowstatic.com/autocomplete/v3/suggestions?q=` (on `zillowstatic.com`; the same path under `www.zillow.com` 404s). It returns region hits (`region_id`, `region_type`, county, lat/lng) and address hits (**`zpid`** — the cheapest address→zpid resolution in the API, ~0.6s vs. `/byAddress`'s two page fetches). It replaced a hand-written `zg-graph` GraphQL POST that was never on the safelist, always failed, and fell through to a stub echoing the query back as a city name.
- **`core/`** — cross-cutting infra: `proxy_manager`, `user_agent_manager`, `rate_limiter`, `middleware`.
- **`zillow_scraper/`** — Django project (settings, urls, wsgi, celery).

### Session pooling — the core performance mechanism

`scrapers/base.py` `SessionPool` (thread-safe singleton) keeps warm `curl_cffi` sessions keyed by proxy. The first request on a fresh session takes ~10s (TLS handshake + PerimeterX challenge cookies); reused sessions take ~1.3s. Sessions auto-refresh every 50 requests and are invalidated after repeated blocks. `scrapers/apps.py` pre-warms a session in a background thread on server startup. All HTTP goes through `curl_cffi` with `impersonate="chrome"` to defeat TLS fingerprinting — **do not swap in plain `requests` for scraping paths**; it will get blocked.

`BaseScraper._make_request` handles retries (recursive, up to `MAX_RETRIES`), delays, and maps HTTP status to exceptions: 403/429 → `BlockedException`, 404 → `NotFoundException`.

### Proxy selection is request-host-aware

`core/middleware.RequestMiddleware` stashes the current request in thread-locals so `proxy_manager.get_proxy()` can read the request host with no argument threading. When the host is the `zillow-com-live-data-scraper-api.p.rapidapi.com` RapidAPI listing, it uses the `PROXIES_LIVE_DATA` proxy pool (and raises if that pool is empty); otherwise it uses `PROXIES`. The proxy provider is assumed to rotate IPs itself, so `mark_proxy_failed`/`mark_proxy_success` are no-ops.

### RapidAPI gating

`core.middleware.RapidAPIOnlyMiddleware` rejects requests that lack RapidAPI's `X-RapidAPI-Proxy-Secret` header, which stops callers from hitting the server's IP directly and bypassing billing. It **fails open**: with `RAPIDAPI_PROXY_SECRET` unset it enforces nothing and only logs (once per state) whether the header is arriving — set the secret after the logs confirm it. Paths in `RAPIDAPI_EXEMPT_PATHS` (default `/health`) always pass. Rejections are a real HTTP 403, not the 200-error contract below, because non-RapidAPI traffic isn't counted in the listing's metrics.

### Error contract — everything returns HTTP 200

`api/exceptions.custom_exception_handler` forces **all** errors (validation, `NotFoundException`, `BlockedException`, `ScraperException`, unhandled) to HTTP 200 with `success: false` and a `status_code` field in the body. This is deliberate: RapidAPI counts non-2xx responses against the API's reliability metric. Views also return validation errors (missing params) as HTTP 200 bodies directly. `NotFoundException` returns an empty paginated result, not an error. Preserve this convention when adding endpoints.

### Pagination

`build_paginated_response` in `views.py` wraps results as `{count, results, pagination}`. Property searches cap at `max_pages=20` (Zillow limits results to ~800 / 20 pages; `search_by_location` raises past page 20).

### Async / Celery

`api/tasks.py` defines Celery tasks (currently not wired into the sync request path — the API scrapes synchronously). Celery worker + beat run as separate compose services; broker/backend is Redis. `celerybeat-schedule` is a generated runtime file.

## Notes

- `scripts/` (`debug_agent_api.py`, etc.) and the `debug_fetch` / `debug_html` views are ad-hoc debugging tools for inspecting raw Zillow HTML/JSON. Neither debug view is routed in `urls.py`, so both are currently unreachable dead code; `python manage.py canary` covers the same ground better.
- Zillow's page structure changes; parsing helpers in `utils.py` try multiple JSON paths defensively. When results come back empty, the extraction paths are the first place to look.
- **The unit suite never touches the network** — it runs against `api/fixtures/property_homedetails.json`, so it stays green when Zillow changes shape and a parser silently starts returning `[]`. That is exactly how `/similarHomes` and `/autocomplete` stayed broken. `python manage.py canary` is the counterweight: it makes real requests, asserts each path still yields the fields customers pay for, and exits 1 on failure (2 if it couldn't run at all). Run it on a schedule and alert on the exit code. It sources a live zpid from a search each run rather than hardcoding one that will go off-market. FAIL = a field Zillow reliably ships came back empty (parser break); WARN = data legitimately absent on many listings. **When you add an endpoint, add a canary check for it** — a fixture test alone cannot tell you it still works.
