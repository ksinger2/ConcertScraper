"""
Base scraper class that all site-specific scrapers inherit from.
"""

import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse


@dataclass
class ListingInfo:
    """Information about a single ticket listing."""
    price: float
    section: str = ""
    row: str = ""
    labels: list[str] = field(default_factory=list)
    view: Optional[str] = None
    quantity: Optional[int] = None
    listing_id: Optional[str] = None


@dataclass
class ScraperResult:
    """Unified result format for all scrapers."""
    source: str
    event_name: str
    event_date: str
    venue: str
    lowest_all_in: Optional[float]
    fees_included: bool
    cheapest_listing: Optional[ListingInfo]
    listings_count: int
    url: str
    extraction_method: str = "unknown"
    price_range: Optional[dict] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "source": self.source,
            "event_name": self.event_name,
            "event_date": self.event_date,
            "venue": self.venue,
            "lowest_all_in": self.lowest_all_in,
            "fees_included": self.fees_included,
            "cheapest_listing": {
                "price": self.cheapest_listing.price,
                "section": self.cheapest_listing.section,
                "row": self.cheapest_listing.row,
                "labels": self.cheapest_listing.labels,
                "view": self.cheapest_listing.view,
            } if self.cheapest_listing else None,
            "listings_count": self.listings_count,
            "url": self.url,
            "extraction_method": self.extraction_method,
            "price_range": self.price_range,
        }


class BaseScraper(ABC):
    """
    Abstract base class for all ticket site scrapers.

    Each site-specific scraper should implement:
    - name: Identifier for the site
    - all_in_pricing: Whether prices include all fees
    - get_lowest_price(): Get the lowest price for an event
    - get_listings(): Get all available listings
    - get_event_info(): Get event metadata
    """

    name: str = "base"
    all_in_pricing: bool = False  # True if prices include all fees

    # Rate limiting settings (in seconds)
    min_delay: float = 3.0  # Minimum delay between requests to same domain
    max_delay: float = 7.0  # Maximum delay (randomized for natural behavior)

    # Class-level tracking of last request time per domain
    _last_request_time: dict[str, float] = {}

    def _wait_for_rate_limit(self, url: str) -> None:
        """
        Wait if needed to respect rate limits.
        Adds randomized delay between requests to the same domain.
        """
        domain = urlparse(url).netloc
        current_time = time.time()

        if domain in BaseScraper._last_request_time:
            elapsed = current_time - BaseScraper._last_request_time[domain]
            delay = random.uniform(self.min_delay, self.max_delay)

            if elapsed < delay:
                wait_time = delay - elapsed
                time.sleep(wait_time)

        BaseScraper._last_request_time[domain] = time.time()

    @classmethod
    def set_rate_limit(cls, min_delay: float = 3.0, max_delay: float = 7.0) -> None:
        """
        Configure rate limiting for all scrapers.

        Args:
            min_delay: Minimum seconds between requests to same domain
            max_delay: Maximum seconds (randomized between min and max)
        """
        cls.min_delay = min_delay
        cls.max_delay = max_delay

    @classmethod
    def reset_rate_limits(cls) -> None:
        """Reset rate limit tracking (e.g., for a new session)."""
        cls._last_request_time = {}

    @abstractmethod
    def get_lowest_price(self, url: str, quantity: int = 2) -> ScraperResult:
        """
        Get the lowest all-in price for an event.

        Args:
            url: Event URL on the ticket site
            quantity: Number of tickets needed

        Returns:
            ScraperResult with price and event information
        """
        pass

    @abstractmethod
    def get_listings(self, url: str, quantity: int = 2) -> list[ListingInfo]:
        """
        Get all available ticket listings for an event.

        Args:
            url: Event URL on the ticket site
            quantity: Number of tickets needed

        Returns:
            List of ListingInfo objects sorted by price
        """
        pass

    @abstractmethod
    def get_event_info(self, url: str) -> dict[str, Any]:
        """
        Get event metadata (name, date, venue, etc).

        Args:
            url: Event URL on the ticket site

        Returns:
            Dictionary with event information
        """
        pass
