"""
Resident Advisor scraper implementation.

Resident Advisor features:
- Electronic music focused
- Events and club nights
- Fees vary
- HTML parsing needed
"""

import json
import re
from typing import Any, Optional

import requests
from bs4 import BeautifulSoup

from ..base import BaseScraper, ScraperResult, ListingInfo
from ..utils import HEADERS, extract_json_ld, extract_event_from_json_ld, parse_price


class ResidentAdvisorScraper(BaseScraper):
    """Scraper for Resident Advisor ticket listings."""

    name = "residentadvisor"
    all_in_pricing = False  # Fees vary

    def _fetch_html(self, url: str) -> str:
        """Fetch HTML content."""
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        return response.text

    def _extract_listings_from_html(self, html: str) -> list[ListingInfo]:
        """Extract listings from HTML."""
        soup = BeautifulSoup(html, "html.parser")
        listings = []

        # RA ticket patterns
        listing_selectors = [
            '[data-testid*="ticket"]',
            '[class*="ticket"]',
            '.ticket-type',
            '.price-option',
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

        # Find price - RA uses various currencies
        price_match = re.search(r'[£$€]([0-9,]+(?:\.\d{2})?)', text)
        if not price_match:
            return None

        price = parse_price(price_match.group(1))
        if not price:
            return None

        # Extract ticket type
        labels = []
        section = ""

        ticket_types = [
            "Early Bird", "First Release", "Second Release", "Final Release",
            "Standard", "Regular", "Door", "Advance", "VIP",
        ]
        for ticket_type in ticket_types:
            if ticket_type.lower() in text.lower():
                section = ticket_type
                break

        if "sold out" in text.lower():
            labels.append("Sold Out")
        if "limited" in text.lower():
            labels.append("Limited")

        return ListingInfo(
            price=price,
            section=section,
            labels=labels,
        )

    def _extract_from_text(self, text: str) -> list[ListingInfo]:
        """Extract listings from text."""
        listings = []

        # Pattern for ticket releases
        patterns = [
            r'(Early Bird|First Release|Second Release|Final Release|Standard|Advance|Door)\s*[:\-]?\s*[£$€]([0-9,]+(?:\.\d{2})?)',
            r'[£$€]([0-9,]+(?:\.\d{2})?)\s*[:\-]?\s*(Early Bird|First Release|Second Release|Final Release|Standard|Advance|Door)',
            r'[£$€]([0-9,]+(?:\.\d{2})?)',
        ]

        for pattern in patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                if len(match.groups()) == 2:
                    if match.group(1).replace(",", "").replace(".", "").isdigit():
                        price = parse_price(match.group(1))
                        section = match.group(2)
                    else:
                        section = match.group(1)
                        price = parse_price(match.group(2))
                else:
                    price = parse_price(match.group(1))
                    section = ""

                if price and price > 3:  # Filter unlikely prices
                    listings.append(ListingInfo(
                        price=price,
                        section=section,
                    ))

            if listings:
                break

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
                # RA uses various data embedding methods
                if "__NEXT_DATA__" in str(script) or "eventData" in script.string:

                    if script.get("id") == "__NEXT_DATA__":
                        data = json.loads(script.string)
                        props = data.get("props", {}).get("pageProps", {})
                    else:
                        match = re.search(r'eventData\s*=\s*({.+?});', script.string, re.DOTALL)
                        if match:
                            props = json.loads(match.group(1))
                        else:
                            continue

                    event = props.get("event", {})
                    if event:
                        event_info = {
                            "name": event.get("title", "") or event.get("name", ""),
                            "startDate": event.get("date", "") or event.get("startTime", ""),
                            "venue": event.get("venue", {}).get("name", "") if isinstance(event.get("venue"), dict) else event.get("venueName", ""),
                        }

                    # Extract tickets
                    tickets = props.get("tickets", []) or event.get("tickets", [])
                    for ticket in tickets:
                        price = parse_price(ticket.get("price") or ticket.get("cost"))
                        if price:
                            listings.append(ListingInfo(
                                price=price,
                                section=ticket.get("name", "") or ticket.get("type", ""),
                                listing_id=ticket.get("id"),
                            ))

            except (json.JSONDecodeError, AttributeError, KeyError):
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
