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
    type = serializers.CharField()
    id = serializers.CharField(required=False, allow_blank=True)


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


class ErrorSerializer(serializers.Serializer):
    """Serializer for error responses."""

    error = serializers.CharField()
    message = serializers.CharField()
    status_code = serializers.IntegerField()
