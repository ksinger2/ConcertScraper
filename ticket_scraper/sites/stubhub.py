"""
StubHub scraper implementation.

Extraction methods:
1. Playwright JS evaluation to read data-price attributes (most reliable)
2. Next.js __NEXT_DATA__ JSON extraction
3. HTML text parsing fallback
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


class StubHubScraper(BaseScraper):
    """Scraper for StubHub ticket listings."""

    name = "stubhub"
    all_in_pricing = False  # StubHub adds fees on top of listed prices

    def _build_url(self, url: str, sort_by_price: bool = True, quantity: int = 2) -> str:
        """Build URL with sorting and quantity parameters."""
        parsed = urlparse(url)
        params = parse_qs(parsed.query)

        if sort_by_price:
            params["sortBy"] = ["NEWPRICE"]
            params["sortDirection"] = ["0"]

        if quantity > 0:
            params["quantity"] = [str(quantity)]

        new_query = urlencode(params, doseq=True)
        return urlunparse(parsed._replace(query=new_query))

    def _fetch_html(self, url: str, use_browser: bool = True) -> str:
        """Fetch HTML content, optionally using headless browser."""
        if use_browser and PLAYWRIGHT_AVAILABLE:
            return self._fetch_with_playwright(url)

        response = requests.get(url, headers=HEADERS, timeout=30)
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

            try:
                page.wait_for_selector('[data-listing-id]', timeout=15000)
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
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=HEADERS["User-Agent"],
                viewport={"width": 1920, "height": 1080},
            )
            page = context.new_page()

            page.goto(url, wait_until="domcontentloaded", timeout=60000)

            # Wait for page to stabilize
            page.wait_for_timeout(2000)

            # Try to find listings - may need multiple attempts
            listings_found = False
            for attempt in range(3):
                # Check for existing listings
                listing_count = page.locator('[data-listing-id]').count()
                if listing_count > 0:
                    listings_found = True
                    break

                # Try clicking "Show more" button to reveal listings
                try:
                    show_more = page.locator("text=Show more")
                    if show_more.count() > 0:
                        show_more.first.click()
                        page.wait_for_timeout(2000)
                except:
                    pass

                # Scroll to trigger lazy loading
                page.evaluate("window.scrollTo(0, 500)")
                page.wait_for_timeout(1500)

                # Wait for listings to appear
                try:
                    page.wait_for_selector('[data-listing-id]', timeout=5000)
                    listings_found = True
                    break
                except:
                    pass

            if not listings_found:
                browser.close()
                return []

            # Extra wait for all listings to load
            page.wait_for_timeout(1500)

            # Extract listing data via JS
            # data-price may contain base price; also extract visible text price for all-in
            raw_listings = page.evaluate("""
                () => {
                    const elements = document.querySelectorAll('[data-listing-id]');
                    return Array.from(elements).map(el => {
                        const text = el.innerText || '';
                        const lowerText = text.toLowerCase();
                        const sectionMatch = text.match(/Section\\s+([A-Za-z0-9]+)/i);
                        const rowMatch = text.match(/Row\\s+([A-Za-z0-9]+)/i);

                        // Check if this is an add-on (not actual event ticket)
                        const isAddOn = lowerText.includes('add-on') ||
                                       lowerText.includes('addon') ||
                                       lowerText.includes('(add-on)') ||
                                       lowerText.includes('does not include event') ||
                                       lowerText.includes('does not include admission') ||
                                       lowerText.includes('not include admission') ||
                                       lowerText.includes('without admission') ||
                                       lowerText.includes('parking pass') ||
                                       lowerText.includes('parking only') ||
                                       lowerText.includes('vip parking') ||
                                       lowerText.includes('fast lane pass') ||
                                       lowerText.includes('fast lane') ||
                                       lowerText.includes('fast pass') ||
                                       lowerText.includes('premier pass') ||
                                       lowerText.includes('upgrade only') ||
                                       lowerText.includes('lounge access only') ||
                                       lowerText.includes('club access only');

                        let view = null;
                        if (lowerText.includes('limited or obstructed') ||
                            lowerText.includes('obstructed view')) {
                            view = 'Limited/Obstructed view';
                        } else if (lowerText.includes('limited view')) {
                            view = 'Limited view';
                        } else if (lowerText.includes('clear view')) {
                            view = 'Clear view';
                        }

                        const labels = [];
                        if (lowerText.includes('best price')) labels.push('Best price');
                        if (lowerText.includes('hidden gem')) labels.push('Hidden gem');
                        if (lowerText.includes('great deal')) labels.push('Great deal');

                        // Extract price from data-price attribute
                        const dataPrice = el.getAttribute('data-price');

                        // Also extract visible text price - look for "$XXX incl" pattern (all-in price)
                        // or "$X,XXX" pattern as fallback
                        let textPrice = null;
                        const inclMatch = text.match(/\\$([0-9,]+)\\s*incl/i);
                        if (inclMatch) {
                            textPrice = inclMatch[1].replace(/,/g, '');
                        } else {
                            // Fallback: find any price pattern in text
                            const priceMatch = text.match(/\\$([0-9,]+)(?:\\.\\d{2})?/);
                            if (priceMatch) {
                                textPrice = priceMatch[1].replace(/,/g, '');
                            }
                        }

                        return {
                            listingId: el.getAttribute('data-listing-id'),
                            dataPrice: dataPrice,
                            textPrice: textPrice,
                            index: parseInt(el.getAttribute('data-index') || '999'),
                            isSold: el.getAttribute('data-is-sold') === '1',
                            isAddOn: isAddOn,
                            section: sectionMatch ? sectionMatch[1] : '',
                            row: rowMatch ? rowMatch[1] : '',
                            view: view,
                            labels: labels
                        };
                    }).filter(l => !l.isSold && (l.dataPrice || l.textPrice) && !l.isAddOn);
                }
            """)

            browser.close()

            # Convert to ListingInfo
            # Prefer textPrice (visible all-in price) over dataPrice (may be base price)
            listings = []
            for item in raw_listings:
                # Use text price if available (it's what users actually see)
                # Fall back to data-price attribute
                text_price = parse_price(item.get('textPrice', ''))
                data_price = parse_price(item.get('dataPrice', ''))

                # Prefer text price as it's the visible all-in price
                price = text_price if text_price else data_price

                if price:
                    listings.append(ListingInfo(
                        price=price,
                        section=item.get('section', ''),
                        row=item.get('row', ''),
                        labels=item.get('labels', []),
                        view=item.get('view'),
                        listing_id=item.get('listingId'),
                    ))

            listings.sort(key=lambda x: x.price)
            return listings

    def _extract_nextjs_listings(self, html: str) -> list[ListingInfo]:
        """Extract listings from Next.js __NEXT_DATA__."""
        soup = BeautifulSoup(html, "html.parser")
        script = soup.find("script", id="__NEXT_DATA__")

        if not script or not script.string:
            return []

        try:
            data = json.loads(script.string)
        except json.JSONDecodeError:
            return []

        props = data.get("props", {})
        page_props = props.get("pageProps", {})

        listings_data = None

        # Try various paths
        if "listings" in page_props:
            listings_data = page_props["listings"]
        elif "ticketListings" in page_props:
            listings_data = page_props["ticketListings"]

        initial_state = page_props.get("initialState", {})
        if not listings_data and "listings" in initial_state:
            listings_data = initial_state["listings"]

        # Check dehydratedState (React Query)
        dehydrated = page_props.get("dehydratedState", {})
        for query in dehydrated.get("queries", []):
            state = query.get("state", {})
            query_data = state.get("data", {})
            if isinstance(query_data, dict):
                if "items" in query_data:
                    listings_data = query_data["items"]
                    break
                if "listings" in query_data:
                    listings_data = query_data["listings"]
                    break

        if not listings_data or not isinstance(listings_data, list):
            return []

        listings = []
        for item in listings_data:
            parsed = self._parse_nextjs_listing(item)
            if parsed:
                listings.append(parsed)

        listings.sort(key=lambda x: x.price)
        return listings

    def _parse_nextjs_listing(self, listing: dict) -> Optional[ListingInfo]:
        """Parse a single listing from Next.js data."""
        if not isinstance(listing, dict):
            return None

        price = None
        price_fields = ["priceWithFees", "totalPrice", "allInPrice", "displayPrice", "price"]

        for field in price_fields:
            if field in listing:
                val = listing[field]
                if isinstance(val, dict):
                    price = val.get("amount") or val.get("value")
                elif isinstance(val, (int, float)):
                    price = val
                elif isinstance(val, str):
                    price = parse_price(val)
                if price:
                    break

        if not price and "pricing" in listing:
            pricing = listing["pricing"]
            if isinstance(pricing, dict):
                price = pricing.get("total") or pricing.get("allIn") or pricing.get("displayPrice")

        if not price:
            return None

        section = listing.get("section") or listing.get("sectionName") or ""
        row = listing.get("row") or listing.get("rowName") or ""

        seat_info = listing.get("seatInfo", {})
        if isinstance(seat_info, dict):
            section = section or seat_info.get("section", "")
            row = row or seat_info.get("row", "")

        labels = []
        for label_field in ["labels", "badges", "tags", "dealTypes"]:
            if label_field in listing:
                val = listing[label_field]
                if isinstance(val, list):
                    labels.extend([str(l) for l in val if l])
                elif isinstance(val, str):
                    labels.append(val)

        if listing.get("isBestPrice") or listing.get("bestValue"):
            labels.append("Best price")

        return ListingInfo(
            price=float(price),
            section=str(section),
            row=str(row),
            labels=list(set(labels)),
            quantity=listing.get("quantity") or listing.get("availableTickets"),
            listing_id=listing.get("id") or listing.get("listingId"),
        )

    def _extract_html_listings(self, html: str) -> list[ListingInfo]:
        """Fallback extraction using regex on HTML text."""
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(separator=" ")

        listings = []
        pattern = re.compile(
            r'Section\s+([A-Za-z0-9]+).*?'
            r'Row\s+([A-Za-z0-9]+).*?'
            r'\$([0-9,]+(?:\.\d{2})?)\s+incl',
            re.IGNORECASE | re.DOTALL
        )

        for match in pattern.finditer(text):
            section = match.group(1)
            row = match.group(2)
            price_str = match.group(3).replace(",", "")

            match_text = match.group(0)
            labels = []
            for label in ["Best price", "Hidden gem", "Great deal"]:
                if label.lower() in match_text.lower():
                    labels.append(label)

            view = None
            if "limited" in match_text.lower() or "obstructed" in match_text.lower():
                view = "Limited/Obstructed view"

            listings.append(ListingInfo(
                price=float(price_str),
                section=section,
                row=row,
                labels=labels,
                view=view,
            ))

        listings.sort(key=lambda x: x.price)
        return listings

    def get_listings(self, url: str, quantity: int = 2) -> list[ListingInfo]:
        """Get all available ticket listings."""
        self._wait_for_rate_limit(url)
        final_url = self._build_url(url, sort_by_price=True, quantity=quantity)

        # Try Playwright first
        if PLAYWRIGHT_AVAILABLE:
            listings = self._extract_listings_with_playwright(final_url)
            if listings:
                return listings

        # Fall back to HTML extraction
        html = self._fetch_html(final_url, use_browser=PLAYWRIGHT_AVAILABLE)

        listings = self._extract_nextjs_listings(html)
        if listings:
            return listings

        return self._extract_html_listings(html)

    def get_event_info(self, url: str) -> dict[str, Any]:
        """Get event metadata from JSON-LD."""
        self._wait_for_rate_limit(url)
        html = self._fetch_html(url, use_browser=False)
        json_ld = extract_json_ld(html)
        return extract_event_from_json_ld(json_ld)

    def get_lowest_price(self, url: str, quantity: int = 2) -> ScraperResult:
        """Get the lowest all-in price for an event."""
        self._wait_for_rate_limit(url)
        final_url = self._build_url(url, sort_by_price=True, quantity=quantity)

        # Determine extraction method used
        extraction_method = "unknown"
        listings = []

        # Try Playwright first
        if PLAYWRIGHT_AVAILABLE:
            listings = self._extract_listings_with_playwright(final_url)
            if listings:
                extraction_method = "data_attributes"

        # Get HTML for event info and fallback extraction
        html = self._fetch_html(final_url, use_browser=PLAYWRIGHT_AVAILABLE)

        # Fall back to other methods
        if not listings:
            listings = self._extract_nextjs_listings(html)
            if listings:
                extraction_method = "nextjs_data"

        if not listings:
            listings = self._extract_html_listings(html)
            if listings:
                extraction_method = "html_parse"

        # Get event info
        json_ld = extract_json_ld(html)
        event_info = extract_event_from_json_ld(json_ld)

        # Find cheapest
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
