# ConcertScraper - Next Steps & Context

## Current Status

The multi-site ticket scraper is functional with 9 supported platforms. Initial testing was done with **Morgan Wallen at M&T Bank Stadium, Baltimore - July 17, 2026**.

### Working Scrapers
| Site | Status | Extraction Method | Notes |
|------|--------|-------------------|-------|
| StubHub | ✅ Working | Playwright + data-attributes | Best extraction, gets all listing details |
| TickPick | ✅ Working | JSON-LD + HTML | All-in pricing (no fees) |
| VividSeats | ✅ Working | HTML parsing | May need Playwright for some pages |
| Ticketmaster | ⚠️ Partial | HTML/embedded data | Works but limited data extraction |
| Dice | ✅ Working | HTML/JSON | All-in pricing (no fees) |
| Eventbrite | ✅ Working | HTML/JSON | Fees vary by event |

### Blocked Scrapers (Need Playwright)
| Site | Status | Issue | Fix Needed |
|------|--------|-------|------------|
| **SeatGeek** | ❌ 403 Forbidden | Bot protection | Add Playwright browser automation |
| **AXS** | ❌ 403 Forbidden | Bot protection | Add Playwright browser automation |
| Resident Advisor | ⚠️ Untested | May have bot protection | Test and add Playwright if needed |

## Immediate TODOs

### 1. Fix SeatGeek and AXS (High Priority)
Both sites return 403 Forbidden with simple requests. Need to add Playwright support similar to StubHub:

```python
# In seatgeek.py and axs.py, add:
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

def _fetch_with_playwright(self, url: str) -> str:
    # Similar to StubHub implementation
```

### 2. Improve TickPick Listing Extraction
Currently gets `lowest_all_in` from JSON-LD but `listings_count: 0`. Need to:
- Investigate TickPick's HTML structure for individual listings
- May need Playwright if they use client-side rendering

### 3. Test All Scrapers with Same Event
Run the verification prompt below to ensure all scrapers return consistent data.

## CLI Usage Reference

### Scrape Single Site
```bash
python scraper.py scrape <url> --quantity 2 --json
python scraper.py scrape <url> --site stubhub --quantity 2
```

### Compare Multiple Sites
```bash
python scraper.py compare \
  --stubhub "https://stubhub.com/..." \
  --tickpick "https://tickpick.com/..." \
  --vividseats "https://vividseats.com/..." \
  --quantity 2
```

### List Supported Sites
```bash
python scraper.py sites
```

---

## VERIFICATION PROMPT

**Copy and paste this prompt to test the scraper:**

> Test the ConcertScraper by finding tickets for the same concert across all supported ticket platforms. Use **Morgan Wallen at M&T Bank Stadium, Baltimore on July 17, 2026** as the test event.
>
> 1. Search for and find URLs for this exact event on: StubHub, TickPick, SeatGeek, VividSeats, Ticketmaster, and AXS
>
> 2. Run the scraper on each URL for 2 tickets:
>    ```bash
>    python scraper.py scrape "<url>" --quantity 2 --json
>    ```
>
> 3. Also test the compare command with the working sites:
>    ```bash
>    python scraper.py compare \
>      --stubhub "<stubhub-url>" \
>      --tickpick "<tickpick-url>" \
>      --vividseats "<vividseats-url>" \
>      --quantity 2
>    ```
>
> 4. Create a comparison table showing:
>    - Site name
>    - Lowest price found
>    - Whether fees are included
>    - Section/Row of cheapest ticket
>    - Number of listings found
>    - Any errors encountered
>
> 5. Verify that TickPick's price (all-in, no fees) is the true best deal when comparing to other sites that add fees.
>
> **Test URLs from previous session:**
> - StubHub: https://www.stubhub.com/morgan-wallen-baltimore-tickets-7-17-2026/event/159757012/
> - TickPick: https://www.tickpick.com/buy-morgan-wallen-brooks-dunn-gavin-adcock-jason-scott-and-the-high-heat-tickets-mt-bank-stadium-7-17-26-3am/7529806/
> - SeatGeek: https://seatgeek.com/morgan-wallen-tickets/baltimore-maryland-m-t-bank-stadium-2026-07-17-5-30-pm/concert/17846495
> - VividSeats: https://www.vividseats.com/morgan-wallen-tickets-baltimore-mt-bank-stadium-7-17-2026--concerts-country-and-folk/production/6207854
> - AXS: https://www.axs.com/events/1189432/morgan-wallen-tickets

---

## Previous Test Results (March 23, 2026)

| Site | Lowest Price | Fees Included? | Location | Listings | Status |
|------|-------------|----------------|----------|----------|--------|
| VividSeats | $139.00 | No (+fees) | Row 25 | 7 | ✅ |
| TickPick | $145.00 | **Yes (all-in)** | - | 0 | ✅ |
| StubHub | $182.00 | No (+fees) | Sec 521, Row 26 | 10 | ✅ |
| SeatGeek | - | - | - | - | ❌ 403 |
| AXS | - | - | - | - | ❌ 403 |

**Winner:** TickPick at $145 (true all-in price, no additional fees at checkout)

---

## Future Enhancements

### Phase 1: Fix Blocked Sites
- [ ] Add Playwright to SeatGeek scraper
- [ ] Add Playwright to AXS scraper
- [ ] Test Resident Advisor

### Phase 2: Improve Data Quality
- [ ] Fix TickPick individual listing extraction
- [ ] Add fee estimation for non-all-in sites (~20% typical)
- [ ] Add "true cost" column that estimates final price with fees

### Phase 3: Features
- [ ] Add price alerts / monitoring
- [ ] Add historical price tracking
- [ ] Discord bot integration
- [ ] Web UI dashboard

### Phase 4: More Sites
- [ ] Gametime
- [ ] MegaSeats
- [ ] TicketNetwork
- [ ] VenueKings

---

## File Structure
```
ConcertScraper/
├── scraper.py              # CLI entry point
├── ticket_scraper/
│   ├── __init__.py
│   ├── base.py             # BaseScraper, ScraperResult, ListingInfo
│   ├── utils.py            # detect_site, get_scraper, HEADERS
│   └── sites/
│       ├── __init__.py     # SCRAPERS registry
│       ├── stubhub.py      # ✅ Working (Playwright)
│       ├── tickpick.py     # ✅ Working (needs listing fix)
│       ├── seatgeek.py     # ❌ Needs Playwright
│       ├── ticketmaster.py # ⚠️ Partial
│       ├── vividseats.py   # ✅ Working
│       ├── axs.py          # ❌ Needs Playwright
│       ├── dice.py         # ✅ Working
│       ├── eventbrite.py   # ✅ Working
│       └── residentadvisor.py # ⚠️ Untested
├── stubhub_scraper.py      # Original standalone scraper
├── requirements.txt
├── .gitignore
└── NextSteps.md            # This file
```

## Dependencies
```bash
pip install -r requirements.txt
playwright install chromium  # Required for StubHub, and needed for SeatGeek/AXS fix
```
