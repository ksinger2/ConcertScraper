"""
Dice scraper implementation.

Dice features:
- Music-focused platform
- No fees (all-in pricing)
- Clean HTML structure
- Often has API endpoints
"""

import json
import re
from typing import Any, Optional

import requests
from bs4 import BeautifulSoup

from ..base import BaseScraper, ScraperResult, ListingInfo
from ..utils import HEADERS, extract_json_ld, extract_event_from_json_ld, parse_price


class DiceScraper(BaseScraper):
    """Scraper for Dice ticket listings."""

    name = "dice"
    all_in_pricing = True  # Dice has no hidden fees

    def _fetch_html(self, url: str) -> str:
        """Fetch HTML content."""
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        return response.text

    def _extract_listings_from_html(self, html: str) -> list[ListingInfo]:
        """Extract listings from HTML."""
        soup = BeautifulSoup(html, "html.parser")
        listings = []

        # Dice listing patterns - they often have simple ticket tiers
        listing_selectors = [
            '[data-testid*="ticket"]',
            '[class*="TicketType"]',
            '[class*="ticket-tier"]',
            '.ticket-option',
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

        # Dice prices - look for various currency formats
        price_match = re.search(r'[£$€]([0-9,]+(?:\.\d{2})?)', text)
        if not price_match:
            # Try without currency symbol
            price_match = re.search(r'(\d+(?:\.\d{2})?)\s*(?:USD|GBP|EUR)?', text, re.I)

        if not price_match:
            return None

        price = parse_price(price_match.group(1))
        if not price:
            return None

        # Dice often has ticket type names instead of sections
        labels = []

        # Common Dice ticket types
        ticket_types = ["General Admission", "GA", "Standing", "VIP", "Early Entry", "Balcony", "Floor"]
        for ticket_type in ticket_types:
            if ticket_type.lower() in text.lower():
                labels.append(ticket_type)
                break

        if "sold out" in text.lower():
            labels.append("Sold Out")
        if "limited" in text.lower():
            labels.append("Limited")
        if "final" in text.lower():
            labels.append("Final Release")

        # Section might be ticket type for Dice
        section = labels[0] if labels and labels[0] not in ["Sold Out", "Limited", "Final Release"] else ""

        return ListingInfo(
            price=price,
            section=section,
            labels=[l for l in labels if l not in [section]],
        )

    def _extract_from_text(self, text: str) -> list[ListingInfo]:
        """Extract listings from text."""
        listings = []

        # Pattern for price tiers
        patterns = [
            # "General Admission $25" or "GA £20"
            r'(General Admission|GA|Standing|VIP|Floor|Balcony)\s*[£$€]?([0-9,]+(?:\.\d{2})?)',
            # "$25 - General Admission"
            r'[£$€]([0-9,]+(?:\.\d{2})?)\s*[-–]\s*(General Admission|GA|Standing|VIP|Floor|Balcony)',
            # Just prices
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

                if price and price > 5:  # Filter unlikely prices
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
                # Dice often uses __NEXT_DATA__
                if script.get("id") == "__NEXT_DATA__":
                    data = json.loads(script.string)
                    props = data.get("props", {}).get("pageProps", {})

                    event = props.get("event", {})
                    if event:
                        event_info = {
                            "name": event.get("name", ""),
                            "startDate": event.get("date", "") or event.get("startTime", ""),
                            "venue": event.get("venue", {}).get("name", "") if isinstance(event.get("venue"), dict) else event.get("venueName", ""),
                        }

                    # Extract ticket types
                    ticket_types = event.get("ticketTypes", []) or props.get("tickets", [])
                    for ticket in ticket_types:
                        price = parse_price(ticket.get("price") or ticket.get("faceValue"))
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
