<p align="center">
  <img src="assets/banner.svg" alt="Google Maps Scraper" width="900">
</p>

<p align="center">
  <strong>Extract business data from Google Maps — names, addresses, phone numbers, websites, emails and hours in one command.</strong>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-575ECF?style=flat-square" alt="License: MIT"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/Python-3.9%2B-575ECF?style=flat-square&logo=python&logoColor=white" alt="Python 3.9+"></a>
  <img src="https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-575ECF?style=flat-square" alt="Platform">
  <a href="https://www.selenium.dev/"><img src="https://img.shields.io/badge/Selenium-4.15%2B-575ECF?style=flat-square&logo=selenium&logoColor=white" alt="Selenium"></a>
  <a href="https://github.com/SoCloseSociety/GoogleMapScraper/stargazers"><img src="https://img.shields.io/github/stars/SoCloseSociety/GoogleMapScraper?style=flat-square&color=575ECF" alt="GitHub Stars"></a>
  <a href="https://github.com/SoCloseSociety/GoogleMapScraper/issues"><img src="https://img.shields.io/github/issues/SoCloseSociety/GoogleMapScraper?style=flat-square&color=575ECF" alt="Issues"></a>
  <a href="https://github.com/SoCloseSociety/GoogleMapScraper/network/members"><img src="https://img.shields.io/github/forks/SoCloseSociety/GoogleMapScraper?style=flat-square&color=575ECF" alt="Forks"></a>
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> &bull;
  <a href="#key-features">Features</a> &bull;
  <a href="#configuration">Configuration</a> &bull;
  <a href="#faq">FAQ</a> &bull;
  <a href="#contributing">Contributing</a>
</p>

---

## What is Google Maps Scraper?

**Google Maps Scraper** is a free, open-source **Google Maps data extraction tool** built with Python and Selenium. It lets you scrape business information from any Google Maps search — names, addresses, phone numbers, websites, and opening hours — into clean CSV files.

Need to build a lead list, research local businesses, or gather market data? This scraper automates what would take hours of manual copy-pasting. Provide a search query or URL, and get a ready-to-use CSV.

### Who is this for?

- **Sales Teams** building lead lists of local businesses
- **Market Researchers** analyzing business density by area
- **Real Estate Agents** studying commercial activity in neighborhoods
- **Startup Founders** doing competitive landscape analysis
- **SEO Agencies** auditing local business listings
- **Data Analysts** collecting location-based business data

### Key Features

- **Single Command** - Provide a search query or URL, get a CSV
- **Two-Phase Scraping** - Collects all place links first, then extracts details
- **Crash-Safe** - Progress saved after each extraction; interrupt and resume anytime
- **Headless Mode** - Run without a visible browser window
- **Smart Scrolling** - Automatically detects when all results are loaded
- **Anti-Detection** - Randomized delays, clean browser fingerprint
- **CLI-First** - Fully scriptable with argparse options
- **Lightweight** - Only 4 dependencies, no bloat
- **Cross-Platform** - Works on Windows, macOS, and Linux
- **Free & Open Source** - MIT license, no API key required

---

## Quick Start

### Prerequisites

