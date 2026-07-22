"""Deterministic offline fixtures for demos and tests."""

from decimal import Decimal

from caffeine_scout.models import RawOffer, RetailerSource, SearchRequest


class MockSource(RetailerSource):
    name = "mock"

    async def search(self, request: SearchRequest) -> list[RawOffer]:
        fixtures = [
            RawOffer(
                source=self.name,
                retailer="MockMart Online",
                source_product_id="MM-ALANI-12",
                product_name="Alani Nu Energy Drink Cherry Slush, 12 Fl Oz Cans, Pack of 12",
                listed_price=Decimal("18.99"),
                coupon_value=Decimal("2.00"),
                shipping_cost=Decimal("0"),
                fulfillment_type="online",
                in_stock=True,
                advertised_discount_percent=10,
                url="https://example.test/alani-cherry-12",
                notes=["Immediate checkout coupon applied"],
                data_confidence=0.98,
            ),
            RawOffer(
                source=self.name,
                retailer="MockMart Center City",
                source_product_id="MM-GHOST-12",
                product_name="GHOST Energy Zero Sugar, Sour Patch Kids Redberry, 16oz (12 Pack)",
                listed_price=Decimal("23.88"),
                fulfillment_type="pickup",
                store_name="MockMart Center City",
                store_address="1600 Market St, Philadelphia, PA 19103",
                distance_miles=0.4,
                in_stock=True,
                url="https://example.test/ghost-redberry-12",
                data_confidence=0.97,
            ),
            RawOffer(
                source=self.name,
                retailer="BulkBox",
                source_product_id="BB-C4-24",
                product_name="C4 Performance Energy Drink Variety Pack, 12 Count",
                listed_price=Decimal("19.49"),
                shipping_cost=Decimal("4.99"),
                fulfillment_type="online",
                in_stock=True,
                membership_required=True,
                url="https://example.test/c4-variety-12",
                notes=["Paid membership price; membership fee not included"],
                data_confidence=0.95,
            ),
            RawOffer(
                source=self.name,
                retailer="MockMart Online",
                source_product_id="MM-ALANI-24",
                product_name="Alani Nu 24-Pack 12 oz Energy Drinks",
                listed_price=Decimal("34.99"),
                shipping_cost=Decimal("0"),
                fulfillment_type="online",
                in_stock=True,
                subscription_required=True,
                url="https://example.test/alani-variety-24",
                notes=["Subscription discount is not included in effective price"],
                data_confidence=0.94,
            ),
            RawOffer(
                source=self.name,
                retailer="MockMart Online",
                source_product_id="MM-MONSTER-12",
                product_name="Monster Energy Original - 12pk/16 fl oz Cans",
                listed_price=Decimal("22.99"),
                caffeine_mg_per_can=160,
                shipping_cost=Decimal("0"),
                fulfillment_type="online",
                in_stock=True,
                url="https://example.test/monster-original-12",
                data_confidence=0.98,
            ),
        ]
        requested = {brand.casefold() for brand in request.brands}
        selected = [
            offer
            for offer in fixtures
            if not requested or any(brand in offer.product_name.casefold() for brand in requested)
        ]
        if request.online_only:
            selected = [offer for offer in selected if offer.fulfillment_type == "online"]
        if request.pickup_only:
            selected = [offer for offer in selected if offer.fulfillment_type == "pickup"]
        return selected
