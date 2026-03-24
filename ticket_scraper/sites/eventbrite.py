"""
Eventbrite scraper implementation.

Eventbrite features:
- Events platform (not just tickets)
- Public API available
- Fees vary by event
- Good structured data
"""

import json
import re
from typing import Any, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from ..base import BaseScraper, ScraperResult, ListingInfo
from ..utils import HEADERS, extract_json_ld, extract_event_from_json_ld, parse_price


class EventbriteScraper(BaseScraper):
    """Scraper for Eventbrite ticket listings."""

    name = "eventbrite"
    all_in_pricing = False  # Fees vary, usually added

    # Eventbrite API base
    API_BASE = "https://www.eventbriteapi.com/v3"

    def _extract_event_id(self, url: str) -> Optional[str]:
        """Extract event ID from Eventbrite URL."""
        # URL patterns:
        # /e/event-name-tickets-123456789
        # /e/123456789

        parsed = urlparse(url)
        path = parsed.path

        # Look for numeric ID at end of path
        match = re.search(r'-(\d{10,})$', path)
        if match:
            return match.group(1)

        # Try just numeric ending
        match = re.search(r'/(\d{10,})/?$', path)
        if match:
            return match.group(1)

        return None

    def _fetch_html(self, url: str) -> str:
        """Fetch HTML content."""
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        return response.text

    def _extract_listings_from_html(self, html: str) -> list[ListingInfo]:
        """Extract listings from HTML."""
        soup = BeautifulSoup(html, "html.parser")
        listings = []

        # Eventbrite ticket type patterns
        listing_selectors = [
            '[data-testid*="ticket"]',
            '[class*="ticket-type"]',
            '[class*="eds-card"]',
            '.ticket-selector-row',
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

        # Find price
        price_match = re.search(r'\$([0-9,]+(?:\.\d{2})?)', text)
        if not price_match:
            # Check for free
            if "free" in text.lower():
                return ListingInfo(
                    price=0.0,
                    section="Free",
                    labels=["Free"],
                )
            return None

        price = parse_price(price_match.group(1))
        if price is None:
            return None

        # Extract ticket type name
        labels = []
        section = ""

        # Common Eventbrite ticket types
        ticket_types = [
            "General Admission", "GA", "VIP", "Early Bird",
            "Student", "Regular", "Premium", "Basic",
        ]
        for ticket_type in ticket_types:
            if ticket_type.lower() in text.lower():
                section = ticket_type
                break

        if "sold out" in text.lower():
            labels.append("Sold Out")
        if "limited" in text.lower():
            labels.append("Limited")
        if "early bird" in text.lower() and "Early Bird" not in section:
            labels.append("Early Bird")

        return ListingInfo(
            price=price,
            section=section,
            labels=labels,
        )

    def _extract_from_text(self, text: str) -> list[ListingInfo]:
        """Extract listings from text."""
        listings = []

        # Pattern for ticket type + price
        patterns = [
            r'(General Admission|GA|VIP|Early Bird|Regular|Standard)\s*[:\-]?\s*\$([0-9,]+(?:\.\d{2})?)',
            r'\$([0-9,]+(?:\.\d{2})?)\s*[:\-]?\s*(General Admission|GA|VIP|Early Bird|Regular|Standard)',
            r'\$([0-9,]+(?:\.\d{2})?)',
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

                if price is not None and (price == 0 or price > 1):
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
                # Eventbrite embeds event data in various ways
                if "window.__SERVER_DATA__" in script.string or "__REACT_QUERY_STATE__" in script.string:
                    # Try to extract JSON
                    json_patterns = [
                        r'window\.__SERVER_DATA__\s*=\s*({.+?});',
                        r'__REACT_QUERY_STATE__\s*=\s*({.+?});',
                    ]

                    for pattern in json_patterns:
                        match = re.search(pattern, script.string, re.DOTALL)
                        if match:
                            try:
                                data = json.loads(match.group(1))

                                # Extract event
                                event = data.get("event", {}) or data.get("eventDetails", {})
                                if event:
                                    event_info = {
                                        "name": event.get("name", "") or event.get("title", ""),
                                        "startDate": event.get("start", {}).get("utc", "") if isinstance(event.get("start"), dict) else event.get("startDate", ""),
                                        "venue": event.get("venue", {}).get("name", "") if isinstance(event.get("venue"), dict) else event.get("venueName", ""),
                                    }

                                # Extract ticket classes
                                ticket_classes = data.get("ticketClasses", []) or data.get("tickets", [])
                                for ticket in ticket_classes:
                                    cost = ticket.get("cost", {})
                                    if isinstance(cost, dict):
                                        price = parse_price(cost.get("display") or cost.get("value"))
                                    else:
                                        price = parse_price(ticket.get("price"))

                                    if ticket.get("free"):
                                        price = 0.0

                                    if price is not None:
                                        listings.append(ListingInfo(
                                            price=price,
                                            section=ticket.get("name", ""),
                                            listing_id=ticket.get("id"),
                                            labels=["Free"] if price == 0 else [],
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
