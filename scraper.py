#!/usr/bin/env python3
"""
Multi-Site Ticket Scraper CLI

Supports multiple ticket platforms with a unified interface.

Usage:
    # Auto-detect site from URL
    python scraper.py <url> --quantity 2

    # Specify site explicitly
    python scraper.py stubhub <url> --quantity 2

    # Compare prices across multiple sites
    python scraper.py compare --stubhub <url1> --tickpick <url2> --quantity 2

    # List supported sites
    python scraper.py sites
"""

import argparse
import json
import sys
from typing import Optional

from rich.console import Console
from rich.table import Table

from ticket_scraper import detect_site, get_scraper, ScraperResult
from ticket_scraper.sites import SCRAPERS


console = Console()


def display_result(result: ScraperResult, show_json: bool = False) -> None:
    """Display scraper result in a formatted table or JSON."""
    if show_json:
        print(json.dumps(result.to_dict(), indent=2))
        return

    # Create summary table
    table = Table(title=f"[bold]{result.source.upper()}[/bold] - {result.event_name or 'Unknown Event'}")
    table.add_column("Field", style="bold cyan", width=20)
    table.add_column("Value", style="white")

    table.add_row("Event", result.event_name or "N/A")
    table.add_row("Date", result.event_date or "N/A")
    table.add_row("Venue", result.venue or "N/A")

    # Price info
    if result.lowest_all_in is not None:
        price_str = f"${result.lowest_all_in:.2f}"
        if result.fees_included:
            price_str += " [green](all-in, no fees)[/green]"
        else:
            price_str += " [yellow](+ fees)[/yellow]"
        table.add_row("Lowest Price", price_str)
    else:
        table.add_row("Lowest Price", "[red]Not found[/red]")

    # Cheapest listing details
    if result.cheapest_listing:
        listing = result.cheapest_listing
        location_parts = []
        if listing.section:
            location_parts.append(f"Section {listing.section}")
        if listing.row:
            location_parts.append(f"Row {listing.row}")
        if location_parts:
            table.add_row("Location", ", ".join(location_parts))
        if listing.labels:
            table.add_row("Labels", ", ".join(listing.labels))
        if listing.view:
            table.add_row("View", listing.view)

    table.add_row("Listings Found", str(result.listings_count))
    table.add_row("Method", result.extraction_method)

    console.print(table)
    console.print(f"\n[dim]URL: {result.url}[/dim]")


def display_comparison(results: list[ScraperResult], show_json: bool = False) -> None:
    """Display comparison of results from multiple sites."""
    if show_json:
        print(json.dumps([r.to_dict() for r in results], indent=2))
        return

    # Sort by price (None values last)
    results.sort(key=lambda r: r.lowest_all_in if r.lowest_all_in is not None else float('inf'))

    table = Table(title="Price Comparison")
    table.add_column("Site", style="bold cyan")
    table.add_column("Price", style="white", justify="right")
    table.add_column("Fees", style="white")
    table.add_column("Section", style="white")
    table.add_column("Listings", style="white", justify="right")

    for i, result in enumerate(results):
        # Highlight cheapest
        if i == 0 and result.lowest_all_in is not None:
            site_name = f"[bold green]{result.source.upper()}[/bold green] [green]★[/green]"
        else:
            site_name = result.source.upper()

        if result.lowest_all_in is not None:
            price = f"${result.lowest_all_in:.2f}"
        else:
            price = "[red]N/A[/red]"

        fees = "[green]Included[/green]" if result.fees_included else "[yellow]+fees[/yellow]"

        section = ""
        if result.cheapest_listing:
            if result.cheapest_listing.section:
                section = result.cheapest_listing.section

        table.add_row(
            site_name,
            price,
            fees,
            section,
            str(result.listings_count),
        )

    console.print(table)

    # Show event info from first result with data
    for result in results:
        if result.event_name:
            console.print(f"\n[bold]Event:[/bold] {result.event_name}")
            if result.venue:
                console.print(f"[bold]Venue:[/bold] {result.venue}")
            if result.event_date:
                console.print(f"[bold]Date:[/bold] {result.event_date}")
            break


def cmd_scrape(args) -> int:
    """Scrape a single URL."""
    url = args.url
    quantity = args.quantity

    # Detect or use specified site
    if args.site:
        site_name = args.site
    else:
        site_name = detect_site(url)
        if not site_name:
            console.print(f"[red]Could not detect site from URL: {url}[/red]")
            console.print("[dim]Use --site to specify the site explicitly[/dim]")
            return 1

    try:
        scraper = get_scraper(site_name)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        return 1

    console.print(f"[dim]Scraping {site_name} for {quantity} ticket(s)...[/dim]")

    try:
        result = scraper.get_lowest_price(url, quantity=quantity)
        display_result(result, show_json=args.json)
        return 0
    except Exception as e:
        console.print(f"[red]Error scraping {site_name}: {e}[/red]")
        if args.debug:
            import traceback
            traceback.print_exc()
        return 1


