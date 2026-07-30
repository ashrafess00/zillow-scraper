"""
Live canary — exercise every scraper path against real Zillow and report which
ones have gone empty.

The unit suite runs entirely against `api/fixtures/property_homedetails.json`, so
it stays green when Zillow changes its JSON shape and a parser silently starts
returning `[]`. That is the failure mode this command exists to catch: it makes
real requests, asserts each path still yields the fields customers pay for, and
exits non-zero so cron or CI can alert on it.

    python manage.py canary                 # human-readable table
    python manage.py canary --json          # machine-readable, for alerting
    python manage.py canary --location miami-fl

Exit codes: 0 = all good (warnings allowed), 1 = at least one check FAILed,
2 = the run itself could not proceed (e.g. every request blocked).

FAIL means a field that Zillow reliably ships came back empty — treat it as a
parser break. WARN means data that is legitimately absent on many listings
(open houses, price cuts) was missing, which is not actionable on its own.
"""

import json
import time
import traceback

from django.core.management.base import BaseCommand

from scrapers.agent_scraper import agent_scraper
from scrapers.property_scraper import property_scraper

PASS, WARN, FAIL = 'PASS', 'WARN', 'FAIL'


class CheckError(Exception):
    """A check could not run at all (as opposed to running and finding nothing)."""


def _nonempty(value) -> bool:
    return value not in (None, '', [], {})


def _require(payload, keys, label):
    """FAIL unless every key in `keys` is present and non-empty on `payload`."""
    missing = [k for k in keys if not _nonempty(payload.get(k))]
    if missing:
        return FAIL, f"{label}: empty {', '.join(missing)}"
    return PASS, label


