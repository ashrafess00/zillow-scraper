"""
Serializers for the Zillow scraper API.
"""

from rest_framework import serializers


class AgentSerializer(serializers.Serializer):
    """Serializer for agent data."""
    
    name = serializers.CharField()
    url = serializers.CharField()
    photo_url = serializers.CharField(required=False, allow_blank=True)
    brokerage = serializers.CharField(required=False, allow_blank=True)
    location = serializers.CharField(required=False, allow_blank=True)
    phone = serializers.CharField(required=False, allow_blank=True)
    rating = serializers.FloatField(required=False, allow_null=True)
    reviews_count = serializers.IntegerField(required=False, allow_null=True)
    sales_count = serializers.IntegerField(required=False, allow_null=True)
    price_range = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    is_team = serializers.BooleanField(required=False, default=False)
    bio = serializers.CharField(required=False, allow_blank=True)


class PropertySerializer(serializers.Serializer):
    """Serializer for property data."""
    
    zpid = serializers.IntegerField(required=False, allow_null=True)
    address = serializers.CharField()
    url = serializers.CharField(required=False, allow_blank=True)
    photo_url = serializers.CharField(required=False, allow_blank=True)
    price = serializers.FloatField(required=False, allow_null=True)
    beds = serializers.IntegerField(required=False, allow_null=True)
    baths = serializers.IntegerField(required=False, allow_null=True)
    sqft = serializers.IntegerField(required=False, allow_null=True)
    property_type = serializers.CharField(required=False, allow_blank=True)
    status = serializers.CharField(required=False, allow_blank=True)
    latitude = serializers.FloatField(required=False, allow_null=True)
    longitude = serializers.FloatField(required=False, allow_null=True)
    brokerage = serializers.CharField(required=False, allow_blank=True)
    # Sold listings carry no sale price in Zillow's search index — these two are
    # what make a sold result usable. See parse_property_card.
    zestimate = serializers.FloatField(required=False, allow_null=True)
    date_sold = serializers.CharField(required=False, allow_blank=True)


class SimilarHomeSerializer(PropertySerializer):
    """A comp from /similarHomes — a property card plus its ranking fields."""

    distance_miles = serializers.FloatField(required=False, allow_null=True)
    similarity_score = serializers.FloatField(required=False, allow_null=True)


class MarketAncestorSerializer(serializers.Serializer):
    """A region containing the one being described."""

    name = serializers.CharField()
    type = serializers.CharField(
        required=False, allow_blank=True,
        help_text="country, state, dma, cbsa, county or city",
    )


class MarketRegionSerializer(serializers.Serializer):
    """The region a /marketStats response describes."""

    id = serializers.IntegerField(required=False, allow_null=True)
    name = serializers.CharField(required=False, allow_blank=True)
    type = serializers.CharField(
        required=False, allow_blank=True,
        help_text="city, zipcode, neighborhood, county or state",
    )
    url = serializers.CharField(required=False, allow_blank=True)
    slug = serializers.CharField(required=False, allow_blank=True)
    parentage = MarketAncestorSerializer(
        many=True, required=False,
        help_text="Regions containing this one, widest first (country → state → county)",
    )


class MarketBenchmarkSerializer(serializers.Serializer):
    """Parent-region figures that give a region's numbers context."""

    county_home_value_index = serializers.FloatField(required=False, allow_null=True)
    state_home_value_index = serializers.FloatField(required=False, allow_null=True)
    national_home_value_index = serializers.FloatField(required=False, allow_null=True)
    national_median_rent = serializers.FloatField(required=False, allow_null=True)


class MarketSeriesPointSerializer(serializers.Serializer):
    """One month of a market time series."""

    date = serializers.CharField()
    value = serializers.FloatField()


class MarketHistorySerializer(serializers.Serializer):
    """Monthly series, newest first. Only present when history=true."""

    home_value_index = MarketSeriesPointSerializer(many=True, required=False)
    median_rent = MarketSeriesPointSerializer(many=True, required=False)
    median_days_to_pending = MarketSeriesPointSerializer(many=True, required=False)
    median_sale_to_list_ratio = MarketSeriesPointSerializer(many=True, required=False)


