#!/usr/bin/env python3
"""
StubHub Scraper

Extracts event data and ticket listings from StubHub pages.
- JSON-LD for event metadata
- Next.js data extraction for reliable all-in prices
- HTML parsing fallback for actual all-in prices (with fees included)
- Supports sorting by cheapest price via URL parameter
- Configurable ticket quantity (affects available listings)

Usage:
    python stubhub_scraper.py <url> [--prices] [--quantity 2] [--output json]
"""

import argparse
import csv
import json
import re
import sys
from typing import Any, Optional, List, Dict
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

import requests
from bs4 import BeautifulSoup
from rich.console import Console
from rich.table import Table

# Optional: Playwright for bypassing bot detection
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


# Default fields for event info
DEFAULT_EVENT_FIELDS = ["name", "startDate", "venue", "city", "state", "lowPrice", "currency", "eventUrl"]

# User-Agent to avoid being blocked
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def extract_nextjs_data(html: str) -> Optional[Dict[str, Any]]:
    """
    Extract listing data from Next.js __NEXT_DATA__ script tag.

    This is the most reliable method as it contains structured JSON data
    with full listing information including prices with fees.

    Returns dict with 'listings' array and metadata, or None if not found.
    """
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")

    if not script or not script.string:
        return None

    try:
        data = json.loads(script.string)
    except json.JSONDecodeError:
        return None

    # Navigate through Next.js structure to find listing data
    # Common paths: props.pageProps.listings, props.pageProps.initialState.listings
    props = data.get("props", {})
    page_props = props.get("pageProps", {})

    result = {
        "listings": [],
        "price_range": {},
        "event_info": {},
        "raw_data": None,
    }

    # Try to find listings in various locations
    listings_data = None

    # Path 1: Direct listings array
    if "listings" in page_props:
        listings_data = page_props["listings"]

    # Path 2: Inside initialState
    initial_state = page_props.get("initialState", {})
    if not listings_data and "listings" in initial_state:
        listings_data = initial_state["listings"]

    # Path 3: Inside ticketListings
    if not listings_data and "ticketListings" in page_props:
        listings_data = page_props["ticketListings"]

    # Path 4: Search in dehydratedState (React Query)
    dehydrated = page_props.get("dehydratedState", {})
    queries = dehydrated.get("queries", [])
    for query in queries:
        state = query.get("state", {})
        query_data = state.get("data", {})
        if isinstance(query_data, dict):
            if "items" in query_data:
                listings_data = query_data["items"]
                break
            if "listings" in query_data:
                listings_data = query_data["listings"]
                break

    # Parse listings into standardized format
    if listings_data and isinstance(listings_data, list):
        for listing in listings_data:
            parsed = _parse_nextjs_listing(listing)
            if parsed:
                result["listings"].append(parsed)

    # Try to extract price range from page props
    if "priceRange" in page_props:
        pr = page_props["priceRange"]
        result["price_range"] = {
            "low": pr.get("min") or pr.get("low"),
            "high": pr.get("max") or pr.get("high"),
        }

    # Store raw for debugging if needed
    result["raw_data"] = page_props

    return result if result["listings"] or result["price_range"] else None


