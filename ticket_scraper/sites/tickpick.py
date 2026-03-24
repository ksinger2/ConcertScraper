"""
TickPick scraper implementation.

TickPick features:
- All-in pricing (no hidden fees)
- Server-side rendered (no Playwright needed)
- JSON-LD with accurate lowPrice
- Simple requests.get() works
"""

import json
import re
from typing import Any, Optional
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

import requests
from bs4 import BeautifulSoup

from ..base import BaseScraper, ScraperResult, ListingInfo
from ..utils import HEADERS, extract_json_ld, extract_event_from_json_ld, parse_price


class TickPickScraper(BaseScraper):
    """Scraper for TickPick ticket listings."""

    name = "tickpick"
    all_in_pricing = True  # TickPick includes all fees in displayed prices

    def _build_url(self, url: str, quantity: int = 2) -> str:
        """Build URL with quantity parameter."""
        parsed = urlparse(url)
        params = parse_qs(parsed.query)

        if quantity > 0:
            params["qty"] = [str(quantity)]

        new_query = urlencode(params, doseq=True)
        return urlunparse(parsed._replace(query=new_query))

    def _fetch_html(self, url: str) -> str:
        """Fetch HTML content using simple requests."""
        headers = {
            **HEADERS,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://www.google.com/",
        }
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        return response.text

    def _extract_json_ld_price(self, html: str) -> Optional[float]:
        """Extract lowPrice from JSON-LD data."""
        json_ld = extract_json_ld(html)
        for item in json_ld:
            offers = item.get("offers", {})
            if isinstance(offers, list) and offers:
                offers = offers[0]
            if isinstance(offers, dict):
                low_price = offers.get("lowPrice")
                if low_price:
                    return parse_price(low_price)
        return None

    def _extract_listings_from_html(self, html: str) -> list[ListingInfo]:
        """
        Extract ticket listings from HTML.

        TickPick listing format in HTML:
        Section 413
        Row 13 • 2 Tickets
        Lowest Price
        $180 ea
        """
        soup = BeautifulSoup(html, "html.parser")
        listings = []

        # Look for listing containers - TickPick uses various class patterns
        listing_containers = soup.find_all(attrs={"data-listing-id": True})

        if not listing_containers:
            # Try to find listing cards by structure
            listing_containers = soup.find_all(class_=re.compile(r'listing|ticket-card|row-card', re.I))

        for container in listing_containers:
            listing = self._parse_listing_element(container)
            if listing:
                listings.append(listing)

        # If no structured listings found, try text parsing
        if not listings:
            listings = self._extract_listings_from_text(soup.get_text(separator="\n"))

        listings.sort(key=lambda x: x.price)
        return listings

    def _parse_listing_element(self, element) -> Optional[ListingInfo]:
        """Parse a single listing HTML element."""
        text = element.get_text(separator=" ")

        # Extract price (look for $XXX patterns)
        price_match = re.search(r'\$([0-9,]+(?:\.\d{2})?)\s*(?:ea|each)?', text, re.I)
        if not price_match:
            return None

        price = parse_price(price_match.group(1))
        if not price:
            return None

        # Extract section
        section = ""
        section_match = re.search(r'Section\s+([A-Za-z0-9]+)', text, re.I)
        if section_match:
            section = section_match.group(1)

        # Extract row
        row = ""
        row_match = re.search(r'Row\s+([A-Za-z0-9]+)', text, re.I)
        if row_match:
            row = row_match.group(1)

        # Extract labels
        labels = []
        label_patterns = [
            "Lowest Price",
            "Awesome Deal",
            "Best Deal",
            "Great Deal",
            "Best Value",
        ]
        for label in label_patterns:
            if label.lower() in text.lower():
                labels.append(label)

        # Get listing ID if available
        listing_id = element.get("data-listing-id")

        return ListingInfo(
            price=price,
            section=section,
            row=row,
            labels=labels,
            listing_id=listing_id,
        )

    def _extract_listings_from_text(self, text: str) -> list[ListingInfo]:
        """Fallback text-based extraction for listings."""
        listings = []

        # Pattern: Section XXX ... Row XX ... $XXX
        pattern = re.compile(
            r'Section\s+([A-Za-z0-9]+).*?'
            r'Row\s+([A-Za-z0-9]+).*?'
            r'\$([0-9,]+(?:\.\d{2})?)',
            re.IGNORECASE | re.DOTALL
        )

        for match in pattern.finditer(text):
            section = match.group(1)
            row = match.group(2)
            price = parse_price(match.group(3))

            if price:
                # Check for labels in the matched context
                match_text = match.group(0)
                labels = []
                if "lowest" in match_text.lower():
                    labels.append("Lowest Price")
                if "deal" in match_text.lower():
                    labels.append("Great Deal")

                listings.append(ListingInfo(
                    price=price,
                    section=section,
                    row=row,
                    labels=labels,
                ))

        return listings

    def _extract_listings_from_script(self, html: str) -> list[ListingInfo]:
        """Try to extract listings from embedded JavaScript/JSON data."""
        listings = []

        soup = BeautifulSoup(html, "html.parser")

        # Look for __NEXT_DATA__ or similar embedded data
        for script in soup.find_all("script"):
            if not script.string:
                continue

            # Try to find JSON data in script
            try:
                # Look for listings array in script content
                if "listings" in script.string or "tickets" in script.string:
                    # Try to extract JSON objects
                    json_match = re.search(r'\{[^{}]*"listings"\s*:\s*\[[^\]]+\][^{}]*\}', script.string)
                    if json_match:
                        data = json.loads(json_match.group())
                        for item in data.get("listings", []):
                            price = parse_price(item.get("price") or item.get("total"))
                            if price:
                                listings.append(ListingInfo(
                                    price=price,
                                    section=item.get("section", ""),
                                    row=item.get("row", ""),
                                    listing_id=item.get("id"),
                                ))
            except (json.JSONDecodeError, AttributeError):
                continue

        return listings

    def get_listings(self, url: str, quantity: int = 2) -> list[ListingInfo]:
        """Get all available ticket listings."""
        final_url = self._build_url(url, quantity=quantity)
        html = self._fetch_html(final_url)

        # Try multiple extraction methods
        listings = self._extract_listings_from_html(html)

        if not listings:
            listings = self._extract_listings_from_script(html)

        listings.sort(key=lambda x: x.price)
        return listings

    def get_event_info(self, url: str) -> dict[str, Any]:
        """Get event metadata from JSON-LD."""
        html = self._fetch_html(url)
        json_ld = extract_json_ld(html)
        return extract_event_from_json_ld(json_ld)

    def get_lowest_price(self, url: str, quantity: int = 2) -> ScraperResult:
        """Get the lowest all-in price for an event."""
        final_url = self._build_url(url, quantity=quantity)
        html = self._fetch_html(final_url)

        # Get event info
        json_ld = extract_json_ld(html)
        event_info = extract_event_from_json_ld(json_ld)

        # Get listings
        extraction_method = "unknown"
        listings = self._extract_listings_from_html(html)
        if listings:
            extraction_method = "html_parse"
        else:
            listings = self._extract_listings_from_script(html)
            if listings:
                extraction_method = "script_data"

        # Also get JSON-LD price as fallback
        json_ld_price = self._extract_json_ld_price(html)

        # Find cheapest
        cheapest = listings[0] if listings else None
        lowest_price = cheapest.price if cheapest else json_ld_price

        return ScraperResult(
            source=self.name,
            event_name=event_info.get("name", ""),
            event_date=event_info.get("startDate", ""),
            venue=event_info.get("venue", ""),
            lowest_all_in=lowest_price,
            fees_included=self.all_in_pricing,
            cheapest_listing=cheapest,
            listings_count=len(listings),
            url=final_url,
            extraction_method=extraction_method,
        )
