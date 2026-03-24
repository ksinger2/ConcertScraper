"""
Site-specific scraper implementations.
"""

from .stubhub import StubHubScraper
from .tickpick import TickPickScraper
from .seatgeek import SeatGeekScraper
from .ticketmaster import TicketmasterScraper
from .vividseats import VividSeatsScraper
from .axs import AXSScraper
from .dice import DiceScraper
from .eventbrite import EventbriteScraper
from .residentadvisor import ResidentAdvisorScraper

# Registry of all available scrapers
SCRAPERS = {
    "stubhub": StubHubScraper,
    "tickpick": TickPickScraper,
    "seatgeek": SeatGeekScraper,
    "ticketmaster": TicketmasterScraper,
    "vividseats": VividSeatsScraper,
    "axs": AXSScraper,
    "dice": DiceScraper,
    "eventbrite": EventbriteScraper,
    "residentadvisor": ResidentAdvisorScraper,
}

__all__ = [
    "StubHubScraper",
    "TickPickScraper",
    "SeatGeekScraper",
    "TicketmasterScraper",
    "VividSeatsScraper",
    "AXSScraper",
    "DiceScraper",
    "EventbriteScraper",
    "ResidentAdvisorScraper",
    "SCRAPERS",
]