def _parse_nextjs_listing(listing: dict) -> Optional[Dict[str, Any]]:
    """Parse a single listing from Next.js data into standardized format."""
    if not isinstance(listing, dict):
        return None

    # Extract price - look for all-in/total price first
    price = None

    # Common price field names in StubHub's data
    price_fields = [
        "priceWithFees",
        "totalPrice",
        "allInPrice",
        "displayPrice",
        "price",
        "listingPrice",
    ]

    for field in price_fields:
        if field in listing:
            val = listing[field]
            if isinstance(val, dict):
                price = val.get("amount") or val.get("value")
            elif isinstance(val, (int, float)):
                price = val
            elif isinstance(val, str):
                # Parse string like "$198.00"
                match = re.search(r'[\d,]+\.?\d*', val.replace(",", ""))
                if match:
                    price = float(match.group())
            if price:
                break

    # Also check nested pricing object
    if not price and "pricing" in listing:
        pricing = listing["pricing"]
        if isinstance(pricing, dict):
            price = pricing.get("total") or pricing.get("allIn") or pricing.get("displayPrice")

    if not price:
        return None

    # Extract section and row
    section = listing.get("section") or listing.get("sectionName") or ""
    row = listing.get("row") or listing.get("rowName") or ""

    # Handle nested seatInfo
    seat_info = listing.get("seatInfo", {})
    if isinstance(seat_info, dict):
        section = section or seat_info.get("section", "")
        row = row or seat_info.get("row", "")

    # Extract labels/badges
    labels = []
    for label_field in ["labels", "badges", "tags", "dealTypes"]:
        if label_field in listing:
            val = listing[label_field]
            if isinstance(val, list):
                labels.extend([str(l) for l in val if l])
            elif isinstance(val, str):
                labels.append(val)

    # Check for specific boolean flags
    if listing.get("isBestPrice") or listing.get("bestValue"):
        labels.append("Best price")
    if listing.get("isHiddenGem"):
        labels.append("Hidden gem")
    if listing.get("isGreatDeal"):
        labels.append("Great deal")

    return {
        "price": float(price) if price else None,
        "section": str(section),
        "row": str(row),
        "labels": list(set(labels)),
        "quantity": listing.get("quantity") or listing.get("availableTickets"),
        "listing_id": listing.get("id") or listing.get("listingId"),
    }


def fetch_html(url: str, use_browser: bool = True) -> str:
    """
    Fetch HTML content from a URL.

    Args:
        url: The URL to fetch
        use_browser: If True and Playwright is available, use headless browser
                     to bypass bot detection. Falls back to requests if False
                     or Playwright unavailable.

    Returns:
        HTML content as string
    """
    if use_browser and PLAYWRIGHT_AVAILABLE:
        return _fetch_with_playwright(url)

    # Fallback to simple requests (may fail on bot-protected sites)
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.text


def _fetch_with_playwright(url: str) -> str:
    """
    Fetch HTML using headless Playwright browser.
    Bypasses JavaScript challenges and bot detection.
    """
    with sync_playwright() as p:
        # Launch headless Chromium
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=HEADERS["User-Agent"],
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()

        # Navigate and wait for DOM to load
        page.goto(url, wait_until="domcontentloaded", timeout=60000)

        # Wait for listing elements with data-listing-id (the reliable selector)
        try:
            page.wait_for_selector('[data-listing-id]', timeout=15000)
        except:
            pass  # Continue even if selector not found

        # Additional wait for all listings to render
        page.wait_for_timeout(2000)

        html = page.content()
        browser.close()

        return html


