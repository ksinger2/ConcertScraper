"""
Multi-site ticket scraper package.

Supports multiple ticket platforms with a unified interface.
"""

from .base import BaseScraper, ScraperResult
from .utils import detect_site, get_scraper

__all__ = ["BaseScraper", "ScraperResult", "detect_site", "get_scraper"]
