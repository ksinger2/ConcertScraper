"""
TickPick scraper implementation.

TickPick features:
- All-in pricing (no hidden fees)
- Playwright-based extraction preferred for accuracy
- JSON-LD lowPrice is NOT used (often stale/inaccurate)
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

    def _fetch_html(self, url: str, use_browser: bool = True) -> str:
        """Fetch HTML content, optionally using headless browser."""
        if use_browser and PLAYWRIGHT_AVAILABLE:
            return self._fetch_with_playwright(url)

        headers = {
            **HEADERS,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://www.google.com/",
        }
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        return response.text

    def _fetch_with_playwright(self, url: str) -> str:
        """Fetch HTML using headless Playwright browser."""
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=HEADERS["User-Agent"],
                viewport={"width": 1920, "height": 1080},
            )
            page = context.new_page()

            page.goto(url, wait_until="domcontentloaded", timeout=60000)

            # Wait for listing elements to load
            try:
                page.wait_for_selector('[class*="ticket"], [class*="listing"], [class*="row-card"], [data-listing-id]', timeout=15000)
            except:
                pass

            page.wait_for_timeout(2000)
            html = page.content()
            browser.close()

            return html

    def _extract_listings_with_playwright(self, url: str) -> list[ListingInfo]:
        """Extract listings using Playwright's JavaScript evaluation."""
        if not PLAYWRIGHT_AVAILABLE:
            return []

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=['--disable-blink-features=AutomationControlled']
            )
            context = browser.new_context(
                user_agent=HEADERS["User-Agent"],
                viewport={"width": 1920, "height": 1080},
            )
            page = context.new_page()
            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")

            page.goto(url, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(3000)

            # Close any modal/popup
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(500)
            except:
                pass

            # Extract listing data via JS - TickPick uses Tailwind CSS so we need text-based extraction
            # Format: "Section XXX\nRow XX • X Tickets\nLabel\n$XXX ea"
            raw_listings = page.evaluate("""
                () => {
                    const listings = [];
                    const seen = new Set();
                    const allElements = document.querySelectorAll('*');

                    allElements.forEach(el => {
                        const text = (el.innerText || '').trim();

                        // Pattern: Should have Section, Row, $ price, and be reasonably short
                        const sectionMatch = text.match(/Section\\s+([A-Z0-9]+)/i);
                        const rowMatch = text.match(/Row\\s+([A-Z0-9]+)/i);
                        const priceMatch = text.match(/\\$([0-9,]+)/);
                        const ticketMatch = text.match(/(\\d+)\\s*Ticket/i);

                        if (sectionMatch && rowMatch && priceMatch && text.length < 300) {
                            const key = sectionMatch[1] + '-' + rowMatch[1] + '-' + priceMatch[1];
                            if (!seen.has(key)) {
                                seen.add(key);

                                const labels = [];
                                const lowerText = text.toLowerCase();
                                if (lowerText.includes('lowest')) labels.push('Lowest Price');
                                if (lowerText.includes('amazing')) labels.push('Amazing Deal');
                                if (lowerText.includes('great deal')) labels.push('Great Deal');
                                if (lowerText.includes('awesome')) labels.push('Awesome Deal');
                                if (lowerText.includes('best deal')) labels.push('Best Deal');
                                if (lowerText.includes('best value')) labels.push('Best Value');

                                listings.push({
                                    section: sectionMatch[1],
                                    row: rowMatch[1],
                                    priceStr: priceMatch[1],
                                    quantity: ticketMatch ? parseInt(ticketMatch[1]) : null,
                                    labels: labels
                                });
                            }
                        }
                    });

                    return listings;
                }
            """)

            browser.close()

            # Convert to ListingInfo
            listings = []
            for item in raw_listings:
                price = parse_price(item.get('priceStr', ''))
                if price:
                    listings.append(ListingInfo(
                        price=price,
                        section=item.get('section', ''),
                        row=item.get('row', ''),
                        labels=item.get('labels', []),
                        quantity=item.get('quantity'),
                    ))

            listings.sort(key=lambda x: x.price)
            return listings

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
            # Try data-ticket-id
            listing_containers = soup.find_all(attrs={"data-ticket-id": True})

        if not listing_containers:
            # Try to find listing cards by class patterns
            listing_containers = soup.find_all(class_=re.compile(r'TicketRow|ListingCard|listing-card|ticket-card|row-card|listing', re.I))

        if not listing_containers:
            # Try common container patterns
            for selector in ['[class*="listing"]', '[class*="ticket-row"]', '[class*="TicketRow"]']:
                listing_containers = soup.select(selector)
                if listing_containers:
                    break

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

        # First, check for __NEXT_DATA__ (Next.js)
        next_data_script = soup.find("script", id="__NEXT_DATA__")
        if next_data_script and next_data_script.string:
            try:
                data = json.loads(next_data_script.string)
                props = data.get("props", {}).get("pageProps", {})

                # Try various paths for listings
                listings_data = None
                for key in ["listings", "tickets", "ticketListings", "inventory"]:
                    if key in props:
                        listings_data = props[key]
                        break

                # Check in dehydratedState (React Query)
                if not listings_data:
                    dehydrated = props.get("dehydratedState", {})
                    for query in dehydrated.get("queries", []):
                        state = query.get("state", {})
                        query_data = state.get("data", {})
                        if isinstance(query_data, dict):
                            for key in ["items", "listings", "tickets"]:
                                if key in query_data:
                                    listings_data = query_data[key]
                                    break
                        elif isinstance(query_data, list):
                            listings_data = query_data
                        if listings_data:
                            break

                if listings_data and isinstance(listings_data, list):
                    for item in listings_data:
                        listing = self._parse_script_listing(item)
                        if listing:
                            listings.append(listing)

            except (json.JSONDecodeError, AttributeError, KeyError):
                pass

        # Fallback: Look for other script patterns
        if not listings:
            for script in soup.find_all("script"):
                if not script.string:
                    continue

                try:
                    # Look for listings array in script content
                    if "listings" in script.string or "tickets" in script.string:
                        # Try multiple JSON patterns
                        patterns = [
                            r'"listings"\s*:\s*(\[[^\]]+\])',
                            r'"tickets"\s*:\s*(\[[^\]]+\])',
                            r'"inventory"\s*:\s*(\[[^\]]+\])',
                        ]

                        for pattern in patterns:
                            match = re.search(pattern, script.string)
                            if match:
                                try:
                                    items = json.loads(match.group(1))
                                    for item in items:
                                        listing = self._parse_script_listing(item)
                                        if listing:
                                            listings.append(listing)
                                    if listings:
                                        break
                                except json.JSONDecodeError:
                                    continue

                except (AttributeError, KeyError):
                    continue

        return listings

    def _parse_script_listing(self, item: dict) -> Optional[ListingInfo]:
        """Parse a listing from script/JSON data."""
        if not isinstance(item, dict):
            return None

        # Try various price field names
        price = None
        for field in ["price", "total", "amount", "displayPrice", "allInPrice", "priceWithFees"]:
            val = item.get(field)
            if val:
                if isinstance(val, dict):
                    price = parse_price(val.get("amount") or val.get("value"))
                elif isinstance(val, (int, float)):
                    price = float(val)
                elif isinstance(val, str):
                    price = parse_price(val)
                if price:
                    break

        if not price:
            return None

        # Get section/row
        section = item.get("section", "") or item.get("sectionName", "")
        row = item.get("row", "") or item.get("rowName", "")

        # Get labels
        labels = []
        for label_field in ["labels", "badges", "tags"]:
            if label_field in item:
                val = item[label_field]
                if isinstance(val, list):
                    labels.extend([str(l) for l in val if l])
                elif isinstance(val, str):
                    labels.append(val)

        return ListingInfo(
            price=price,
            section=str(section),
            row=str(row),
            labels=labels,
            listing_id=item.get("id") or item.get("listingId"),
        )

    def get_listings(self, url: str, quantity: int = 2) -> list[ListingInfo]:
        """Get all available ticket listings."""
        self._wait_for_rate_limit(url)
        final_url = self._build_url(url, quantity=quantity)

        # Try Playwright first
        if PLAYWRIGHT_AVAILABLE:
            listings = self._extract_listings_with_playwright(final_url)
            if listings:
                return listings

        # Fall back to HTML extraction
        html = self._fetch_html(final_url, use_browser=PLAYWRIGHT_AVAILABLE)

        # Try multiple extraction methods
        listings = self._extract_listings_from_html(html)

        if not listings:
            listings = self._extract_listings_from_script(html)

        listings.sort(key=lambda x: x.price)
        return listings

    def get_event_info(self, url: str) -> dict[str, Any]:
        """Get event metadata from JSON-LD."""
        self._wait_for_rate_limit(url)
        html = self._fetch_html(url)
        json_ld = extract_json_ld(html)
        return extract_event_from_json_ld(json_ld)

    def get_lowest_price(self, url: str, quantity: int = 2) -> ScraperResult:
        """Get the lowest all-in price for an event."""
        self._wait_for_rate_limit(url)
        final_url = self._build_url(url, quantity=quantity)

        # Determine extraction method used
        extraction_method = "unknown"
        listings = []

        # Try Playwright first
        if PLAYWRIGHT_AVAILABLE:
            listings = self._extract_listings_with_playwright(final_url)
            if listings:
                extraction_method = "playwright_js"

        # Get HTML for event info and fallback extraction
        html = self._fetch_html(final_url, use_browser=PLAYWRIGHT_AVAILABLE)

        # Fall back to other methods
        if not listings:
            listings = self._extract_listings_from_html(html)
            if listings:
                extraction_method = "html_parse"

        if not listings:
            listings = self._extract_listings_from_script(html)
            if listings:
                extraction_method = "script_data"

        # Get event info
        json_ld = extract_json_ld(html)
        event_info = extract_event_from_json_ld(json_ld)

        # Find cheapest - only use verified listings, not JSON-LD metadata
        # JSON-LD lowPrice is often stale/incorrect and doesn't reflect actual inventory
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