def _extract_listings_with_playwright(url: str) -> List[Dict[str, Any]]:
    """
    Extract listings using Playwright's JavaScript evaluation.
    This reads data-price attributes directly from DOM elements,
    which is more reliable than parsing page text.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=HEADERS["User-Agent"],
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()

        page.goto(url, wait_until="domcontentloaded", timeout=60000)

        # Wait for listing elements
        try:
            page.wait_for_selector('[data-listing-id]', timeout=15000)
        except:
            browser.close()
            return []

        # Allow time for all listings to render
        page.wait_for_timeout(2000)

        # Extract listing data using JavaScript evaluation
        listings = page.evaluate("""
            () => {
                const elements = document.querySelectorAll('[data-listing-id]');
                return Array.from(elements).map(el => {
                    const text = el.innerText || '';

                    // Extract section and row from text
                    const sectionMatch = text.match(/Section\\s+([A-Za-z0-9]+)/i);
                    const rowMatch = text.match(/Row\\s+([A-Za-z0-9]+)/i);

                    // Check for view quality
                    let view = null;
                    if (text.toLowerCase().includes('limited or obstructed') ||
                        text.toLowerCase().includes('obstructed view')) {
                        view = 'Limited/Obstructed view';
                    } else if (text.toLowerCase().includes('limited view')) {
                        view = 'Limited view';
                    } else if (text.toLowerCase().includes('clear view')) {
                        view = 'Clear view';
                    } else if (text.toLowerCase().includes('partial view')) {
                        view = 'Partial view';
                    }

                    // Check for labels
                    const labels = [];
                    if (text.toLowerCase().includes('best price')) labels.push('Best price');
                    if (text.toLowerCase().includes('hidden gem')) labels.push('Hidden gem');
                    if (text.toLowerCase().includes('great deal')) labels.push('Great deal');

                    return {
                        listingId: el.getAttribute('data-listing-id'),
                        priceStr: el.getAttribute('data-price'),
                        index: parseInt(el.getAttribute('data-index') || '999'),
                        isSold: el.getAttribute('data-is-sold') === '1',
                        section: sectionMatch ? sectionMatch[1] : '',
                        row: rowMatch ? rowMatch[1] : '',
                        view: view,
                        labels: labels
                    };
                }).filter(l => !l.isSold && l.priceStr);
            }
        """)

        browser.close()

        # Parse price strings to floats and format results
        result = []
        for listing in listings:
            price_str = listing.get('priceStr', '')
            price = None
            if price_str:
                # Remove $ and commas, convert to float
                cleaned = re.sub(r'[^\d.]', '', price_str)
                if cleaned:
                    price = float(cleaned)

            result.append({
                "price": price,
                "section": listing.get('section', ''),
                "row": listing.get('row', ''),
                "labels": listing.get('labels', []),
                "view": listing.get('view'),
                "listing_id": listing.get('listingId'),
                "is_cheapest": False,
            })

        # Sort by price and mark cheapest
        result.sort(key=lambda x: x.get('price') or float('inf'))
        if result:
            result[0]['is_cheapest'] = True

        return result


def build_url(url: str, sort_by_price: bool = True, quantity: int = 1) -> str:
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


def extract_json_ld(html: str) -> list[dict[str, Any]]:
    """Parse HTML and extract all JSON-LD script contents."""
    soup = BeautifulSoup(html, "html.parser")
    json_ld_scripts = soup.find_all("script", type="application/ld+json")

    results = []
    for script in json_ld_scripts:
        try:
            data = json.loads(script.string)
            if isinstance(data, list):
                results.extend(data)
            else:
                results.append(data)
        except (json.JSONDecodeError, TypeError):
            continue

    return results


def extract_price_range(html: str) -> dict[str, Any]:
    """
    Extract the price range indicator from the page (shows true all-in prices).

    Improved approach:
    1. First try to find price range in specific header/filter area
    2. Look for contextual clues like "Price Range" or filter controls
    3. Fall back to regex patterns on full HTML
    """
    soup = BeautifulSoup(html, "html.parser")

    # Strategy 1: Look for price range near filter/header elements
    # StubHub often has price range in a specific filter area
    price_range_containers = []

    # Look for elements with price-range-like classes or data attributes
    for selector in [
        '[data-testid*="price"]',
        '[class*="priceRange"]',
        '[class*="price-range"]',
        '[class*="PriceRange"]',
        '[aria-label*="price"]',
    ]:
        price_range_containers.extend(soup.select(selector))

    # Also look for text containing "Price Range" or similar
    for text_match in soup.find_all(string=re.compile(r'Price\s*Range', re.IGNORECASE)):
        parent = text_match.find_parent()
        if parent:
            price_range_containers.append(parent)

    # Extract prices from containers
    for container in price_range_containers:
        text = container.get_text()
        result = _extract_price_range_from_text(text)
        if result:
            return result

    # Strategy 2: Look in header area (first 20% of page)
    # Split HTML roughly and search header portion more carefully
    header_portion = html[:len(html) // 5]
    result = _extract_price_range_from_text(header_portion)
    if result:
        return result

    # Strategy 3: Fall back to full page regex (original approach)
    return _extract_price_range_from_text(html)


def _extract_price_range_from_text(text: str) -> dict[str, Any]:
    """Extract price range from text using regex patterns."""
    patterns = [
        # $198 - $1,536+ (standard range with separator)
        r'\$([0-9,]+(?:\.\d{2})?)\s*[-–—]\s*\$([0-9,]+(?:\.\d{2})?)\+?',
        # $198$1,536+ (concatenated, no separator)
        r'\$([0-9,]+(?:\.\d{2})?)\$([0-9,]+(?:\.\d{2})?)\+?',
        # "from $198 to $1,536"
        r'from\s+\$([0-9,]+(?:\.\d{2})?)\s+to\s+\$([0-9,]+(?:\.\d{2})?)',
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            low = match.group(1).replace(",", "")
            high = match.group(2).replace(",", "")
            low_val = float(low)
            high_val = float(high)
            # Sanity check: low should be less than high
            if low_val <= high_val:
                return {
                    "low": low_val,
                    "high": high_val,
                }

    return {}


def extract_listings(html: str, url: str = None) -> List[Dict[str, Any]]:
    """
    Extract server-rendered ticket listings from HTML.

    Approach:
    1. Try __NEXT_DATA__ first (most reliable if present)
    2. Fall back to regex-based text extraction

    Note: For best results, use extract_listings_from_url() which uses
    Playwright's JS evaluation to read data-price attributes directly.

    Returns list of dicts: {price, section, row, labels, view, is_cheapest}
    """
    # Strategy 1: Try Next.js data extraction (most reliable if present)
    nextjs_data = extract_nextjs_data(html)
    if nextjs_data and nextjs_data.get("listings"):
        listings = nextjs_data["listings"]
        # Mark first one as cheapest (assuming sorted by price)
        if listings:
            listings[0]["is_cheapest"] = True
        return listings

    # Strategy 2: Use regex-based extraction on page text
    return _extract_listings_fallback(html)


def extract_listings_from_url(url: str) -> List[Dict[str, Any]]:
    """
    Extract listings directly from URL using Playwright JS evaluation.
    This is the most reliable method as it reads data-price attributes
    directly from DOM elements.

    Returns list of dicts: {price, section, row, labels, view, is_cheapest}
    """
    if not PLAYWRIGHT_AVAILABLE:
        raise RuntimeError("Playwright is required. Install with: pip install playwright && playwright install chromium")

    return _extract_listings_with_playwright(url)


def _parse_html_listing_card(card) -> Optional[Dict[str, Any]]:
    """Parse a single listing card HTML element."""
    text = card.get_text(separator=" ")

    # Extract price
    price_match = re.search(r'\$([0-9,]+(?:\.\d{2})?)', text)
    if not price_match:
        return None

    price = float(price_match.group(1).replace(",", ""))

    # Extract section
    section = ""
    section_match = re.search(r'Section\s+([A-Za-z0-9]+)', text, re.IGNORECASE)
    if section_match:
        section = section_match.group(1)

    # Extract row
    row = ""
    row_match = re.search(r'Row\s+([A-Za-z0-9]+)', text, re.IGNORECASE)
    if row_match:
        row = row_match.group(1)

    # Extract labels
    labels = []
    label_patterns = [
        r'(Best price)',
        r'(Hidden gem)',
        r'(Fan favorite)',
        r'(Great deal)',
        r'(Best value)',
    ]
    for pattern in label_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            labels.append(re.search(pattern, text, re.IGNORECASE).group(1))

    return {
        "price": price,
        "section": section,
        "row": row,
        "labels": labels,
        "is_cheapest": False,
    }


def _extract_listings_fallback(html: str) -> List[Dict[str, Any]]:
    """
    Fallback extraction using regex patterns on full HTML.
    Looks for listing patterns: Section + Row + ... + price "incl. fees"
    """
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator=" ")

    listings = []

    # Pattern to find actual listings: Section/Row followed by price with "incl. fees"
    # Example: "Section 408 Row 27 2 tickets together Clear view Best price $648 incl. fees"
    listing_pattern = re.compile(
        r'Section\s+([A-Za-z0-9]+).*?'
        r'Row\s+([A-Za-z0-9]+).*?'
        r'\$([0-9,]+(?:\.\d{2})?)\s+incl',
        re.IGNORECASE | re.DOTALL
    )

    for match in listing_pattern.finditer(text):
        section = match.group(1)
        row = match.group(2)
        price_str = match.group(3).replace(",", "")

        # Get context for labels and view info (between Section and price)
        match_text = match.group(0)

        # Check for deal labels
        labels = []
        for label in ["Best price", "Hidden gem", "Fan favorite", "Great deal", "Best value"]:
            if label.lower() in match_text.lower():
                labels.append(label)

        # Check for view quality indicators
        view_info = None
        view_patterns = [
            (r'limited\s+or\s+obstructed\s+view', "Limited/Obstructed view"),
            (r'obstructed\s+view', "Obstructed view"),
            (r'limited\s+view', "Limited view"),
            (r'partial\s+view', "Partial view"),
            (r'restricted\s+view', "Restricted view"),
            (r'side\s+view', "Side view"),
            (r'behind\s+stage', "Behind stage"),
            (r'clear\s+view', "Clear view"),
        ]
        for pattern, view_label in view_patterns:
            if re.search(pattern, match_text, re.IGNORECASE):
                view_info = view_label
                break

        listings.append({
            "price": float(price_str),
            "section": section,
            "row": row,
            "labels": labels,
            "view": view_info,
            "is_cheapest": False,
        })


    # Mark first as cheapest after sorting
    if listings:
        listings.sort(key=lambda x: x["price"])
        listings[0]["is_cheapest"] = True

    return listings


def flatten_event(event: dict[str, Any]) -> dict[str, Any]:
    """Flatten a JSON-LD event object into a simple dict with target fields."""
    flattened = {}

    # Direct fields
    flattened["name"] = event.get("name", "")
    flattened["startDate"] = event.get("startDate", "")
    flattened["eventUrl"] = event.get("url", "")

    # Location fields (nested)
    location = event.get("location", {})
    if isinstance(location, dict):
        flattened["venue"] = location.get("name", "")
        address = location.get("address", {})
        if isinstance(address, dict):
            flattened["city"] = address.get("addressLocality", "")
            flattened["state"] = address.get("addressRegion", "")
        else:
            flattened["city"] = ""
            flattened["state"] = ""
    else:
        flattened["venue"] = ""
        flattened["city"] = ""
        flattened["state"] = ""

    # Offers fields (nested, may be list or dict)
    offers = event.get("offers", {})
    if isinstance(offers, list) and offers:
        offers = offers[0]
    if isinstance(offers, dict):
        flattened["lowPrice"] = offers.get("lowPrice", offers.get("price", ""))
        flattened["currency"] = offers.get("priceCurrency", "")
    else:
        flattened["lowPrice"] = ""
        flattened["currency"] = ""

    return flattened


def extract_events(json_ld_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter for event types and flatten their fields."""
    events = []
    event_types = {"MusicEvent", "Event", "SportsEvent", "TheaterEvent", "Festival"}

    for item in json_ld_data:
        item_type = item.get("@type", "")
        if isinstance(item_type, list):
            types = set(item_type)
        else:
            types = {item_type}

        if types & event_types:
            events.append(flatten_event(item))

    return events