def cmd_compare(args) -> int:
    """Compare prices across multiple sites."""
    quantity = args.quantity
    results = []

    # Collect URLs from site-specific arguments
    site_urls = {
        "stubhub": args.stubhub,
        "tickpick": args.tickpick,
        "seatgeek": args.seatgeek,
        "ticketmaster": args.ticketmaster,
        "vividseats": args.vividseats,
        "axs": args.axs,
        "dice": args.dice,
        "eventbrite": args.eventbrite,
        "residentadvisor": args.residentadvisor,
    }

    urls_to_scrape = [(site, url) for site, url in site_urls.items() if url]

    if not urls_to_scrape:
        console.print("[red]No URLs provided. Use --stubhub, --tickpick, etc.[/red]")
        return 1

    console.print(f"[dim]Comparing prices across {len(urls_to_scrape)} site(s) for {quantity} ticket(s)...[/dim]")

    for site_name, url in urls_to_scrape:
        try:
            scraper = get_scraper(site_name)
            console.print(f"[dim]  Fetching {site_name}...[/dim]")
            result = scraper.get_lowest_price(url, quantity=quantity)
            results.append(result)
        except Exception as e:
            console.print(f"[yellow]  Warning: {site_name} failed: {e}[/yellow]")

    if not results:
        console.print("[red]All scrapers failed[/red]")
        return 1

    console.print()
    display_comparison(results, show_json=args.json)
    return 0


def cmd_sites(args) -> int:
    """List supported sites."""
    table = Table(title="Supported Ticket Sites")
    table.add_column("Site", style="bold cyan")
    table.add_column("All-In Pricing", style="white")
    table.add_column("Type", style="white")

    site_info = {
        "stubhub": ("No (fees added)", "Secondary Market"),
        "tickpick": ("Yes (no fees)", "Secondary Market"),
        "seatgeek": ("No (fees shown)", "Secondary Market"),
        "ticketmaster": ("No (fees added)", "Primary + Resale"),
        "vividseats": ("No (fees added)", "Secondary Market"),
        "axs": ("No (fees added)", "Primary Market"),
        "dice": ("Yes (no fees)", "Primary (Music)"),
        "eventbrite": ("Varies", "Events Platform"),
        "residentadvisor": ("Varies", "Electronic Music"),
    }

    for site_name in sorted(SCRAPERS.keys()):
        info = site_info.get(site_name, ("Unknown", "Unknown"))
        fees_style = "[green]" if "Yes" in info[0] else "[yellow]"
        table.add_row(site_name, f"{fees_style}{info[0]}[/]", info[1])

    console.print(table)
    return 0


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Multi-site ticket price scraper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Scrape a URL (auto-detect site)
  python scraper.py scrape https://www.stubhub.com/event/123456 --quantity 2

  # Specify site explicitly
  python scraper.py scrape https://tickpick.com/buy-event/123 --site tickpick

  # Compare prices across sites
  python scraper.py compare \\
    --stubhub "https://stubhub.com/..." \\
    --tickpick "https://tickpick.com/..." \\
    --quantity 2

  # List supported sites
  python scraper.py sites
        """,
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # Sites command
    sites_parser = subparsers.add_parser("sites", help="List supported sites")

    # Compare command
    compare_parser = subparsers.add_parser("compare", help="Compare prices across sites")
    compare_parser.add_argument("--stubhub", metavar="URL", help="StubHub URL")
    compare_parser.add_argument("--tickpick", metavar="URL", help="TickPick URL")
    compare_parser.add_argument("--seatgeek", metavar="URL", help="SeatGeek URL")
    compare_parser.add_argument("--ticketmaster", metavar="URL", help="Ticketmaster URL")
    compare_parser.add_argument("--vividseats", metavar="URL", help="VividSeats URL")
    compare_parser.add_argument("--axs", metavar="URL", help="AXS URL")
    compare_parser.add_argument("--dice", metavar="URL", help="Dice URL")
    compare_parser.add_argument("--eventbrite", metavar="URL", help="Eventbrite URL")
    compare_parser.add_argument("--residentadvisor", metavar="URL", help="Resident Advisor URL")
    compare_parser.add_argument("-q", "--quantity", type=int, default=2, help="Number of tickets (default: 2)")
    compare_parser.add_argument("--json", action="store_true", help="Output as JSON")
    compare_parser.add_argument("--debug", action="store_true", help="Show debug info on errors")

    # Scrape command
    scrape_parser = subparsers.add_parser("scrape", help="Scrape a single URL")
    scrape_parser.add_argument("url", help="URL to scrape")
    scrape_parser.add_argument("--site", "-s", choices=list(SCRAPERS.keys()), help="Site to use (auto-detected if not specified)")
    scrape_parser.add_argument("-q", "--quantity", type=int, default=2, help="Number of tickets (default: 2)")
    scrape_parser.add_argument("--json", action="store_true", help="Output as JSON")
    scrape_parser.add_argument("--debug", action="store_true", help="Show debug info on errors")

    args = parser.parse_args()

    # Handle subcommands
    if args.command == "sites":
        return cmd_sites(args)
    elif args.command == "compare":
        return cmd_compare(args)
    elif args.command == "scrape":
        return cmd_scrape(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
