#!/usr/bin/env python3
"""
Compare ticket prices for 5 concerts across multiple ticket sites.
"""

import sys
from dataclasses import dataclass
from datetime import datetime

sys.path.insert(0, '/Users/karen/Desktop/Git Projects/ScraperTest')

from ticket_scraper.base import BaseScraper
from ticket_scraper.sites.stubhub import StubHubScraper
from ticket_scraper.sites.tickpick import TickPickScraper
from ticket_scraper.sites.vividseats import VividSeatsScraper

# Configure rate limiting (3-7 seconds between requests to same domain)
BaseScraper.set_rate_limit(min_delay=3.0, max_delay=7.0)


@dataclass
class ConcertInfo:
    name: str
    date: str
    venue: str
    urls: dict[str, str]


# Define all concerts
CONCERTS = [
    ConcertInfo(
        name="Zara Larsson",
        date="Mar 26, 2026",
        venue="Brooklyn Paramount, NYC",
        urls={
            "StubHub": "https://www.stubhub.com/zara-larsson-brooklyn-tickets-3-26-2026/event/159308473/",
            "TickPick": "https://www.tickpick.com/buy-zara-larsson-tickets-brooklyn-paramount-3-26-26-7pm/7380479/",
            "VividSeats": "https://www.vividseats.com/zara-larsson-tickets-brooklyn-brooklyn-paramount-3-26-2026--concerts-pop/production/5980491",
        }
    ),
    ConcertInfo(
        name="Jack Johnson",
        date="Jun 27, 2026",
        venue="TD Pavilion, Philadelphia",
        urls={
            "StubHub": "https://www.stubhub.com/jack-johnson-philadelphia-tickets-6-27-2026/event/159819719/",
            "TickPick": "https://www.tickpick.com/buy-jack-johnson-tickets-td-pavilion-at-the-mann-center-for-the-performing-arts-6-27-26-7pm/7547979/",
            "VividSeats": "https://www.vividseats.com/jack-johnson-tickets-philadelphia-td-pavilion-at-the-mann-6-27-2026--concerts-pop/production/6268961",
        }
    ),
    ConcertInfo(
        name="Bruno Mars",
        date="May 2, 2026",
        venue="Northwest Stadium, Landover MD",
        urls={
            "StubHub": "https://www.stubhub.com/bruno-mars-landover-tickets-5-2-2026/event/160227613/",
            "TickPick": "https://www.tickpick.com/buy-bruno-mars-leon-thomas-dj-pee-wee-tickets-northwest-stadium-5-2-26-7pm/7668626/",
            "VividSeats": "https://www.vividseats.com/bruno-mars-tickets-landover-northwest-stadium-5-2-2026/production/6501568",
        }
    ),
    ConcertInfo(
        name="My Chemical Romance",
        date="Aug 9, 2026",
        venue="Citi Field, Queens NY",
        urls={
            "StubHub": "https://www.stubhub.com/my-chemical-romance-flushing-tickets-8-9-2026/event/159456666/",
            "TickPick": "https://www.tickpick.com/buy-my-chemical-romance-franz-ferdinand-tickets-citi-field-8-9-26-6pm/7424930/",
            "VividSeats": "https://www.vividseats.com/my-chemical-romance-tickets-flushing-citi-field-8-9-2026--concerts-rock/production/6062357",
        }
    ),
    ConcertInfo(
        name="Ariana Grande",
        date="Jul 12, 2026",
        venue="Barclays Center, Brooklyn",
        urls={
            "StubHub": "https://www.stubhub.com/ariana-grande-brooklyn-tickets-7-12-2026/event/159278554/",
            "TickPick": "https://www.tickpick.com/buy-ariana-grande-tickets-barclays-center-7-12-26-7pm/7373444/",
            "VividSeats": "https://www.vividseats.com/ariana-grande-tickets-brooklyn-barclays-center-7-12-2026--concerts-pop/production/5973675",
        }
    ),
]

SCRAPERS = {
    "StubHub": StubHubScraper(),
    "TickPick": TickPickScraper(),
    "VividSeats": VividSeatsScraper(),
}