class MarketStatsSerializer(serializers.Serializer):
    """Market statistics for a region."""

    region = MarketRegionSerializer()
    # Zillow publishes listing and sale aggregates on different monthly cycles,
    # so each carries its own period end rather than being forced into one date.
    listings_as_of = serializers.CharField(required=False, allow_blank=True)
    sales_as_of = serializers.CharField(required=False, allow_blank=True)

    home_value_index = serializers.FloatField(
        required=False, allow_null=True,
        help_text="Zillow Home Value Index (ZHVI) for the region",
    )
    home_value_index_yoy_pct = serializers.FloatField(
        required=False, allow_null=True,
        help_text="Year-over-year change in ZHVI, as a percentage (-5.02 = down 5.02%)",
    )
    median_list_price = serializers.FloatField(required=False, allow_null=True)
    median_sale_price = serializers.FloatField(required=False, allow_null=True)
    median_days_to_pending = serializers.FloatField(required=False, allow_null=True)
    median_sale_to_list_ratio = serializers.FloatField(
        required=False, allow_null=True,
        help_text="A ratio, not a percentage: 0.98 means homes sell for 98% of list price",
    )
    pct_sold_above_list = serializers.FloatField(required=False, allow_null=True)
    pct_sold_below_list = serializers.FloatField(required=False, allow_null=True)
    for_sale_inventory = serializers.FloatField(required=False, allow_null=True)
    new_listings = serializers.FloatField(required=False, allow_null=True)
    median_rent = serializers.FloatField(
        required=False, allow_null=True,
        help_text="Zillow Observed Rent Index (ZORI)",
    )
    benchmarks = MarketBenchmarkSerializer()
    history = MarketHistorySerializer(required=False)


class ReviewSerializer(serializers.Serializer):
    """Serializer for review data."""
    
    zuid = serializers.CharField()
    rating = serializers.IntegerField()
    review = serializers.CharField()
    reviewer_name = serializers.CharField(required=False, allow_blank=True)
    date = serializers.CharField(required=False, allow_blank=True)
    transaction_type = serializers.CharField(required=False, allow_blank=True)


class PaginationMetadataSerializer(serializers.Serializer):
    """Serializer for pagination metadata."""
    
    total_results = serializers.IntegerField()
    total_pages = serializers.IntegerField()
    current_page = serializers.IntegerField()
    per_page = serializers.IntegerField()
    has_next = serializers.BooleanField()
    has_previous = serializers.BooleanField()



class AutocompleteSuggestionSerializer(serializers.Serializer):
    """Serializer for autocomplete suggestions."""

    display = serializers.CharField()
    type = serializers.CharField(help_text="'region' or 'address'")
    id = serializers.CharField(required=False, allow_blank=True)
    city = serializers.CharField(required=False, allow_blank=True)
    state = serializers.CharField(required=False, allow_blank=True)
    # Region hits: feed region_id back into a search. Address hits: zpid goes
    # straight to any of the detail endpoints.
    region_id = serializers.IntegerField(required=False, allow_null=True)
    region_type = serializers.CharField(required=False, allow_blank=True)
    county = serializers.CharField(required=False, allow_blank=True)
    zipcode = serializers.CharField(required=False, allow_blank=True)
    zpid = serializers.IntegerField(required=False, allow_null=True)
    address_type = serializers.CharField(required=False, allow_blank=True)
    latitude = serializers.FloatField(required=False, allow_null=True)
    longitude = serializers.FloatField(required=False, allow_null=True)


class ApartmentDetailsSerializer(serializers.Serializer):
    """Serializer for apartment details."""
    
    url = serializers.CharField()
    name = serializers.CharField()
    address = serializers.CharField()
    description = serializers.CharField(required=False, allow_blank=True)
    units = serializers.ListField(required=False, default=list)
    amenities = serializers.ListField(required=False, default=list)
    photos = serializers.ListField(required=False, default=list)


class PropertyDetailsSerializer(serializers.Serializer):
    """Serializer for a single property's full details (by zpid)."""

    zpid = serializers.IntegerField(required=False, allow_null=True)
    url = serializers.CharField(required=False, allow_blank=True)
    address = serializers.CharField(required=False, allow_blank=True)
    price = serializers.FloatField(required=False, allow_null=True)
    zestimate = serializers.FloatField(required=False, allow_null=True)
    rent_zestimate = serializers.FloatField(required=False, allow_null=True)
    price_per_sqft = serializers.FloatField(required=False, allow_null=True)
    beds = serializers.IntegerField(required=False, allow_null=True)
    baths = serializers.IntegerField(required=False, allow_null=True)
    sqft = serializers.IntegerField(required=False, allow_null=True)
    lot_size = serializers.FloatField(required=False, allow_null=True)
    year_built = serializers.IntegerField(required=False, allow_null=True)
    property_type = serializers.CharField(required=False, allow_blank=True)
    status = serializers.CharField(required=False, allow_blank=True)
    latitude = serializers.FloatField(required=False, allow_null=True)
    longitude = serializers.FloatField(required=False, allow_null=True)
    description = serializers.CharField(required=False, allow_blank=True)
    brokerage = serializers.CharField(required=False, allow_blank=True)
    mls_id = serializers.CharField(required=False, allow_blank=True)
    mls_name = serializers.CharField(required=False, allow_blank=True)
    hoa_fee = serializers.FloatField(required=False, allow_null=True)
    days_on_zillow = serializers.IntegerField(required=False, allow_null=True)
    page_view_count = serializers.IntegerField(required=False, allow_null=True)
    favorite_count = serializers.IntegerField(required=False, allow_null=True)
    photo_count = serializers.IntegerField(required=False, allow_null=True)
    photo_url = serializers.CharField(required=False, allow_blank=True)