| Requirement | Details |
|-------------|---------|
| **Python** | Version 3.9 or higher ([Download](https://www.python.org/downloads/)) |
| **Google Chrome** | Latest version ([Download](https://www.google.com/chrome/)) |

### Installation

```bash
# 1. Clone the repository
git clone https://github.com/SoCloseSociety/GoogleMapScraper.git
cd GoogleMapScraper

# 2. (Recommended) Create a virtual environment
python -m venv venv

# Activate it:
# Windows:
venv\Scripts\activate
# macOS / Linux:
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt
```

### Usage

```bash
# Search by query
python main.py -q "restaurants+paris" -o my_results

# Search by full Google Maps URL
python main.py -u "https://www.google.com/maps/search/dentist+london/" -o dentists
```

The scraper will:
1. Open Chrome and search Google Maps
2. Scroll through all results and collect every place link
3. Visit each place and extract business details
4. Save everything to CSV files

#### All CLI Options

```bash
python main.py --help
```

| Option | Description | Default |
|--------|-------------|---------|
| `-u, --url URL` | Full Google Maps search URL | None |
| `-q, --query QUERY` | Search query (spaces replaced with +) | None |
| `-o, --output NAME` | Base name for output CSV files | `output` |
| `--headless` | Run Chrome without visible browser window | Off |
| `--links-only` | Only collect links, skip detail extraction | Off |
| `--from-links CSV` | Skip link collection, extract from existing CSV | None |
| `--no-website` | Skip visiting business websites (emails, owner, socials) — faster | Off |

#### Examples

```bash
# Headless mode (no browser window)
python main.py -q "bakery+tokyo" -o bakeries --headless

# Collect links only (fast reconnaissance)
python main.py -q "gym+berlin" -o gyms --links-only

# Resume from a previous links file (skip Phase 1)
python main.py --from-links gyms_links.csv -o gyms
```

---

## Cover a whole city (area search)

Google Maps shows at most ~120 places per search. `--area` splits the city into map tiles, searches every
tile, and automatically splits crowded tiles into smaller ones, so you only type the city name:

```bash
python main.py -q "grocery store" --area "Mumbai" -o mumbai --headless

# several shop types in one run (repeat -q); results are merged and de-duplicated
python main.py -q "grocery store" -q "pharmacy" -q "kirana store" --area "Mumbai" -o mumbai --headless
```

- Put the shop type in `-q` and the place in `--area` (don't write "Mumbai" inside `-q`).
- Only places **inside the area's real boundary** (from OpenStreetMap) are collected, so neighbouring towns
  (e.g. Navi Mumbai, Thane) are left out and tiles outside the boundary are skipped. The log shows which boundary was
  used; if it says OpenStreetMap only has a *point*, use a more specific name or `--bbox`.
- Boundary not found or wrong? Draw your own at https://geojson.io, save it, and use `--polygon-file mumbai.geojson`.
- Every searched tile is logged to `<output>_tiles.csv` (results, places inside the tile, whether the list was cut short).
- Works for any city, district or state OpenStreetMap knows: `--area "Pune"`, `--area "Thane district"`.
- Many cities in one run: `--areas-file cities.txt` (one name per line).
- Your own region: `--bbox 18.89,72.77,19.27,72.99` (south,west,north,east).
- Interrupted? Run the same command again — it resumes from the last tile.
- Collect links first, scrape details later: add `--links-only`, then `--from-links mumbai_links.csv`.

| Option | Default | Meaning |
|--------|---------|---------|
| `--cell-km` | 3 | Starting tile size in km |
| `--split-threshold` | 25 | A tile with this many places *inside it* is split into 4 (Google pads results with places from outside the tile, so only places inside count) |
| `--max-depth` | 3 | Max splits per tile (3 -> ~600 m tiles) |

---

## How complete is my result? (recall check)

```bash
python main.py -q "stationery store" --area "Vile Parle" --links-only -o check_vp
python compare_links.py check_vp_links.csv mumbai_links.csv
```

Prints the share of a small, thoroughly searched neighbourhood that your big run also found. Try 2-3 neighbourhoods.
Different wordings find different places, so combine terms: `-q "stationery store" -q "stationary shop"` (many shops spell it "stationary").

---

## Stopped or crashed? Just continue

Both steps resume. Re-run the **same command** (or skip the slow link search with `--from-links`):

```bash
python main.py --from-links mumbai_links.csv -o mumbai --headless
```

Places already in `mumbai_details.csv` are skipped. If Chrome dies mid-run, a new one is started automatically,
and the run stops after 5 failures in a row instead of burning through the list. `--fresh` starts the details step over.

---

## How It Works

```
┌──────────────────────────────────────────────────────┐
│                    PHASE 1                           │
│         Scroll & Collect Place Links                 │
│                                                      │
│  Google Maps URL ──> Scroll feed ──> Extract /maps/  │
│                      place/ links ──> Save CSV       │
└──────────────────────┬───────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────┐
│                    PHASE 2                           │
│         Visit Each Place & Extract Details           │
│                                                      │
│  For each link ──> Load page ──> Parse HTML ──>      │
│  Extract name, address, phone, website, schedule     │
│  ──> Append to CSV (crash-safe)                      │
└──────────────────────────────────────────────────────┘
```

---

## Data Extracted

**From the Google Maps listing**

| Field | Description |
|-------|-------------|
| `name`, `category` | Business name and type |
| `rating`, `reviews`, `price_level` | Rating, review count, price level |
| `address`, `plus_code`, `latitude`, `longitude` | Location |
| `phone` | Phone number on the listing |
| `website` | Business website |
| `open_status`, `hours`, `schedule` | Open/closed now and the full weekly hours |
| `google_maps_url`, `place_id` | Link and Google's ID for the place |

**From the business's own website** (homepage + contact / about / team pages)

| Field | Description |
|-------|-------------|
| `shop_email` | Shop mailbox (info@, contact@, bookings@ ...) |
| `owner_email` | Email matching a found owner/founder name (blank if not clear) |
| `other_emails` | Any other published addresses |
| `owner_name`, `owner_title` | Owner / founder / manager named on the site |
| `website_phones`, `whatsapp` | Extra phone and WhatsApp numbers |
| `facebook`, `instagram`, `twitter`, `linkedin`, `youtube`, `tiktok` | The shop's own social profiles |

Owner and email fields are filled only when the business publishes them; many shops don't, so blanks are normal.
Use `--no-website` to skip the website step.

`best_contact` shows the best way to reach each business: email, else WhatsApp, else phone.

### Re-run the email step on an existing CSV

```bash
python add_emails.py my_results_details.csv -o my_results_emails.csv
python add_emails.py my_results_details.csv --render     # also uses Chrome for JavaScript-built sites
```

No Google Maps re-scrape needed. It also decodes Cloudflare-hidden emails and tries /contact, /about etc. when the site doesn't link them.

---

## Configuration

You can adjust these constants at the top of `main.py`:

| Constant | Default | Description |
|----------|---------|-------------|
| `DEFAULT_DELAY` | `(2, 4)` | Random delay range between requests (seconds) |
| `PAGE_LOAD_TIMEOUT` | `15` | Max wait for page elements (seconds) |
| `SCROLL_PAUSE` | `1.5` | Pause between scrolls (seconds) |
| `MAX_SCROLL_STALLS` | `15` | Stop scrolling after N stalls with no new links |

---

## Project Structure

```
GoogleMapScraper/
├── main.py              # Main scraper script
├── add_emails.py        # Re-run the email step on an existing CSV
├── compare_links.py     # Recall check against a small neighbourhood
├── grid.py              # Area tiling (geocoding + map tiles)
├── enrich.py            # Website enrichment (emails, owner, phones, socials)
├── requirements.txt     # Python dependencies
├── assets/
│   └── banner.svg       # Project banner
├── LICENSE              # MIT License
├── README.md            # This file
├── CONTRIBUTING.md      # Contribution guidelines
└── .gitignore           # Git ignore rules
```

---

## Troubleshooting

### Chrome driver issues

The scraper uses `webdriver-manager` to automatically download the correct ChromeDriver. If you encounter issues:

```bash
pip install --upgrade webdriver-manager
```

### No results found

If the scraper doesn't find any links:
1. Try using a full Google Maps URL with `-u` instead of a query
2. Make sure Chrome is up to date
3. Try without `--headless` to see what's happening

### Google Maps UI changes

Google occasionally updates its interface. If the scraper breaks:
1. Check the [Issues](https://github.com/SoCloseSociety/GoogleMapScraper/issues) page
2. Open a new issue with the error message

---

## FAQ

**Q: Is this free?**
A: Yes. Google Maps Scraper is 100% free and open source under the MIT license.

**Q: Do I need a Google Maps API key?**
A: No. This tool uses browser automation (Selenium), so no API key is needed.

**Q: How many businesses can I scrape?**
A: The tool scrapes all results returned by Google Maps for your search. Just be mindful of Google's usage policies.

**Q: Can I resume a failed scrape?**
A: Yes! Use `--from-links` to resume from a previously saved links CSV.

**Q: Does it work on Mac / Linux?**
A: Yes. Fully cross-platform on Windows, macOS, and Linux.

**Q: Can I run it without a browser window?**
A: Yes. Use `--headless` mode.

---

## Alternatives Comparison

| Feature | Google Maps Scraper | Manual Copy-Paste | Google Places API | Outscraper |
|---------|--------------------|--------------------|-------------------|------------|
| Price | **Free** | Free | $0.02/request | $2.50/1000 |
| Bulk extraction | Yes | No | Yes | Yes |
| Crash recovery | Yes | N/A | N/A | N/A |
| Open source | Yes | N/A | No | No |
| API key required | No | No | Yes | Yes |
| Cross-platform | Yes | Yes | Any | Web only |
| Headless mode | Yes | N/A | N/A | N/A |

---

## Contributing

Contributions are welcome! Please read the [Contributing Guide](CONTRIBUTING.md) before submitting a pull request.

---

## License

This project is licensed under the [MIT License](LICENSE).

---

## Disclaimer

This tool is provided for **educational and research purposes only**. Scraping Google Maps may violate Google's Terms of Service. You are solely responsible for how you use this tool. The authors are not responsible for any misuse or consequences arising from the use of this software.

---

<p align="center">
  <strong>If this project helps you, please give it a star!</strong><br>
  It helps others discover this tool.<br><br>
  <a href="https://github.com/SoCloseSociety/GoogleMapScraper">
    <img src="https://img.shields.io/github/stars/SoCloseSociety/GoogleMapScraper?style=for-the-badge&logo=github&color=575ECF" alt="Star this repo">
  </a>
</p>

<br>

<p align="center">
  <sub>Built with purpose by <a href="https://soclose.co"><strong>SoClose</strong></a> &mdash; Digital Innovation Through Automation & AI</sub><br>
  <sub>
    <a href="https://soclose.co">Website</a> &bull;
    <a href="https://linkedin.com/company/soclose-agency">LinkedIn</a> &bull;
    <a href="https://twitter.com/SoCloseAgency">Twitter</a> &bull;
    <a href="mailto:hello@soclose.co">Contact</a>
  </sub>
</p>
