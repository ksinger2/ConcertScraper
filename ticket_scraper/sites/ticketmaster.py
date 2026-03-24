"""
Ticketmaster scraper implementation.

Ticketmaster features:
- Primary and resale tickets
- Discovery API available (may need API key)
- Platinum/dynamic pricing on primary
- Fees added on top
"""

import json
import re
from typing import Any, Optional
from urllib.parse import urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

from ..base import BaseScraper, ScraperResult, ListingInfo
from ..utils import HEADERS, extract_json_ld, extract_event_from_json_ld, parse_price


class TicketmasterScraper(BaseScraper):
    """Scraper for Ticketmaster ticket listings."""

    name = "ticketmaster"
    all_in_pricing = False  # Ticketmaster adds fees

    # Discovery API base (public, but rate limited without key)
    API_BASE = "https://app.ticketmaster.com/discovery/v2"

    def _extract_event_id(self, url: str) -> Optional[str]:
        """Extract event ID from Ticketmaster URL."""
        # URL patterns:
        # /event/artist-venue-tickets/E123456
        # /event/E123456

        parsed = urlparse(url)
        path_parts = parsed.path.strip("/").split("/")

        for part in path_parts:
            # Ticketmaster IDs often start with letters or are alphanumeric
            if re.match(r'^[A-Za-z0-9]{10,}$', part):
                return part

        # Check query params
        params = parse_qs(parsed.query)
        if "id" in params:
            return params["id"][0]

        return None

    def _fetch_html(self, url: str) -> str:
        """Fetch HTML content."""
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        return response.text

    def _extract_listings_from_html(self, html: str) -> list[ListingInfo]:
        """Extract listings from HTML page."""
        soup = BeautifulSoup(html, "html.parser")
        listings = []

        # Ticketmaster uses various listing container patterns
        listing_selectors = [
            '[data-testid*="listing"]',
            '[class*="TicketListing"]',
            '[class*="ticket-card"]',
            '[data-tid*="ticket"]',
        ]

        listing_elements = []
        for selector in listing_selectors:
            elements = soup.select(selector)
            if elements:
                listing_elements.extend(elements)
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
        """Parse a listing HTML element."""
        text = element.get_text(separator=" ")

        # Find price - Ticketmaster often shows ranges or "from $X"
        price_match = re.search(r'\$([0-9,]+(?:\.\d{2})?)', text)
        if not price_match:
            return None

        price = parse_price(price_match.group(1))
        if not price:
            return None

        # Extract section
        section = ""
        section_match = re.search(r'(?:Section|Sec\.?)\s*([A-Za-z0-9]+)', text, re.I)
        if section_match:
            section = section_match.group(1)

        # Extract row
        row = ""
        row_match = re.search(r'Row\s+([A-Za-z0-9]+)', text, re.I)
        if row_match:
            row = row_match.group(1)

        # Check for ticket type labels
        labels = []
        if "resale" in text.lower():
            labels.append("Resale")
        if "verified" in text.lower():
            labels.append("Verified Resale")
        if "platinum" in text.lower():
            labels.append("Platinum")
        if "official" in text.lower():
            labels.append("Official Platinum")

        return ListingInfo(
            price=price,
            section=section,
            row=row,
            labels=labels,
        )

    def _extract_from_text(self, text: str) -> list[ListingInfo]:
        """Extract listings from page text."""
        listings = []

        # Pattern for section/row/price combinations
        pattern = re.compile(
            r'(?:Section|Sec\.?)\s*([A-Za-z0-9]+).*?'
            r'Row\s+([A-Za-z0-9]+).*?'
            r'\$([0-9,]+(?:\.\d{2})?)',
            re.IGNORECASE | re.DOTALL
        )

        for match in pattern.finditer(text):
            price = parse_price(match.group(3))
            if price:
                listings.append(ListingInfo(
                    price=price,
                    section=match.group(1),
                    row=match.group(2),
                ))

        return listings

    def _extract_embedded_data(self, html: str) -> tuple[dict, list[ListingInfo]]:
        """Extract event info and listings from embedded JavaScript."""
        soup = BeautifulSoup(html, "html.parser")
        event_info = {}
        listings = []

        # Look for Next.js data or similar embedded JSON
        for script in soup.find_all("script"):
            if not script.string:
                continue

            script_text = script.string

            # Look for event data patterns
            if "__NEXT_DATA__" in script_text or "window.__data" in script_text:
                try:
                    # Try to extract JSON
                    if script.get("id") == "__NEXT_DATA__":
                        data = json.loads(script_text)
                    else:
                        json_match = re.search(r'window\.__data\s*=\s*({.+?});', script_text)
                        if json_match:
                            data = json.loads(json_match.group(1))
                        else:
                            continue

                    # Extract from various paths
                    props = data.get("props", {}).get("pageProps", {})

                    event = props.get("event", {}) or props.get("eventDetail", {})
                    if event:
                        event_info = {
                            "name": event.get("name", ""),
                            "startDate": event.get("dates", {}).get("start", {}).get("dateTime", ""),
                            "venue": event.get("_embedded", {}).get("venues", [{}])[0].get("name", ""),
                        }

                    # Extract offers/tickets
                    offers = props.get("offers", []) or props.get("tickets", [])
                    for offer in offers:
                        price = parse_price(offer.get("price") or offer.get("min"))
                        if price:
                            listings.append(ListingInfo(
                                price=price,
                                section=offer.get("section", ""),
                                row=offer.get("row", ""),
                                listing_id=offer.get("id"),
                            ))

                except (json.JSONDecodeError, AttributeError, KeyError):
                    continue

        return event_info, listings

    def get_listings(self, url: str, quantity: int = 2) -> list[ListingInfo]:
        """Get all available ticket listings."""
        html = self._fetch_html(url)

        # Try embedded data first
        _, listings = self._extract_embedded_data(html)

        if not listings:
            listings = self._extract_listings_from_html(html)

        listings.sort(key=lambda x: x.price)
        return listings

    def get_event_info(self, url: str) -> dict[str, Any]:
        """Get event metadata."""
        html = self._fetch_html(url)

        # Try embedded data
        event_info, _ = self._extract_embedded_data(html)
        if event_info.get("name"):
            return event_info

        # Fall back to JSON-LD
        json_ld = extract_json_ld(html)
        return extract_event_from_json_ld(json_ld)

    def get_lowest_price(self, url: str, quantity: int = 2) -> ScraperResult:
        """Get the lowest price for an event."""
        html = self._fetch_html(url)

        # Try embedded data first
        event_info, listings = self._extract_embedded_data(html)
        extraction_method = "embedded_data" if listings else "unknown"

        if not listings:
            listings = self._extract_listings_from_html(html)
            extraction_method = "html_parse" if listings else "unknown"

        # Get event info if not from embedded data
        if not event_info.get("name"):
            json_ld = extract_json_ld(html)
            event_info = extract_event_from_json_ld(json_ld)

        # Find cheapest
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