class ZestimateSerializer(serializers.Serializer):
    """Serializer for a property's valuation estimates."""

    zpid = serializers.IntegerField(required=False, allow_null=True)
    zestimate = serializers.FloatField(required=False, allow_null=True)
    rent_zestimate = serializers.FloatField(required=False, allow_null=True)
    price = serializers.FloatField(required=False, allow_null=True)
    currency = serializers.CharField(required=False, allow_blank=True)


class PriceHistoryEventSerializer(serializers.Serializer):
    """Serializer for a single price-history event."""

    date = serializers.CharField(required=False, allow_blank=True)
    event = serializers.CharField(required=False, allow_blank=True)
    price = serializers.FloatField(required=False, allow_null=True)
    price_change_rate = serializers.FloatField(required=False, allow_null=True)
    price_per_sqft = serializers.FloatField(required=False, allow_null=True)
    source = serializers.CharField(required=False, allow_blank=True)


class TaxHistoryEventSerializer(serializers.Serializer):
    """Serializer for a single tax-history event."""

    year = serializers.IntegerField(required=False, allow_null=True)
    tax_paid = serializers.FloatField(required=False, allow_null=True)
    tax_increase_rate = serializers.FloatField(required=False, allow_null=True)
    assessment = serializers.FloatField(required=False, allow_null=True)
    assessment_increase_rate = serializers.FloatField(required=False, allow_null=True)


class SchoolSerializer(serializers.Serializer):
    """Serializer for a nearby/assigned school."""

    name = serializers.CharField(required=False, allow_blank=True)
    rating = serializers.IntegerField(required=False, allow_null=True)
    level = serializers.CharField(required=False, allow_blank=True)
    grades = serializers.CharField(required=False, allow_blank=True)
    distance = serializers.FloatField(required=False, allow_null=True)
    type = serializers.CharField(required=False, allow_blank=True)
    link = serializers.CharField(required=False, allow_blank=True)


class OpenHouseSerializer(serializers.Serializer):
    """Serializer for a single scheduled open house."""

    start_time = serializers.CharField(required=False, allow_blank=True)
    end_time = serializers.CharField(required=False, allow_blank=True)
    description = serializers.CharField(required=False, allow_blank=True)
    display_text = serializers.CharField(required=False, allow_blank=True)


class ListingAgentSerializer(serializers.Serializer):
    """Serializer for listing-agent, co-agent, broker and MLS attribution."""

    zpid = serializers.IntegerField(required=False, allow_null=True)
    agent_name = serializers.CharField(required=False, allow_blank=True)
    agent_phone = serializers.CharField(required=False, allow_blank=True)
    agent_email = serializers.CharField(required=False, allow_blank=True)
    agent_license = serializers.CharField(required=False, allow_blank=True)
    co_agent_name = serializers.CharField(required=False, allow_blank=True)
    co_agent_phone = serializers.CharField(required=False, allow_blank=True)
    co_agent_license = serializers.CharField(required=False, allow_blank=True)
    broker_name = serializers.CharField(required=False, allow_blank=True)
    broker_phone = serializers.CharField(required=False, allow_blank=True)
    listing_offices = serializers.ListField(
        child=serializers.CharField(allow_blank=True), required=False
    )
    buyer_agent_name = serializers.CharField(required=False, allow_blank=True)
    buyer_brokerage_name = serializers.CharField(required=False, allow_blank=True)
    mls_id = serializers.CharField(required=False, allow_blank=True)
    mls_name = serializers.CharField(required=False, allow_blank=True)
    mls_disclaimer = serializers.CharField(required=False, allow_blank=True)
    attribution_contact = serializers.CharField(required=False, allow_blank=True)
    last_updated = serializers.CharField(required=False, allow_blank=True)
    last_checked = serializers.CharField(required=False, allow_blank=True)


