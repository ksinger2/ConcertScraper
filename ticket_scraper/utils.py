"""
Shared utilities for ticket scrapers.
"""

import re
from typing import Optional, TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from .base import BaseScraper

# Common headers to avoid being blocked
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# Site domain patterns
SITE_PATTERNS = {
    "stubhub": ["stubhub.com", "stubhub.co.uk", "stubhub.ca"],
    "tickpick": ["tickpick.com"],
    "seatgeek": ["seatgeek.com"],
    "ticketmaster": ["ticketmaster.com", "ticketmaster.co.uk", "ticketmaster.ca"],
    "vividseats": ["vividseats.com"],
    "axs": ["axs.com"],
    "dice": ["dice.fm"],
    "eventbrite": ["eventbrite.com", "eventbrite.co.uk"],
    "residentadvisor": ["ra.co", "residentadvisor.net"],
}


def detect_site(url: str) -> Optional[str]:
    """
    Detect which ticket site a URL belongs to.

    Args:
        url: Full URL to analyze

    Returns:
        Site name (e.g., "stubhub", "tickpick") or None if unknown
    """
    # Ensure url is a string
    if isinstance(url, bytes):
        url = url.decode('utf-8')

    parsed = urlparse(str(url))
    domain = str(parsed.netloc).lower()

    # Remove www. prefix
    if domain.startswith("www."):
        domain = domain[4:]

    for site_name, patterns in SITE_PATTERNS.items():
        for pattern in patterns:
            if domain == pattern or domain.endswith("." + pattern):
                return site_name

    return None


def get_scraper(site_name: str) -> "BaseScraper":
    """
    Get the appropriate scraper instance for a site.

    Args:
        site_name: Site identifier (e.g., "stubhub", "tickpick")

    Returns:
        Scraper instance for the site

    Raises:
        ValueError: If site is not supported
    """
    # Import here to avoid circular imports
    from .sites import SCRAPERS

    site_name = site_name.lower()
    if site_name not in SCRAPERS:
        supported = ", ".join(sorted(SCRAPERS.keys()))
        raise ValueError(f"Unsupported site: {site_name}. Supported sites: {supported}")

    return SCRAPERS[site_name]()


def parse_price(price_value) -> Optional[float]:
    """
    Parse a price value that might be string, int, or float.

    Args:
        price_value: Price in various formats ($123, 123.45, "123", etc.)

    Returns:
        Price as float, or None if unparseable
    """
    if price_value is None or price_value == "":
        return None
    if isinstance(price_value, (int, float)):
        return float(price_value)
    if isinstance(price_value, str):
        # Remove currency symbols and commas
        cleaned = re.sub(r'[^\d.]', '', price_value)
        if cleaned:
            try:
                return float(cleaned)
            except ValueError:
                return None
    return None


def extract_json_ld(html: str) -> list[dict]:
    """
    Extract JSON-LD structured data from HTML.

    Args:
        html: HTML content

    Returns:
        List of JSON-LD objects found in the page
    """
    import json
    from bs4 import BeautifulSoup

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


def extract_event_from_json_ld(json_ld_data: list[dict]) -> dict:
    """
    Extract event information from JSON-LD data.

    Args:
        json_ld_data: List of JSON-LD objects

    Returns:
        Dictionary with event info (name, date, venue, etc.)
    """
    event_types = {"MusicEvent", "Event", "SportsEvent", "TheaterEvent", "Festival", "ComedyEvent"}

    for item in json_ld_data:
        item_type = item.get("@type", "")
        if isinstance(item_type, list):
            types = set(item_type)
        else:
            types = {item_type}

        if types & event_types:
            return _flatten_event(item)

    return {}


def _flatten_event(event: dict) -> dict:
    """Flatten a JSON-LD event object into a simple dict."""
    result = {
        "name": event.get("name", ""),
        "startDate": event.get("startDate", ""),
        "url": event.get("url", ""),
    }

    # Location fields
    location = event.get("location", {})
    if isinstance(location, dict):
        result["venue"] = location.get("name", "")
        address = location.get("address", {})
        if isinstance(address, dict):
            result["city"] = address.get("addressLocality", "")
            result["state"] = address.get("addressRegion", "")
        else:
            result["city"] = ""
            result["state"] = ""
    else:
        result["venue"] = str(location) if location else ""
        result["city"] = ""
        result["state"] = ""

    # Offers/pricing
    offers = event.get("offers", {})
    if isinstance(offers, list) and offers:
        offers = offers[0]
    if isinstance(offers, dict):
        result["lowPrice"] = offers.get("lowPrice", offers.get("price", ""))
        result["currency"] = offers.get("priceCurrency", "USD")

    return result
