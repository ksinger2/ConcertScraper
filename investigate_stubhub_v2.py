#!/usr/bin/env python3
"""
StubHub Price Investigation Script v2
More robust handling to find all price data
"""

import asyncio
import json
import re
from pathlib import Path
from playwright.async_api import async_playwright

URL = "https://www.stubhub.com/bruno-mars-santa-clara-tickets-10-10-2026/event/160226903?sortBy=NEWPRICE&sortDirection=0&quantity=2"
OUTPUT_DIR = Path("/Users/karen/Desktop/Git Projects/ScraperTest")

async def main():
    print("Starting StubHub investigation v2...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        page = await context.new_page()

        # Track XHR/fetch responses
        api_data = []

        async def handle_response(response):
            url = response.url
            content_type = response.headers.get('content-type', '')
            if 'json' in content_type or '/api/' in url or 'listing' in url.lower():
                try:
                    body = await response.text()
                    if body and len(body) > 10:
                        api_data.append({
                            'url': url,
                            'status': response.status,
                            'body': body[:50000]
                        })
                except:
                    pass

        page.on("response", handle_response)

        print(f"Navigating to: {URL}")

        try:
            # Don't wait for networkidle - just domcontentloaded
            await page.goto(URL, wait_until="domcontentloaded", timeout=30000)
            print("Initial page loaded")

            # Wait for listings to appear
            try:
                await page.wait_for_selector('[data-listing-id]', timeout=15000)
                print("Found listing elements")
            except:
                print("No [data-listing-id] found, checking for other selectors...")
                try:
                    await page.wait_for_selector('[data-testid="listings-container"]', timeout=10000)
                    print("Found listings container")
                except:
                    print("No listings container found either")

            # Give time for dynamic content
            await asyncio.sleep(3)

        except Exception as e:
            print(f"Navigation error: {e}")

        # Save full HTML
        html_content = await page.content()
        html_path = OUTPUT_DIR / "debug_page.html"
        html_path.write_text(html_content)
        print(f"Saved HTML ({len(html_content)} bytes)")

        # Extract all listing data via JavaScript
        listing_data = await page.evaluate("""
            () => {
                const results = {
                    listings: [],
                    priceElements: [],
                    mapPins: [],
                    rawText: []
                };

                // Get all elements with data-listing-id
                document.querySelectorAll('[data-listing-id]').forEach(el => {
                    results.listings.push({
                        listingId: el.getAttribute('data-listing-id'),
                        price: el.getAttribute('data-price'),
                        index: el.getAttribute('data-index'),
                        isSold: el.getAttribute('data-is-sold'),
                        featureId: el.getAttribute('data-feature-id'),
                        className: el.className,
                        innerText: el.innerText.substring(0, 500)
                    });
                });

                // Get all price-like elements
                document.querySelectorAll('[class*="price"], [class*="Price"]').forEach(el => {
                    const text = el.innerText.trim();
                    if (text.match(/\$[\d,]+/)) {
                        results.priceElements.push({
                            text: text.substring(0, 100),
                            className: el.className?.substring?.(0, 100) || '',
                            tagName: el.tagName
                        });
                    }
                });

                // Look for map pins with prices (often use different styling)
                document.querySelectorAll('svg text, [class*="pin"], [class*="Pin"], [class*="marker"], [class*="Marker"]').forEach(el => {
                    const text = el.innerText?.trim() || el.textContent?.trim();
                    if (text && text.match(/\$[\d,]+/)) {
                        results.mapPins.push({
                            text: text.substring(0, 50),
                            className: el.className?.toString?.()?.substring?.(0, 100) || ''
                        });
                    }
                });

                // Get ALL text with $ amounts
                const walker = document.createTreeWalker(
                    document.body,
                    NodeFilter.SHOW_TEXT,
                    null,
                    false
                );
                let node;
                let count = 0;
                while ((node = walker.nextNode()) && count < 200) {
                    const text = node.textContent.trim();
                    if (text.match(/\$\d{2,4}/)) {
                        results.rawText.push({
                            text: text.substring(0, 100),
                            parentTag: node.parentElement?.tagName,
                            parentClass: node.parentElement?.className?.substring?.(0, 100) || ''
                        });
                        count++;
                    }
                }

                return results;
            }
        """)

        # Save listing data
        findings = {
            "url": URL,
            "listing_elements": listing_data.get('listings', []),
            "price_elements": listing_data.get('priceElements', []),
            "map_pins": listing_data.get('mapPins', []),
            "raw_price_text": listing_data.get('rawText', []),
            "api_calls": [{"url": d["url"], "status": d["status"], "preview": d["body"][:500]} for d in api_data]
        }

        findings_path = OUTPUT_DIR / "debug_findings.json"
        findings_path.write_text(json.dumps(findings, indent=2, default=str))
        print(f"Saved findings to {findings_path}")

        # Save full API responses
        if api_data:
            api_path = OUTPUT_DIR / "debug_api_responses.json"
            api_path.write_text(json.dumps(api_data, indent=2))
            print(f"Saved {len(api_data)} API responses")

        # Take screenshot
        await page.screenshot(path=str(OUTPUT_DIR / "debug_screenshot.png"), full_page=False)
        print("Saved screenshot")

        # Print summary
        print("\n" + "="*60)
        print("SUMMARY")
        print("="*60)

        print(f"\nListings found: {len(listing_data.get('listings', []))}")
        for l in listing_data.get('listings', []):
            print(f"  - ID {l['listingId']}: {l['price']}")

        print(f"\nPrice elements found: {len(listing_data.get('priceElements', []))}")
        seen = set()
        for p in listing_data.get('priceElements', []):
            if p['text'] not in seen:
                print(f"  - {p['text']}")
                seen.add(p['text'])

        print(f"\nAPI calls captured: {len(api_data)}")
        for a in api_data[:5]:
            print(f"  - {a['url'][:80]}...")

        await browser.close()

    print("\nInvestigation complete!")

if __name__ == "__main__":
    asyncio.run(main())