class MonthlyCostSerializer(serializers.Serializer):
    """Serializer for the estimated monthly ownership-cost breakdown."""

    zpid = serializers.IntegerField(required=False, allow_null=True)
    price = serializers.FloatField(required=False, allow_null=True)
    down_payment = serializers.FloatField(required=False, allow_null=True)
    down_payment_percent = serializers.FloatField(required=False, allow_null=True)
    loan_amount = serializers.FloatField(required=False, allow_null=True)
    interest_rate = serializers.FloatField(required=False, allow_null=True)
    term_years = serializers.IntegerField(required=False, allow_null=True)
    rate_source = serializers.CharField(required=False, allow_blank=True)
    principal_and_interest = serializers.FloatField(required=False, allow_null=True)
    property_tax = serializers.FloatField(required=False, allow_null=True)
    property_tax_rate = serializers.FloatField(required=False, allow_null=True)
    home_insurance = serializers.FloatField(required=False, allow_null=True)
    hoa_fee = serializers.FloatField(required=False, allow_null=True)
    mortgage_insurance = serializers.FloatField(required=False, allow_null=True)
    total_monthly = serializers.FloatField(required=False, allow_null=True)
    estimated_fields = serializers.ListField(
        child=serializers.CharField(), required=False
    )
    currency = serializers.CharField(required=False, allow_blank=True)


class HomeFactsSerializer(serializers.Serializer):
    """Serializer for the full RESO facts block."""

    zpid = serializers.IntegerField(required=False, allow_null=True)
    fact_count = serializers.IntegerField(required=False, allow_null=True)
    # Zillow's RESO block is a wide, sparse, listing-dependent bag of values —
    # pass it through untyped rather than pinning ~187 shifting fields.
    at_a_glance = serializers.DictField(required=False)
    facts = serializers.DictField(required=False)


class TaxAssessmentSerializer(serializers.Serializer):
    """Serializer for the current tax assessment and parcel identifiers."""

    zpid = serializers.IntegerField(required=False, allow_null=True)
    tax_assessed_value = serializers.FloatField(required=False, allow_null=True)
    tax_annual_amount = serializers.FloatField(required=False, allow_null=True)
    property_tax_rate = serializers.FloatField(required=False, allow_null=True)
    effective_tax_rate = serializers.FloatField(required=False, allow_null=True)
    parcel_id = serializers.CharField(required=False, allow_blank=True)
    county = serializers.CharField(required=False, allow_blank=True)
    county_fips = serializers.CharField(required=False, allow_blank=True)
    zoning = serializers.CharField(required=False, allow_blank=True)
    zoning_description = serializers.CharField(required=False, allow_blank=True)


class NearbyRegionSerializer(serializers.Serializer):
    """Serializer for a single nearby city / neighborhood / zipcode link."""

    name = serializers.CharField(required=False, allow_blank=True)
    path = serializers.CharField(required=False, allow_blank=True)
    url = serializers.CharField(required=False, allow_blank=True)


class NearbyAreasSerializer(serializers.Serializer):
    """Serializer for the nearby regions Zillow links from a listing."""

    zpid = serializers.IntegerField(required=False, allow_null=True)
    cities = NearbyRegionSerializer(many=True, required=False)
    neighborhoods = NearbyRegionSerializer(many=True, required=False)
    zipcodes = NearbyRegionSerializer(many=True, required=False)


class ListingStatusSerializer(serializers.Serializer):
    """Serializer for listing status, price-cut tracking and listing-type flags."""

    zpid = serializers.IntegerField(required=False, allow_null=True)
    status = serializers.CharField(required=False, allow_blank=True)
    listing_type = serializers.CharField(required=False, allow_blank=True)
    price = serializers.FloatField(required=False, allow_null=True)
    price_change = serializers.FloatField(required=False, allow_null=True)
    price_change_date = serializers.CharField(required=False, allow_blank=True)
    date_sold = serializers.CharField(required=False, allow_blank=True)
    last_sold_price = serializers.FloatField(required=False, allow_null=True)
    days_on_zillow = serializers.IntegerField(required=False, allow_null=True)
    time_on_zillow = serializers.CharField(required=False, allow_blank=True)
    contingent_type = serializers.CharField(required=False, allow_blank=True)
    is_fsbo = serializers.BooleanField(required=False)
    is_fsba = serializers.BooleanField(required=False)
    is_new_home = serializers.BooleanField(required=False)
    is_foreclosure = serializers.BooleanField(required=False)
    is_bank_owned = serializers.BooleanField(required=False)
    is_for_auction = serializers.BooleanField(required=False)
    is_coming_soon = serializers.BooleanField(required=False)
    is_pending = serializers.BooleanField(required=False)
    page_view_count = serializers.IntegerField(required=False, allow_null=True)
    favorite_count = serializers.IntegerField(required=False, allow_null=True)


class ErrorSerializer(serializers.Serializer):
    """Serializer for error responses."""

    error = serializers.CharField()
    message = serializers.CharField()
    status_code = serializers.IntegerField()
