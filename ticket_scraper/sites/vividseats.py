"""
VividSeats scraper implementation.

VividSeats features:
- Secondary market
- Similar structure to StubHub
- May need Playwright for bot protection
- Fees added on top
"""

import json
import re
from typing import Any, Optional
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

import requests
from bs4 import BeautifulSoup

from ..base import BaseScraper, ScraperResult, ListingInfo
from ..utils import HEADERS, extract_json_ld, extract_event_from_json_ld, parse_price

# Optional Playwright support
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


class VividSeatsScraper(BaseScraper):
    """Scraper for VividSeats ticket listings."""

    name = "vividseats"
    all_in_pricing = False  # VividSeats adds fees

    def _build_url(self, url: str, quantity: int = 2) -> str:
        """Build URL with quantity parameter."""
        parsed = urlparse(url)
        params = parse_qs(parsed.query)

        if quantity > 0:
            params["qty"] = [str(quantity)]

        new_query = urlencode(params, doseq=True)
        return urlunparse(parsed._replace(query=new_query))

    def _fetch_html(self, url: str, use_browser: bool = True) -> str:
        """Fetch HTML, optionally using headless browser."""
        if use_browser and PLAYWRIGHT_AVAILABLE:
            return self._fetch_with_playwright(url)

        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        return response.text

    def _fetch_with_playwright(self, url: str) -> str:
        """Fetch HTML using Playwright."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=HEADERS["User-Agent"],
                viewport={"width": 1920, "height": 1080},
            )
            page = context.new_page()

            page.goto(url, wait_until="domcontentloaded", timeout=60000)

            # Wait for listings to load
            try:
                page.wait_for_selector('[data-testid*="listing"], [class*="listing"]', timeout=10000)
            except:
                pass

            page.wait_for_timeout(2000)
            html = page.content()
            browser.close()

            return html

    def _extract_listings_from_html(self, html: str) -> list[ListingInfo]:
        """Extract listings from HTML."""
        soup = BeautifulSoup(html, "html.parser")
        listings = []

        # VividSeats listing patterns
        listing_selectors = [
            '[data-testid*="ticket"]',
            '[class*="listing-row"]',
            '[class*="TicketRow"]',
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
        if "best" in text.lower():
            labels.append("Best Value")
        if "deal" in text.lower():
            labels.append("Great Deal")

        return ListingInfo(
            price=price,
            section=section,
            row=row,
            labels=labels,
        )

    def _extract_from_text(self, text: str) -> list[ListingInfo]:
        """Extract listings from text."""
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
        """Extract data from embedded JavaScript."""
        soup = BeautifulSoup(html, "html.parser")
        event_info = {}
        listings = []

        for script in soup.find_all("script"):
            if not script.string:
                continue

            # Look for embedded JSON data
            try:
                if "window.__INITIAL_STATE__" in script.string:
                    json_match = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.+?});', script.string)
                    if json_match:
                        data = json.loads(json_match.group(1))

                        # Extract event info
                        event = data.get("event", {}) or data.get("production", {})
                        if event:
                            event_info = {
                                "name": event.get("name", ""),
                                "startDate": event.get("date", "") or event.get("eventDate", ""),
                                "venue": event.get("venue", {}).get("name", "") if isinstance(event.get("venue"), dict) else event.get("venueName", ""),
                            }

                        # Extract listings
                        ticket_groups = data.get("ticketGroups", []) or data.get("listings", [])
                        for group in ticket_groups:
                            price = parse_price(group.get("price") or group.get("listPrice"))
                            if price:
                                listings.append(ListingInfo(
                                    price=price,
                                    section=group.get("section", ""),
                                    row=group.get("row", ""),
                                    quantity=group.get("quantity"),
                                    listing_id=group.get("id"),
                                ))

            except (json.JSONDecodeError, AttributeError):
                continue

        return event_info, listings

    def get_listings(self, url: str, quantity: int = 2) -> list[ListingInfo]:
        """Get all available ticket listings."""
        final_url = self._build_url(url, quantity=quantity)
        html = self._fetch_html(final_url)

        _, listings = self._extract_embedded_data(html)

        if not listings:
            listings = self._extract_listings_from_html(html)

        listings.sort(key=lambda x: x.price)
        return listings

    def get_event_info(self, url: str) -> dict[str, Any]:
        """Get event metadata."""
        html = self._fetch_html(url, use_browser=False)

        event_info, _ = self._extract_embedded_data(html)
        if event_info.get("name"):
            return event_info

        json_ld = extract_json_ld(html)
        return extract_event_from_json_ld(json_ld)

    def get_lowest_price(self, url: str, quantity: int = 2) -> ScraperResult:
        """Get the lowest price for an event."""
        final_url = self._build_url(url, quantity=quantity)
        html = self._fetch_html(final_url)

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
            url=final_url,
            extraction_method=extraction_method,
        )