def filter_fields(events: list[dict[str, Any]], fields: list[str]) -> list[dict[str, Any]]:
    """Filter events to only include specified fields."""
    return [{k: v for k, v in event.items() if k in fields} for event in events]


def display_table(events: list[dict[str, Any]], fields: list[str]) -> None:
    """Display events as a Rich terminal table."""
    console = Console()

    if not events:
        console.print("[yellow]No events found.[/yellow]")
        return

    table = Table(title="StubHub Events", show_header=True, header_style="bold cyan")

    for field in fields:
        table.add_column(field, style="white", overflow="fold")

    for event in events:
        row = [str(event.get(field, "")) for field in fields]
        table.add_row(*row)

    console.print(table)
    console.print(f"\n[green]Total events: {len(events)}[/green]")


def display_price_summary(
    price_range: dict,
    listings: List[Dict],
    json_ld_low: str,
    quantity: int,
    extraction_method: str = "unknown"
) -> None:
    """Display a summary of listings with price comparison."""
    console = Console()

    table = Table(title=f"Ticket Prices (Quantity: {quantity})", show_header=True, header_style="bold cyan")
    table.add_column("Metric", style="bold white", width=25)
    table.add_column("Value", style="white")
    table.add_column("Notes", style="dim")

    # JSON-LD price (pre-fee)
    table.add_row(
        "JSON-LD lowPrice",
        f"${json_ld_low}" if json_ld_low else "N/A",
        "Pre-fee, NOT what you pay"
    )

    # Find cheapest listing
    cheapest = None
    if listings:
        sorted_listings = sorted(listings, key=lambda x: x.get("price") or float("inf"))
        if sorted_listings and sorted_listings[0].get("price"):
            cheapest = sorted_listings[0]

    # All-in price from cheapest listing or price range
    if cheapest:
        method_note = f"[green]✓ Via {extraction_method}[/green]"
        table.add_row(
            "Lowest All-In Price",
            f"${cheapest['price']:.2f}",
            method_note
        )
        if cheapest.get("section") or cheapest.get("row"):
            location = []
            if cheapest.get("section"):
                location.append(f"Section {cheapest['section']}")
            if cheapest.get("row"):
                location.append(f"Row {cheapest['row']}")
            table.add_row(
                "Cheapest Ticket Location",
                ", ".join(location),
                ""
            )
    elif price_range.get("low"):
        table.add_row(
            "Lowest All-In Price",
            f"${price_range['low']:.2f}",
            "[yellow]From price range (less reliable)[/yellow]"
        )

    if price_range.get("high"):
        table.add_row(
            "Highest Price",
            f"${price_range['high']:.2f}",
            ""
        )

    # Number of listings found
    if listings:
        table.add_row(
            "Listings Parsed",
            str(len(listings)),
            f"Method: {extraction_method}"
        )

    console.print(table)

    # Labels found
    all_labels = set()
    for listing in listings:
        for label in listing.get("labels", []):
            all_labels.add(label)
    if all_labels:
        console.print(f"\n[cyan]Labels found:[/cyan] {', '.join(all_labels)}")

    # Show first few sections
    sections = [l.get("section") for l in listings if l.get("section")][:5]
    if sections:
        console.print(f"[cyan]Sections:[/cyan] {', '.join(sections)}")


