#!/usr/bin/env python3
"""
StubHub Page Investigation Script
Analyzes the page structure to find where prices are stored
"""

import asyncio
import json
import re
from pathlib import Path
from playwright.async_api import async_playwright

URL = "https://www.stubhub.com/bruno-mars-santa-clara-tickets-10-10-2026/event/160226903?sortBy=NEWPRICE&sortDirection=0&quantity=2"
OUTPUT_DIR = Path("/Users/karen/Desktop/Git Projects/ScraperTest")

async def main():
    print("Starting StubHub investigation...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        page = await context.new_page()

        print(f"Navigating to: {URL}")

        # Track network requests for API calls
        api_responses = []

        async def handle_response(response):
            url = response.url
            if 'api' in url.lower() or 'listing' in url.lower() or 'price' in url.lower():
                try:
                    if 'json' in response.headers.get('content-type', ''):
                        body = await response.json()
                        api_responses.append({
                            'url': url,
                            'data': body
                        })
                except:
                    pass

        page.on("response", handle_response)

        try:
            await page.goto(URL, wait_until="networkidle", timeout=60000)
            print("Page loaded, waiting for dynamic content...")

            # Wait a bit more for dynamic content
            await asyncio.sleep(5)

            # Scroll to load lazy content
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
            await asyncio.sleep(2)

        except Exception as e:
            print(f"Navigation error: {e}")

        # 1. Save full HTML
        html_content = await page.content()
        html_path = OUTPUT_DIR / "debug_page.html"
        html_path.write_text(html_content)
        print(f"Saved HTML to {html_path} ({len(html_content)} bytes)")

        # 2. Extract and save all script tags
        scripts_content = []
        script_elements = await page.query_selector_all("script")

        for i, script in enumerate(script_elements):
            script_type = await script.get_attribute("type") or "text/javascript"
            script_src = await script.get_attribute("src")
            script_id = await script.get_attribute("id") or ""
            inner_text = await script.inner_text()

            scripts_content.append(f"\n{'='*80}")
            scripts_content.append(f"SCRIPT #{i+1}")
            scripts_content.append(f"Type: {script_type}")
            scripts_content.append(f"ID: {script_id}")
            scripts_content.append(f"Src: {script_src or 'inline'}")
            scripts_content.append(f"{'='*80}")

            if inner_text.strip():
                scripts_content.append(inner_text[:50000])  # Limit size

        scripts_path = OUTPUT_DIR / "debug_scripts.txt"
        scripts_path.write_text("\n".join(scripts_content))
        print(f"Saved scripts to {scripts_path}")

        # 3. Look for JSON data sources
        json_findings = []

        # Check for __NEXT_DATA__
        next_data = await page.evaluate("""
            () => {
                const el = document.getElementById('__NEXT_DATA__');
                if (el) return el.textContent;
                return null;
            }
        """)
        if next_data:
            json_findings.append(("__NEXT_DATA__", next_data))

        # Check for window variables
        window_vars = await page.evaluate("""
            () => {
                const results = {};
                const patterns = ['__data', '__INITIAL', '__STATE', '__PRELOADED', 'window.data', '__APP'];
                for (const key of Object.keys(window)) {
                    if (patterns.some(p => key.includes(p)) ||
                        (typeof window[key] === 'object' && window[key] !== null)) {
                        try {
                            const val = window[key];
                            if (typeof val === 'object' && JSON.stringify(val).length < 500000) {
                                results[key] = val;
                            }
                        } catch (e) {}
                    }
                }
                return results;
            }
        """)

        for key, val in window_vars.items():
            if val and str(val) != '[object Object]':
                json_findings.append((f"window.{key}", json.dumps(val, indent=2)[:10000]))

        # Check data-* attributes with JSON
        data_attrs = await page.evaluate("""
            () => {
                const results = [];
                document.querySelectorAll('*').forEach(el => {
                    for (const attr of el.attributes) {
                        if (attr.name.startsWith('data-') && attr.value.includes('{')) {
                            try {
                                JSON.parse(attr.value);
                                results.push({
                                    element: el.tagName,
                                    attribute: attr.name,
                                    value: attr.value.substring(0, 5000)
                                });
                            } catch (e) {}
                        }
                    }
                });
                return results;
            }
        """)

        for item in data_attrs:
            json_findings.append((f"data-attr: {item['element']}[{item['attribute']}]", item['value']))

        # 4. Find price elements in DOM
        price_info = await page.evaluate("""
            () => {
                const results = [];

                // Find elements with $ in text
                const walker = document.createTreeWalker(
                    document.body,
                    NodeFilter.SHOW_TEXT,
                    null,
                    false
                );

                let node;
                let count = 0;
                while ((node = walker.nextNode()) && count < 100) {
                    const text = node.textContent.trim();
                    if (text.match(/\$\s*\d{2,4}/)) {
                        const parent = node.parentElement;
                        results.push({
                            text: text,
                            tagName: parent?.tagName,
                            className: parent?.className,
                            id: parent?.id,
                            parentHTML: parent?.outerHTML?.substring(0, 500)
                        });
                        count++;
                    }
                }

                // Also check for price-related classes/attributes
                const priceElements = document.querySelectorAll('[class*="price"], [class*="Price"], [data-price], [data-amount]');
                priceElements.forEach(el => {
                    results.push({
                        text: el.textContent?.substring(0, 100),
                        tagName: el.tagName,
                        className: el.className,
                        dataPrice: el.getAttribute('data-price'),
                        dataAmount: el.getAttribute('data-amount'),
                        outerHTML: el.outerHTML?.substring(0, 500)
                    });
                });

                return results;
            }
        """)

        # 5. Look for listing containers
        listing_info = await page.evaluate("""
            () => {
                const results = [];

                // Common listing container patterns
                const selectors = [
                    '[class*="listing"]',
                    '[class*="Listing"]',
                    '[class*="ticket"]',
                    '[class*="Ticket"]',
                    '[class*="row"]',
                    '[class*="Row"]',
                    '[class*="item"]',
                    '[class*="Item"]',
                    '[data-listing]',
                    '[data-ticket]',
                    'button[class*="buy"]',
                    'button[class*="Buy"]'
                ];

                for (const selector of selectors) {
                    const elements = document.querySelectorAll(selector);
                    if (elements.length > 0 && elements.length < 50) {
                        results.push({
                            selector: selector,
                            count: elements.length,
                            samples: Array.from(elements).slice(0, 3).map(el => ({
                                tagName: el.tagName,
                                className: el.className?.substring?.(0, 200) || el.className,
                                id: el.id,
                                text: el.textContent?.substring(0, 200),
                                attributes: Array.from(el.attributes).map(a => `${a.name}="${a.value?.substring(0, 100)}"`).join(', ')
                            }))
                        });
                    }
                }

                return results;
            }
        """)

        # 6. Check for specific StubHub patterns
        stubhub_data = await page.evaluate("""
            () => {
                const results = {};

                // Check for StubHub specific globals
                if (window.__INITIAL_STATE__) results.__INITIAL_STATE__ = window.__INITIAL_STATE__;
                if (window.__PRELOADED_STATE__) results.__PRELOADED_STATE__ = window.__PRELOADED_STATE__;
                if (window.stubhub) results.stubhub = window.stubhub;
                if (window.SH) results.SH = window.SH;
                if (window.pageData) results.pageData = window.pageData;
                if (window.eventData) results.eventData = window.eventData;
                if (window.listingData) results.listingData = window.listingData;

                // Look for Redux/Apollo state
                if (window.__APOLLO_STATE__) results.__APOLLO_STATE__ = window.__APOLLO_STATE__;
                if (window.__REDUX_STATE__) results.__REDUX_STATE__ = window.__REDUX_STATE__;

                return results;
            }
        """)

        # Take a screenshot for reference
        await page.screenshot(path=str(OUTPUT_DIR / "debug_screenshot.png"), full_page=False)
        print("Saved screenshot")

        # Save API responses captured
        if api_responses:
            api_path = OUTPUT_DIR / "debug_api_responses.json"
            api_path.write_text(json.dumps(api_responses, indent=2))
            print(f"Saved {len(api_responses)} API responses")

        # Compile all findings
        findings = {
            "json_sources": [(name, data[:2000] if isinstance(data, str) else str(data)[:2000]) for name, data in json_findings],
            "price_elements": price_info[:30],
            "listing_containers": listing_info,
            "stubhub_globals": {k: str(v)[:2000] for k, v in stubhub_data.items()} if stubhub_data else {},
            "api_responses_count": len(api_responses)
        }

        findings_path = OUTPUT_DIR / "debug_findings.json"
        findings_path.write_text(json.dumps(findings, indent=2, default=str))
        print(f"Saved findings to {findings_path}")

        await browser.close()

    print("\nInvestigation complete!")
    print(f"Files saved in: {OUTPUT_DIR}")
    print("- debug_page.html (full HTML)")
    print("- debug_scripts.txt (all script tags)")
    print("- debug_findings.json (structured analysis)")
    print("- debug_screenshot.png (visual reference)")
    if api_responses:
        print("- debug_api_responses.json (captured API calls)")

if __name__ == "__main__":
    asyncio.run(main())
