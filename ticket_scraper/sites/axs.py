"""
AXS scraper implementation.

AXS features:
- Primary market
- Used by many venues (AEG properties)
- Fees added on top
- HTML parsing needed
"""

import json
import re
from typing import Any, Optional

import requests
from bs4 import BeautifulSoup

from ..base import BaseScraper, ScraperResult, ListingInfo
from ..utils import HEADERS, extract_json_ld, extract_event_from_json_ld, parse_price


class AXSScraper(BaseScraper):
    """Scraper for AXS ticket listings."""

    name = "axs"
    all_in_pricing = False  # AXS adds fees

    def _fetch_html(self, url: str) -> str:
        """Fetch HTML content."""
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        return response.text

    def _extract_listings_from_html(self, html: str) -> list[ListingInfo]:
        """Extract listings from HTML."""
        soup = BeautifulSoup(html, "html.parser")
        listings = []

        # AXS listing patterns
        listing_selectors = [
            '[data-testid*="ticket"]',
            '[class*="ticket-row"]',
            '[class*="price-level"]',
            '.ticket-card',
        ]

        listing_elements = []
        for selector in listing_selectors:
            elements = soup.select(selector)
            if elements:
                listing_elements = elements
                break

        for element in listing_elements:
            listing = self._parse_listing_element(element)
            if listing:
                listings.append(listing)

        # Fallback to text extraction
        if not listings:
            listings = self._extract_from_text(soup.get_text(separator="\n"))

        listings.sort(key=lambda x: x.price)
        return listings

    def _parse_listing_element(self, element) -> Optional[ListingInfo]:
        """Parse a listing element."""
        text = element.get_text(separator=" ")

        price_match = re.search(r'\$([0-9,]+(?:\.\d{2})?)', text)
        if not price_match:
            return None

        price = parse_price(price_match.group(1))
        if not price:
            return None

        section = ""
        section_match = re.search(r'(?:Section|Sec\.?)\s*([A-Za-z0-9]+)', text, re.I)
        if section_match:
            section = section_match.group(1)

        row = ""
        row_match = re.search(r'Row\s+([A-Za-z0-9]+)', text, re.I)
        if row_match:
            row = row_match.group(1)

        labels = []
        if "best available" in text.lower():
            labels.append("Best Available")
        if "limited" in text.lower():
            labels.append("Limited Availability")

        return ListingInfo(
            price=price,
            section=section,
            row=row,
            labels=labels,
        )

    def _extract_from_text(self, text: str) -> list[ListingInfo]:
        """Extract listings from text."""
        listings = []

        # Look for price tier patterns (common in AXS)
        tier_pattern = re.compile(
            r'(?:Level|Tier|Section)\s*([A-Za-z0-9]+).*?'
            r'\$([0-9,]+(?:\.\d{2})?)',
            re.IGNORECASE | re.DOTALL
        )

        for match in tier_pattern.finditer(text):
            price = parse_price(match.group(2))
            if price:
                listings.append(ListingInfo(
                    price=price,
                    section=match.group(1),
                ))

        # Also try standard section/row/price pattern
        if not listings:
            pattern = re.compile(
                r'\$([0-9,]+(?:\.\d{2})?)',
                re.IGNORECASE
            )
            for match in pattern.finditer(text):
                price = parse_price(match.group(1))
                if price and price > 10:  # Filter out unlikely prices
                    listings.append(ListingInfo(price=price))

        return listings

    def _extract_embedded_data(self, html: str) -> tuple[dict, list[ListingInfo]]:
        """Extract data from embedded JavaScript."""
        soup = BeautifulSoup(html, "html.parser")
        event_info = {}
        listings = []

        for script in soup.find_all("script"):
            if not script.string:
                continue

            try:
                # Look for event/ticket data
                if "eventData" in script.string or "ticketData" in script.string:
                    # Try various JSON patterns
                    patterns = [
                        r'eventData\s*=\s*({.+?});',
                        r'ticketData\s*=\s*({.+?});',
                        r'"event"\s*:\s*({.+?})[,}]',
                    ]

                    for pattern in patterns:
                        match = re.search(pattern, script.string, re.DOTALL)
                        if match:
                            try:
                                data = json.loads(match.group(1))

                                if "name" in data:
                                    event_info = {
                                        "name": data.get("name", ""),
                                        "startDate": data.get("startDate", "") or data.get("date", ""),
                                        "venue": data.get("venue", {}).get("name", "") if isinstance(data.get("venue"), dict) else "",
                                    }

                                # Extract price levels
                                price_levels = data.get("priceLevels", []) or data.get("offers", [])
                                for level in price_levels:
                                    price = parse_price(level.get("price") or level.get("min"))
                                    if price:
                                        listings.append(ListingInfo(
                                            price=price,
                                            section=level.get("name", "") or level.get("section", ""),
                                            listing_id=level.get("id"),
                                        ))
                            except json.JSONDecodeError:
                                continue

            except (AttributeError, KeyError):
                continue

        return event_info, listings

    def get_listings(self, url: str, quantity: int = 2) -> list[ListingInfo]:
        """Get all available ticket listings."""
        html = self._fetch_html(url)

        _, listings = self._extract_embedded_data(html)

        if not listings:
            listings = self._extract_listings_from_html(html)

        listings.sort(key=lambda x: x.price)
        return listings

    def get_event_info(self, url: str) -> dict[str, Any]:
        """Get event metadata."""
        html = self._fetch_html(url)

        event_info, _ = self._extract_embedded_data(html)
        if event_info.get("name"):
            return event_info

        json_ld = extract_json_ld(html)
        return extract_event_from_json_ld(json_ld)

    def get_lowest_price(self, url: str, quantity: int = 2) -> ScraperResult:
        """Get the lowest price for an event."""
        html = self._fetch_html(url)

        event_info, listings = self._extract_embedded_data(html)
        extraction_method = "embedded_data" if listings else "unknown"

        if not listings:
            listings = self._extract_listings_from_html(html)
            extraction_method = "html_parse" if listings else "unknown"

        if not event_info.get("name"):
            json_ld = extract_json_ld(html)
            event_info = extract_event_from_json_ld(json_ld)

        listings.sort(key=lambda x: x.price)
        cheapest = listings[0] if listings else None
        lowest_price = cheapest.price if cheapest else None

        return ScraperResult(
            source=self.name,
            event_name=event_info.get("name", ""),
            event_date=event_info.get("startDate", ""),
            venue=event_info.get("venue", ""),
            lowest_all_in=lowest_price,
            fees_included=self.all_in_pricing,
            cheapest_listing=cheapest,
            listings_count=len(listings),
            url=url,
            extraction_method=extraction_method,
        )