def display_comparison(events: list[dict[str, Any]]) -> None:
    """Display a side-by-side comparison table for verification."""
    console = Console()

    if not events:
        console.print("[yellow]No events found for comparison.[/yellow]")
        return

    event = events[0]

    table = Table(title="Event Data Comparison", show_header=True, header_style="bold cyan")
    table.add_column("Field", style="bold white", width=15)
    table.add_column("Scraped Value", style="white", overflow="fold")
    table.add_column("Status", style="green", justify="center", width=8)

    for field, value in event.items():
        status = "✓" if value else "—"
        status_style = "green" if value else "yellow"
        table.add_row(field, str(value), f"[{status_style}]{status}[/{status_style}]")

    console.print(table)

    if len(events) > 1:
        console.print(f"\n[dim]Showing 1 of {len(events)} events. Use table output to see all.[/dim]")


def display_json(events: list[dict[str, Any]]) -> None:
    """Display events as formatted JSON."""
    print(json.dumps(events, indent=2))


def save_csv(events: list[dict[str, Any]], path: str, fields: list[str]) -> None:
    """Save events to a CSV file."""
    if not events:
        print("No events to save.")
        return

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(events)

    print(f"Saved {len(events)} events to {path}")


def get_lowest_price(url: str, quantity: int = 1, use_browser: bool = True) -> dict[str, Any]:
    """
    Convenience function to get the lowest all-in price for an event.
    Returns a dict with price info - useful for Discord bot integration.

    Args:
        url: StubHub event URL
        quantity: Number of tickets
        use_browser: Use headless browser to bypass bot detection (default True)

    Returns structured data with:
    - lowest_all_in: The real price with fees
    - json_ld_price: Pre-fee price (for comparison)
    - cheapest_listing: Full details of the cheapest ticket
    - extraction_method: How the data was extracted (for confidence)
    """
    final_url = build_url(url, sort_by_price=True, quantity=quantity)

    # Use Playwright JS evaluation for most reliable extraction
    extraction_method = "unknown"
    listings = []

    if use_browser and PLAYWRIGHT_AVAILABLE:
        try:
            listings = _extract_listings_with_playwright(final_url)
            if listings:
                extraction_method = "data_attributes"
        except Exception:
            pass

    # Fetch HTML for event metadata (JSON-LD)
    html = fetch_html(final_url, use_browser=use_browser)

    json_ld_data = extract_json_ld(html)
    events = extract_events(json_ld_data)
    event_info = events[0] if events else {}

    # Fall back to HTML parsing if Playwright extraction failed
    if not listings:
        nextjs_data = extract_nextjs_data(html)
        if nextjs_data and nextjs_data.get("listings"):
            extraction_method = "nextjs_data"
            listings = nextjs_data.get("listings", [])
        else:
            listings = extract_listings(html)
            if listings:
                extraction_method = "html_parse"

    # Get price range from HTML
    price_range = extract_price_range(html)

    # Determine lowest all-in price
    lowest_all_in = None
    cheapest_listing = None

    if listings:
        # Sort by price to ensure we have the cheapest
        sorted_listings = sorted(listings, key=lambda x: x.get("price") or float("inf"))
        if sorted_listings and sorted_listings[0].get("price"):
            cheapest_listing = sorted_listings[0]
            lowest_all_in = cheapest_listing["price"]

    # Fall back to price range if no listings
    if lowest_all_in is None and price_range.get("low"):
        lowest_all_in = price_range["low"]

    # Build result
    result = {
        "event_name": event_info.get("name", ""),
        "event_date": event_info.get("startDate", ""),
        "venue": event_info.get("venue", ""),
        "lowest_all_in": lowest_all_in,
        "json_ld_price": _parse_price(event_info.get("lowPrice", "")),
        "price_range": {
            "low": price_range.get("low"),
            "high": price_range.get("high"),
        } if price_range else None,
        "cheapest_listing": {
            "price": cheapest_listing["price"],
            "section": cheapest_listing.get("section", ""),
            "row": cheapest_listing.get("row", ""),
            "labels": cheapest_listing.get("labels", []),
            "view": cheapest_listing.get("view"),
        } if cheapest_listing else None,
        "extraction_method": extraction_method,
        "quantity": quantity,
        "url": final_url,
    }

    return result


