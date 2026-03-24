"""
SeatGeek scraper implementation.

SeatGeek features:
- Public API available at api.seatgeek.com
- Deal Score (0-10) for value rating
- Fees shown separately
- Good structured data
"""

import json
import re
from typing import Any, Optional
from urllib.parse import urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

from ..base import BaseScraper, ScraperResult, ListingInfo
from ..utils import HEADERS, extract_json_ld, extract_event_from_json_ld, parse_price


class SeatGeekScraper(BaseScraper):
    """Scraper for SeatGeek ticket listings."""

    name = "seatgeek"
    all_in_pricing = False  # SeatGeek shows fees separately

    # SeatGeek public API base
    API_BASE = "https://api.seatgeek.com/2"

    def _extract_event_id(self, url: str) -> Optional[str]:
        """Extract event ID from SeatGeek URL."""
        # URL patterns:
        # /events/artist-at-venue-date-tickets/5123456
        # /artist-tickets/event-name/5123456

        parsed = urlparse(url)
        path_parts = parsed.path.strip("/").split("/")

        # Event ID is typically the last numeric segment
        for part in reversed(path_parts):
            if part.isdigit():
                return part

        return None

    def _fetch_html(self, url: str) -> str:
        """Fetch HTML content."""
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        return response.text

    def _fetch_api_data(self, event_id: str) -> Optional[dict]:
        """Fetch event data from SeatGeek API."""
        try:
            # Public endpoint - may require client_id in production
            api_url = f"{self.API_BASE}/events/{event_id}"
            response = requests.get(api_url, headers=HEADERS, timeout=30)

            if response.status_code == 200:
                return response.json()
        except requests.RequestException:
            pass

        return None

    def _extract_listings_from_html(self, html: str) -> list[ListingInfo]:
        """Extract listings from HTML page."""
        soup = BeautifulSoup(html, "html.parser")
        listings = []

        # Look for listing elements
        listing_elements = soup.find_all(attrs={"data-tid": re.compile(r"listing")})

        if not listing_elements:
            # Try alternative selectors
            listing_elements = soup.find_all(class_=re.compile(r'ListingRow|TicketListing', re.I))

        for element in listing_elements:
            listing = self._parse_listing_element(element)
            if listing:
                listings.append(listing)

        # Fallback to text parsing
        if not listings:
            listings = self._extract_from_text(soup.get_text(separator="\n"))

        listings.sort(key=lambda x: x.price)
        return listings

    def _parse_listing_element(self, element) -> Optional[ListingInfo]:
        """Parse a listing HTML element."""
        text = element.get_text(separator=" ")

        # Find price
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

        # Extract Deal Score if present
        labels = []
        score_match = re.search(r'Deal Score[:\s]*([0-9.]+)', text, re.I)
        if score_match:
            score = float(score_match.group(1))
            if score >= 8:
                labels.append(f"Great Deal (Score: {score})")
            elif score >= 6:
                labels.append(f"Good Deal (Score: {score})")

        return ListingInfo(
            price=price,
            section=section,
            row=row,
            labels=labels,
        )

    def _extract_from_text(self, text: str) -> list[ListingInfo]:
        """Extract listings from page text."""
        listings = []

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
        """Extract event info and listings from embedded JavaScript data."""
        soup = BeautifulSoup(html, "html.parser")
        event_info = {}
        listings = []

        # Look for __NEXT_DATA__ or similar
        for script in soup.find_all("script", id="__NEXT_DATA__"):
            if not script.string:
                continue
            try:
                data = json.loads(script.string)
                props = data.get("props", {}).get("pageProps", {})

                # Extract event info
                event = props.get("event", {})
                if event:
                    event_info = {
                        "name": event.get("title", ""),
                        "startDate": event.get("datetime_utc", ""),
                        "venue": event.get("venue", {}).get("name", ""),
                    }

                # Extract listings
                for listing in props.get("listings", []):
                    price = parse_price(listing.get("price") or listing.get("pf"))
                    if price:
                        labels = []
                        deal_score = listing.get("dq") or listing.get("deal_quality")
                        if deal_score and deal_score >= 0.8:
                            labels.append(f"Great Deal (Score: {deal_score*10:.1f})")

                        listings.append(ListingInfo(
                            price=price,
                            section=listing.get("s", "") or listing.get("section", ""),
                            row=listing.get("r", "") or listing.get("row", ""),
                            labels=labels,
                            listing_id=listing.get("id"),
                        ))
            except json.JSONDecodeError:
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