class Command(BaseCommand):
    help = "Run live checks against Zillow and report which scraper paths broke."

    def add_arguments(self, parser):
        parser.add_argument(
            '--location', default='austin-tx',
            help='Location slug used to source a live zpid and test search (default: austin-tx)',
        )
        parser.add_argument(
            '--agent-location', default='los-angeles',
            help='Location slug for the agent checks (default: los-angeles)',
        )
        parser.add_argument(
            '--json', action='store_true', dest='as_json',
            help='Emit JSON instead of a table',
        )

    # -- individual checks -------------------------------------------------
    # Each returns (status, detail). Raising CheckError marks it FAIL with the
    # exception text; any other exception is caught and reported the same way,
    # so one broken path never hides the others.

    def check_search_for_sale(self, ctx):
        result = property_scraper.search_by_location(ctx['location'], list_type='for-sale')
        results = result.get('results') or []
        if not results:
            return FAIL, 'no for-sale results'
        # Stash a live zpid for the detail checks rather than hardcoding one that
        # will eventually go off-market.
        for card in results:
            if card.get('zpid'):
                ctx['zpid'] = card['zpid']
                break
        priced = [c for c in results if _nonempty(c.get('price'))]
        addressed = [c for c in results if _nonempty(c.get('address'))]
        if len(priced) < len(results) * 0.8:
            return FAIL, f"only {len(priced)}/{len(results)} cards have a price"
        if len(addressed) < len(results) * 0.8:
            return FAIL, f"only {len(addressed)}/{len(results)} cards have an address"
        return PASS, f"{len(results)} cards, {len(priced)} priced"

    def check_search_sold(self, ctx):
        result = property_scraper.search_by_location(ctx['location'], list_type='sold')
        results = result.get('results') or []
        if not results:
            return FAIL, 'no sold results'
        # Sold cards carry no sale price upstream; the zestimate is what makes
        # them usable, so that is what we assert on.
        with_zest = [c for c in results if _nonempty(c.get('zestimate'))]
        with_date = [c for c in results if _nonempty(c.get('date_sold'))]
        if not with_zest:
            return FAIL, f"{len(results)} sold cards, none with a zestimate"
        if not with_date:
            return WARN, f"{len(results)} sold cards, none with a sold date"
        return PASS, f"{len(results)} cards, {len(with_zest)} with zestimate"

    def check_property_detail(self, ctx):
        data = property_scraper.get_property_details(ctx['zpid'])
        return _require(
            data, ['zpid', 'address', 'price', 'beds', 'baths', 'sqft'],
            f"zpid {ctx['zpid']}",
        )

    def check_zestimate(self, ctx):
        data = property_scraper.get_zestimate(ctx['zpid'])
        if not _nonempty(data.get('zestimate')):
            return WARN, 'no zestimate on this listing'
        return PASS, f"zestimate {data['zestimate']}"

    def check_price_history(self, ctx):
        events = property_scraper.get_price_history(ctx['zpid'])
        # `priceHistory` is genuinely null on some listings (a freshly listed
        # home with no events yet), so an empty list is not on its own a break —
        # a real one shows up as WARN on every location, run after run. Events
        # that exist but carry no date *are* a parser break.
        if not events:
            return WARN, 'no price history on this listing'
        if not _nonempty(events[0].get('date')):
            return FAIL, 'price history events have no date'
        return PASS, f"{len(events)} events"

    def check_tax_history(self, ctx):
        events = property_scraper.get_tax_history(ctx['zpid'])
        if not events:
            return WARN, 'no tax history on this listing'
        return PASS, f"{len(events)} events"

    def check_photos(self, ctx):
        photos = property_scraper.get_property_photos(ctx['zpid'])
        if not photos:
            return FAIL, 'no photos'
        if not all(str(p).startswith('http') for p in photos):
            return FAIL, 'photo entries are not URLs'
        return PASS, f"{len(photos)} photos"

    def check_schools(self, ctx):
        schools = property_scraper.get_schools(ctx['zpid'])
        if not schools:
            return WARN, 'no schools listed'
        return PASS, f"{len(schools)} schools"

    def check_similar_homes(self, ctx):
        homes = property_scraper.get_similar_homes(ctx['zpid'], count=5)
        if not homes:
            return FAIL, 'no comps returned'
        first = homes[0]
        if first.get('distance_miles') is None:
            return FAIL, 'comps missing distance_miles'
        if not _nonempty(first.get('address')):
            return FAIL, 'comps missing address'
        return PASS, f"{len(homes)} comps, nearest {first['distance_miles']} mi"

    def check_listing_agent(self, ctx):
        data = property_scraper.get_listing_agent(ctx['zpid'])
        if not _nonempty(data.get('agent_name')) and not _nonempty(data.get('broker_name')):
            return FAIL, 'no agent or broker attribution'
        return PASS, data.get('agent_name') or data.get('broker_name')

    def check_home_facts(self, ctx):
        data = property_scraper.get_home_facts(ctx['zpid'])
        count = data.get('fact_count') or 0
        if count < 20:
            return FAIL, f"only {count} facts (expect 70-90)"
        return PASS, f"{count} facts"

    def check_tax_assessment(self, ctx):
        data = property_scraper.get_tax_assessment(ctx['zpid'])
        if not _nonempty(data.get('tax_assessed_value')):
            return WARN, 'no assessed value on this listing'
        return PASS, f"assessed {data['tax_assessed_value']}"

    def check_nearby_areas(self, ctx):
        data = property_scraper.get_nearby_areas(ctx['zpid'])
        total = sum(len(data.get(k) or []) for k in ('cities', 'neighborhoods', 'zipcodes'))
        if not total:
            return FAIL, 'no nearby areas'
        return PASS, f"{total} areas"

    def check_listing_status(self, ctx):
        data = property_scraper.get_listing_status(ctx['zpid'])
        return _require(data, ['status'], f"status {data.get('status')}")

    def check_monthly_cost(self, ctx):
        data = property_scraper.get_monthly_cost(ctx['zpid'])
        if not _nonempty(data.get('total_monthly')):
            return FAIL, 'no total_monthly'
        return PASS, f"{data['total_monthly']}/mo"

    def check_market_stats(self, ctx):
        stats = property_scraper.get_market_stats(ctx['location'])
        state, detail = _require(
            stats,
            ['home_value_index', 'median_list_price', 'for_sale_inventory'],
            f"{stats.get('region', {}).get('name') or ctx['location']}: "
            f"ZHVI {stats.get('home_value_index')}",
        )
        if state == FAIL:
            return state, detail
        if not (stats.get('region') or {}).get('id'):
            return FAIL, 'region did not resolve to a region id'
        return state, detail

    def check_market_stats_zipcode(self, ctx):
        """A bare zipcode only works via the autocomplete slug fallback."""
        stats = property_scraper.get_market_stats('90210')
        region = stats.get('region') or {}
        if region.get('type') != 'zipcode':
            return FAIL, f"resolved to {region.get('type')!r}, expected 'zipcode'"
        if not stats.get('home_value_index'):
            return FAIL, 'no home value index for the zipcode'
        return PASS, f"slug fallback -> {region.get('slug')}"

    def check_market_stats_history(self, ctx):
        stats = property_scraper.get_market_stats(ctx['location'], history=True)
        series = (stats.get('history') or {}).get('home_value_index') or []
        if len(series) < 12:
            return FAIL, f"only {len(series)} months of home value index"
        return PASS, f"{len(series)} months"

    def check_agents_by_location(self, ctx):
        result = agent_scraper.get_agents_by_location(ctx['agent_location'])
        agents = result.get('results') or []
        if not agents:
            return FAIL, 'no agents'
        named = [a for a in agents if _nonempty(a.get('name'))]
        if not named:
            return FAIL, f"{len(agents)} agents, none with a name"
        ctx['agent_url'] = next((a['url'] for a in agents if a.get('url')), None)
        return PASS, f"{len(agents)} agents"

    def check_agent_info(self, ctx):
        if not ctx.get('agent_url'):
            raise CheckError('no agent URL from the previous check')
        data = agent_scraper.get_agent_info(url=ctx['agent_url'])
        result = data.get('result') or data
        return _require(result, ['name'], result.get('name', ''))

    def check_autocomplete(self, ctx):
        suggestions = property_scraper.autocomplete('seattle')
        if not suggestions:
            return FAIL, 'no suggestions'
        if not any(s.get('region_id') for s in suggestions):
            return FAIL, f"{len(suggestions)} suggestions, none with a region_id"
        return PASS, f"{len(suggestions)} suggestions"

    def check_autocomplete_address(self, ctx):
        """An address query must resolve to a zpid — that's what makes it useful."""
        suggestions = property_scraper.autocomplete('1006 Hollybluff St Austin TX')
        addresses = [s for s in suggestions if s.get('zpid')]
        if not addresses:
            return FAIL, f"{len(suggestions)} suggestions, none resolved to a zpid"
        return PASS, f"resolved to zpid {addresses[0]['zpid']}"

    CHECKS = [
        ('search:for-sale', 'check_search_for_sale'),
        ('search:sold', 'check_search_sold'),
        ('property', 'check_property_detail'),
        ('zestimate', 'check_zestimate'),
        ('priceHistory', 'check_price_history'),
        ('taxHistory', 'check_tax_history'),
        ('photos', 'check_photos'),
        ('schools', 'check_schools'),
        ('similarHomes', 'check_similar_homes'),
        ('listingAgent', 'check_listing_agent'),
        ('homeFacts', 'check_home_facts'),
        ('taxAssessment', 'check_tax_assessment'),
        ('nearbyAreas', 'check_nearby_areas'),
        ('listingStatus', 'check_listing_status'),
        ('monthlyCost', 'check_monthly_cost'),
        ('marketStats', 'check_market_stats'),
        ('marketStats:zip', 'check_market_stats_zipcode'),
        ('marketStats:hist', 'check_market_stats_history'),
        ('agentByLocation', 'check_agents_by_location'),
        ('agentInfo', 'check_agent_info'),
        ('autocomplete', 'check_autocomplete'),
        ('autocomplete:addr', 'check_autocomplete_address'),
    ]

    def handle(self, *args, **options):
        as_json = options['as_json']
        ctx = {
            'location': options['location'],
            'agent_location': options['agent_location'],
            'zpid': None,
        }

        rows = []
        for name, method_name in self.CHECKS:
            # The detail checks all key off a zpid discovered by the first check.
            if ctx['zpid'] is None and method_name.startswith('check_') and \
                    name not in ('search:for-sale', 'search:sold', 'agentByLocation',
                                 'agentInfo', 'autocomplete', 'autocomplete:addr',
                                 'marketStats', 'marketStats:zip', 'marketStats:hist'):
                rows.append({'check': name, 'status': FAIL, 'detail':
                             'skipped: no live zpid (the for-sale search failed)',
                             'seconds': 0.0})
                continue

            started = time.time()
            try:
                state, detail = getattr(self, method_name)(ctx)
            except CheckError as e:
                state, detail = FAIL, str(e)
            except Exception as e:
                state = FAIL
                detail = f"{type(e).__name__}: {e}"
                # A canary is read by cron and alerting, so the default output is
                # the one-line reason. Full stacks only on --traceback.
                if options.get('traceback'):
                    self.stderr.write(traceback.format_exc())
            rows.append({
                'check': name, 'status': state, 'detail': detail,
                'seconds': round(time.time() - started, 2),
            })

        failures = [r for r in rows if r['status'] == FAIL]
        warnings = [r for r in rows if r['status'] == WARN]

        if as_json:
            self.stdout.write(json.dumps({
                'location': ctx['location'],
                'zpid': ctx['zpid'],
                'passed': len(rows) - len(failures) - len(warnings),
                'warned': len(warnings),
                'failed': len(failures),
                'checks': rows,
            }, indent=2))
        else:
            self.stdout.write(f"\nCanary — location={ctx['location']} zpid={ctx['zpid']}\n")
            for r in rows:
                colour = {
                    PASS: self.style.SUCCESS,
                    WARN: self.style.WARNING,
                    FAIL: self.style.ERROR,
                }[r['status']]
                self.stdout.write(
                    f"  {colour(r['status']):<4}  {r['check']:<18} "
                    f"{r['seconds']:>6.2f}s  {r['detail']}"
                )
            self.stdout.write(
                f"\n{len(rows) - len(failures) - len(warnings)} passed, "
                f"{len(warnings)} warned, {len(failures)} failed\n"
            )

        if ctx['zpid'] is None and len(failures) == len(rows):
            raise SystemExit(2)
        if failures:
            raise SystemExit(1)