def _parse_price(price_value) -> Optional[float]:
    """Parse a price value that might be string, int, or float."""
    if price_value is None or price_value == "":
        return None
    if isinstance(price_value, (int, float)):
        return float(price_value)
    if isinstance(price_value, str):
        # Remove currency symbols and commas
        cleaned = re.sub(r'[^\d.]', '', price_value)
        if cleaned:
            return float(cleaned)
    return None


def main() -> None:
    """Main entry point with CLI argument parsing."""
    parser = argparse.ArgumentParser(
        description="Scrape event data and ticket prices from StubHub",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Get lowest all-in prices for an event (2 tickets)
  python stubhub_scraper.py https://www.stubhub.com/event/123456 --prices --quantity 2

  # Get single-ticket prices
  python stubhub_scraper.py https://www.stubhub.com/event/123456 --prices --quantity 1

  # Scrape event metadata from performer page
  python stubhub_scraper.py https://www.stubhub.com/jay-z-tickets/performer/9912

  # Output as JSON (useful for bot integration)
  python stubhub_scraper.py <url> --prices --output json

  # Save to CSV
  python stubhub_scraper.py <url> --output csv --save events.csv
        """,
    )

    parser.add_argument("url", help="StubHub URL to scrape")
    parser.add_argument(
        "--fields",
        type=lambda s: s.split(","),
        default=DEFAULT_EVENT_FIELDS,
        help=f"Comma-separated list of fields (default: {','.join(DEFAULT_EVENT_FIELDS)})",
    )
    parser.add_argument(
        "--output",
        choices=["table", "json", "csv"],
        default="table",
        help="Output format (default: table)",
    )
    parser.add_argument(
        "--save",
        metavar="FILE",
        help="Save output to file (for csv output)",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Show detailed comparison table for first event",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Show raw JSON-LD data without processing",
    )
    parser.add_argument(
        "--prices",
        action="store_true",
        help="Focus on ticket prices - shows lowest all-in price (with fees)",
    )
    parser.add_argument(
        "--quantity", "-q",
        type=int,
        default=2,
        help="Number of tickets (affects which listings are shown, default: 2)",
    )
    parser.add_argument(
        "--no-sort",
        action="store_true",
        help="Don't sort by lowest price",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Don't use headless browser (faster but may fail on bot-protected sites)",
    )

    args = parser.parse_args()

    console = Console()

    try:
        # Build URL with params
        url = build_url(
            args.url,
            sort_by_price=(args.prices and not args.no_sort),
            quantity=args.quantity
        )

        use_browser = not args.no_browser
        if args.prices:
            console.print(f"[dim]Fetching prices for {args.quantity} ticket(s)...[/dim]")

        if use_browser and PLAYWRIGHT_AVAILABLE:
            console.print(f"[dim]Using headless browser...[/dim]")
        elif use_browser and not PLAYWRIGHT_AVAILABLE:
            console.print(f"[yellow]Playwright not installed. Run: pip install playwright && playwright install chromium[/yellow]")

        console.print(f"[dim]URL: {url}[/dim]")
        html = fetch_html(url, use_browser=use_browser)

        json_ld_data = extract_json_ld(html)

        # Price-focused mode
        if args.prices:
            extraction_method = "unknown"
            listings = []
            price_range = {}

            # Try Playwright JS evaluation first (most reliable - reads data-price attributes)
            if use_browser and PLAYWRIGHT_AVAILABLE:
                try:
                    listings = _extract_listings_with_playwright(url)
                    if listings:
                        extraction_method = "data_attributes"
                except Exception as e:
                    console.print(f"[yellow]Playwright extraction failed: {e}[/yellow]")

            # Fall back to Next.js data
            if not listings:
                nextjs_data = extract_nextjs_data(html)
                if nextjs_data and nextjs_data.get("listings"):
                    extraction_method = "nextjs_data"
                    listings = nextjs_data.get("listings", [])
                    if nextjs_data.get("price_range"):
                        price_range = nextjs_data["price_range"]

            # Fall back to HTML text parsing
            if not listings:
                listings = extract_listings(html)
                if listings:
                    extraction_method = "html_parse"

            # Get price range from HTML if not already found
            if not price_range:
                price_range = extract_price_range(html)

            events = extract_events(json_ld_data)
            json_ld_low = events[0].get("lowPrice", "") if events else ""

            if args.output == "json":
                # JSON output for bot integration - use new structure
                cheapest = None
                if listings:
                    sorted_listings = sorted(listings, key=lambda x: x.get("price") or float("inf"))
                    if sorted_listings and sorted_listings[0].get("price"):
                        cheapest = sorted_listings[0]

                result = {
                    "event_name": events[0].get("name", "") if events else "",
                    "event_date": events[0].get("startDate", "") if events else "",
                    "venue": events[0].get("venue", "") if events else "",
                    "lowest_all_in": cheapest["price"] if cheapest else price_range.get("low"),
                    "json_ld_price": _parse_price(json_ld_low),
                    "price_range": price_range if price_range else None,
                    "cheapest_listing": {
                        "price": cheapest["price"],
                        "section": cheapest.get("section", ""),
                        "row": cheapest.get("row", ""),
                        "labels": cheapest.get("labels", []),
                        "view": cheapest.get("view"),
                    } if cheapest else None,
                    "extraction_method": extraction_method,
                    "quantity": args.quantity,
                    "listings_count": len(listings),
                }
                print(json.dumps(result, indent=2))
            else:
                display_price_summary(price_range, listings, json_ld_low, args.quantity, extraction_method)
                if events:
                    console.print("\n")
                    display_comparison(events)
            return

        if not json_ld_data:
            console.print("[red]No JSON-LD data found on page.[/red]")
            sys.exit(1)

        # Raw mode
        if args.raw:
            print(json.dumps(json_ld_data, indent=2))
            return

        # Extract and filter events
        events = extract_events(json_ld_data)

        if not events:
            console.print("[yellow]No events found in JSON-LD data.[/yellow]")
            console.print("[dim]Try --raw to see the raw JSON-LD structure.[/dim]")
            sys.exit(1)

        events = filter_fields(events, args.fields)

        # Output
        if args.compare:
            display_comparison(events)
        elif args.output == "json":
            display_json(events)
        elif args.output == "csv":
            if args.save:
                save_csv(events, args.save, args.fields)
            else:
                import io
                output = io.StringIO()
                writer = csv.DictWriter(output, fieldnames=args.fields, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(events)
                print(output.getvalue())
        else:
            display_table(events, args.fields)

    except requests.RequestException as e:
        console.print(f"[red]HTTP Error: {e}[/red]")
        sys.exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        sys.exit(130)


if __name__ == "__main__":
    main()