def scrape_concert(concert: ConcertInfo) -> list[dict]:
    """Scrape all URLs for a concert and return results."""
    results = []

    print(f"\n  Scraping: {concert.name} ({concert.date})")

    for site_name, url in concert.urls.items():
        print(f"    [{site_name}] Fetching...", end=" ", flush=True)
        scraper = SCRAPERS.get(site_name)

        if not scraper:
            print("No scraper")
            results.append({"site": site_name, "price": None, "url": url, "error": "No scraper"})
            continue

        try:
            result = scraper.get_lowest_price(url, quantity=2)
            price = result.lowest_all_in
            fees_included = result.fees_included

            if price:
                print(f"${price:.2f}")
            else:
                print("No price found")

            results.append({
                "site": site_name,
                "price": price,
                "fees_included": fees_included,
                "url": url,
                "error": None
            })
        except Exception as e:
            print(f"Error: {str(e)[:40]}")
            results.append({"site": site_name, "price": None, "url": url, "error": str(e)})

    return results


def format_price(r: dict) -> str:
    """Format price with fees indicator."""
    if r.get("error"):
        return "Error"
    elif r.get("price"):
        fees = "" if r.get("fees_included") else "*"
        return f"${r['price']:.0f}{fees}"
    else:
        return "N/A"


def main():
    print("=" * 70)
    print("TICKET PRICE COMPARISON - 5 CONCERTS")
    print("=" * 70)
    print(f"Rate limiting: {BaseScraper.min_delay}-{BaseScraper.max_delay}s between requests")
    print("=" * 70)

    all_results = {}

    for concert in CONCERTS:
        all_results[concert.name] = {
            "info": concert,
            "results": scrape_concert(concert)
        }

    # Build output
    output_lines = []
    output_lines.append("\n# Ticket Price Comparison\n")
    output_lines.append(f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n")
    output_lines.append("**Note:** Prices marked with `*` have additional fees at checkout. Unmarked prices are all-in.\n")

    # Summary table
    output_lines.append("## Quick Comparison\n")
    output_lines.append("| Concert | Date | StubHub | TickPick | VividSeats | Best Deal | Link |")
    output_lines.append("|---------|------|---------|----------|------------|-----------|------|")

    for concert in CONCERTS:
        data = all_results[concert.name]
        results = {r["site"]: r for r in data["results"]}

        stubhub = format_price(results.get("StubHub", {}))
        tickpick = format_price(results.get("TickPick", {}))
        vividseats = format_price(results.get("VividSeats", {}))

        # Find best deal
        valid_prices = [(r["site"], r["price"], r.get("fees_included", False), r.get("url", ""))
                        for r in data["results"] if r.get("price")]
        if valid_prices:
            # Sort by price
            valid_prices.sort(key=lambda x: x[1])
            best_site, best_price, best_fees, best_url = valid_prices[0]
            best_deal = f"**{best_site}** ${best_price:.0f}"
            best_link = f"[Buy]({best_url})"
        else:
            best_deal = "N/A"
            best_link = "-"

        output_lines.append(f"| {concert.name} | {concert.date} | {stubhub} | {tickpick} | {vividseats} | {best_deal} | {best_link} |")

    output_lines.append("")

    # Detailed tables for each concert
    output_lines.append("---\n")
    output_lines.append("## Detailed Results\n")

    for concert in CONCERTS:
        data = all_results[concert.name]
        results = data["results"]

        output_lines.append(f"### {concert.name}")
        output_lines.append(f"**{concert.date}** @ {concert.venue}\n")
        output_lines.append("| Site | Price | Fees | Link |")
        output_lines.append("|------|-------|------|------|")

        # Sort by price
        sorted_results = sorted(results, key=lambda x: x["price"] if x.get("price") else float('inf'))

        for i, r in enumerate(sorted_results):
            site = r["site"]
            url = r["url"]

            if r.get("error"):
                price_str = "Error"
                fees_str = "-"
            elif r.get("price"):
                price_str = f"${r['price']:.2f}"
                if i == 0:
                    price_str = f"**{price_str}** ⭐"
                fees_str = "All-in" if r.get("fees_included") else "+fees"
            else:
                price_str = "Not found"
                fees_str = "-"

            link = f"[Buy]({url})"
            output_lines.append(f"| {site} | {price_str} | {fees_str} | {link} |")

        output_lines.append("")

    # Print and save
    output_text = "\n".join(output_lines)
    print("\n" + "=" * 70)
    print("RESULTS")
    print("=" * 70)
    print(output_text)

    with open("ticket_comparison_results.md", "w") as f:
        f.write(output_text)

    print("\n✓ Results saved to ticket_comparison_results.md")


if __name__ == "__main__":
    main()
