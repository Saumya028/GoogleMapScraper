#!/usr/bin/env python3
"""
SoClose Google Maps Scraper — Light Edition
A lightweight, community-driven Google Maps data scraper.
https://github.com/SoCloseSociety/GoogleMapScraper

Usage:
    python main.py -q "restaurants+paris" -o results
    python main.py -u "https://www.google.com/maps/search/..." -o results
    python main.py --from-links results_links.csv -o results
"""

import argparse
import csv
import logging
import os
import sys
import time
import random
import socket
import re

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    WebDriverException,
)
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import pandas as pd

from enrich import best_contact, enrich_from_website


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_DELAY = (2, 4)          # Random delay range between requests (seconds)
PAGE_LOAD_TIMEOUT = 15          # Max wait for page elements (seconds)
SCROLL_PAUSE = 1.5              # Pause between scrolls (seconds)
MAX_SCROLL_STALLS = 15          # Stop scrolling after N stalls with no new links

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(levelname)s]  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("soclose-gmaps")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def check_internet(host="one.one.one.one", port=80, timeout=3):
    """Return True if we can reach the internet."""
    try:
        addr = socket.gethostbyname(host)
        conn = socket.create_connection((addr, port), timeout)
        conn.close()
        return True
    except OSError:
        return False


def create_driver(headless=False):
    """Create and return a configured Chrome WebDriver instance."""
    opts = Options()
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--lang=en")
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    opts.add_experimental_option(
        "excludeSwitches", ["enable-logging", "enable-automation"]
    )
    opts.add_experimental_option("useAutomationExtension", False)

    if headless:
        opts.add_argument("--headless=new")
        opts.add_argument("--window-size=1920,1080")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=opts)

    if not headless:
        driver.maximize_window()

    driver.set_page_load_timeout(30)
    return driver


def random_delay(bounds=DEFAULT_DELAY):
    """Sleep for a random duration within *bounds*."""
    time.sleep(random.uniform(*bounds))


# ---------------------------------------------------------------------------
# Phase 1 — Collect place links
# ---------------------------------------------------------------------------

def collect_links(driver, url):
    """Scroll through Google Maps results and collect all place links.

    Returns a sorted list of unique Google Maps place URLs.
    """
    log.info("Phase 1 — Collecting place links ...")
    driver.get(url + "&hl=en")

    # Wait for the results feed to load
    try:
        WebDriverWait(driver, PAGE_LOAD_TIMEOUT).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'div[role="feed"]'))
        )
    except TimeoutException:
        log.error("Timed out waiting for the results feed. Check your URL.")
        return []

    feed = driver.find_element(By.CSS_SELECTOR, 'div[role="feed"]')

    links = set()
    stall_count = 0

    while True:
        prev_count = len(links)

        # Parse current page source
        soup = BeautifulSoup(driver.page_source, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/maps/place/" in href:
                links.add(href)

        new_count = len(links)
        log.info(f"  Links found: {new_count}")

        if new_count == prev_count:
            stall_count += 1
            if stall_count >= MAX_SCROLL_STALLS:
                log.info("  No new results — stopping scroll.")
                break
        else:
            stall_count = 0

        # Scroll the feed container
        driver.execute_script(
            "arguments[0].scrollTop = arguments[0].scrollHeight", feed
        )
        time.sleep(SCROLL_PAUSE)

    log.info(f"Phase 1 complete — {len(links)} links collected.")
    return sorted(links)


# ---------------------------------------------------------------------------
# Phase 2 — Extract business details
# ---------------------------------------------------------------------------

COLUMNS = [
    "name", "category", "rating", "reviews", "price_level",
    "address", "plus_code", "latitude", "longitude",
    "phone", "website_phones", "whatsapp", "website",
    "shop_email", "owner_email", "other_emails",
    "owner_name", "owner_title",
    "open_status", "hours", "schedule",
    "facebook", "instagram", "twitter", "linkedin", "youtube", "tiktok",
    "google_maps_url", "place_id", "best_contact",
]
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _parse_hours(soup):
    """Return the weekly opening hours as 'Monday 9 AM-5 PM | Tuesday ...'."""
    rows = []
    for tr in soup.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 2:
            continue
        day = cells[0].get_text(" ", strip=True)
        if day.lower() not in DAYS:
            continue
        times = " ".join(cells[1].get_text(" ", strip=True).split())
        times = times.replace("\u202f", " ").replace("\u2009", " ")
        rows.append(f"{day} {times}")
    return " | ".join(rows) or None


def extract_details(driver, link):
    """Visit a single place link and extract business information from Google Maps.

    Returns a dict (see COLUMNS) or an empty dict on failure.
    """
    driver.get(link + "&hl=en")

    try:
        WebDriverWait(driver, PAGE_LOAD_TIMEOUT).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "h1"))
        )
    except TimeoutException:
        log.warning(f"  Timeout loading: {link[:80]}...")
        return {}

    soup = BeautifulSoup(driver.page_source, "html.parser")
    data = dict.fromkeys(COLUMNS)
    data["google_maps_url"] = link

    h1 = soup.find("h1")
    if h1:
        data["name"] = h1.get_text(strip=True)

    # --- Core info rows (data-item-id is far more stable than CSS classes)
    btn = soup.find(attrs={"data-item-id": "address"})
    if btn:
        data["address"] = btn.get_text(strip=True)

    a_tag = soup.find("a", attrs={"data-item-id": "authority"})
    if a_tag and a_tag.get("href"):
        data["website"] = a_tag["href"]

    phone_el = soup.find(attrs={"data-item-id": re.compile(r"^phone:tel:")})
    if phone_el:
        data["phone"] = phone_el["data-item-id"].split("tel:", 1)[1] or phone_el.get_text(strip=True)

    plus = soup.find(attrs={"data-item-id": "oloc"})
    if plus:
        data["plus_code"] = plus.get_text(strip=True)

    # --- Category
    cat = soup.find("button", attrs={"jsaction": re.compile("category")})
    if cat:
        data["category"] = cat.get_text(strip=True)

    # --- Rating / reviews / price
    for el in soup.find_all(attrs={"aria-label": True}):
        label = el["aria-label"]
        m = re.match(r"^\s*([\d.,]+)\s+stars?", label)
        if m and not data["rating"]:
            data["rating"] = m.group(1).replace(",", ".")
        m = re.match(r"^\s*([\d,\.]+)\s+reviews?", label)
        if m and not data["reviews"]:
            data["reviews"] = re.sub(r"\D", "", m.group(1))
        if label.startswith("Price:") and not data["price_level"]:
            data["price_level"] = label.split(":", 1)[1].strip()

    # --- Opening hours
    data["hours"] = _parse_hours(soup)
    for el in soup.find_all(attrs={"aria-label": True}):
        label = el["aria-label"]
        if "opening hours" in label.lower() or "open hours" in label.lower():
            parts = label.split(".")
            if parts and parts[0]:
                data["schedule"] = parts[0].replace(",", " -> ")
                break
    status = soup.find(string=re.compile(r"^\s*(Open|Closed|Opens|Closes|Temporarily closed|Permanently closed)\b"))
    if status:
        data["open_status"] = " ".join(status.split())

    # --- Coordinates and place id come from the URL
    url = driver.current_url
    m = re.search(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", url) or re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", url)
    if m:
        data["latitude"], data["longitude"] = m.group(1), m.group(2)
    m = re.search(r"!1s(0x[0-9a-f]+:0x[0-9a-f]+)", link + url)
    if m:
        data["place_id"] = m.group(1)

    return data


def _render(driver, url):
    """Load a page in the browser (for JavaScript-built sites) and return its HTML."""
    try:
        driver.get(url)
        time.sleep(2.5)
        return driver.page_source
    except WebDriverException:
        return None


def scrape_details(driver, links, output_path, find_extra=True):
    """Iterate through all links, extract details, and save to CSV.

    Progress is saved after each extraction (crash-safe).
    """
    log.info(f"Phase 2 — Extracting details for {len(links)} places ...")
    results = []

    for i, link in enumerate(links, 1):
        log.info(f"  [{i}/{len(links)}] Scraping ...")

        try:
            data = extract_details(driver, link)
            if data and data.get("name"):
                if find_extra and data.get("website"):
                    data.update({k: v for k, v in enrich_from_website(data["website"], render=lambda u: _render(driver, u)).items() if v})
                data["best_contact"] = best_contact(data)
                results.append(data)
                log.info(
                    f"    -> {data['name']}  |  phone: {data.get('phone') or '-'}"
                    f"  |  email: {data.get('shop_email') or data.get('owner_email') or '-'}"
                    f"  |  owner: {data.get('owner_name') or '-'}"
                )
            else:
                log.warning("    -> No data extracted")
        except WebDriverException as exc:
            log.error(f"    -> WebDriver error: {exc}")

        # Save progress after each extraction (crash-safe)
        if results:
            df = pd.DataFrame(results).reindex(columns=COLUMNS)
            df.to_csv(output_path, index=False, encoding="utf-8-sig")

        random_delay()

    log.info(f"Phase 2 complete — {len(results)} businesses saved to {output_path}")
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

BANNER = r"""
  ____         ____ _
 / ___|  ___  / ___| | ___  ___  ___
 \___ \ / _ \| |   | |/ _ \/ __|/ _ \
  ___) | (_) | |___| | (_) \__ \  __/
 |____/ \___/ \____|_|\___/|___/\___|
       Google Maps Scraper — Light
"""


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="SoClose Google Maps Scraper — extract business data from Google Maps.",
        epilog="Example:  python main.py -q 'restaurants+paris' -o results",
    )
    parser.add_argument(
        "-u", "--url",
        help="Full Google Maps search URL.",
    )
    parser.add_argument(
        "-q", "--query",
        help="Search query (spaces replaced with +). Builds the URL for you.",
    )
    parser.add_argument(
        "-o", "--output",
        default="output",
        help="Base name for output CSV files (default: output).",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run Chrome in headless mode (no visible browser window).",
    )
    parser.add_argument(
        "--links-only",
        action="store_true",
        help="Only collect links — skip detail extraction.",
    )
    parser.add_argument(
        "--from-links",
        metavar="CSV",
        help="Skip link collection and extract details from an existing links CSV.",
    )
    parser.add_argument(
        "--no-website",
        action="store_true",
        help="Skip visiting business websites for emails / owner / socials (faster).",
    )
    return parser.parse_args()


def main():
    """Entry point."""
    args = parse_args()
    print(BANNER)

    # --- Resolve search URL ---------------------------------------------------
    if args.from_links:
        if not os.path.isfile(args.from_links):
            log.error(f"File not found: {args.from_links}")
            sys.exit(1)
        search_url = None
    elif args.url:
        search_url = args.url
    elif args.query:
        query = args.query.replace(" ", "+")
        search_url = f"https://www.google.com/maps/search/{query}/"
    else:
        log.error("Provide either --url or --query (see --help).")
        sys.exit(1)

    # --- Internet check -------------------------------------------------------
    if not check_internet():
        log.error("No internet connection detected. Aborting.")
        sys.exit(1)

    log.info("Starting Chrome driver ...")
    driver = create_driver(headless=args.headless)

    try:
        # Phase 1 — Collect links
        if args.from_links:
            log.info(f"Loading links from {args.from_links}")
            with open(args.from_links, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                links = [
                    row[0] for row in reader
                    if row and "/maps/place/" in row[0]
                ]
        else:
            links = collect_links(driver, search_url)
            if links:
                links_csv = f"{args.output}_links.csv"
                pd.DataFrame({"link": links}).to_csv(links_csv, index=False)
                log.info(f"Links saved to {links_csv}")

        if not links:
            log.warning("No links found. Nothing to scrape.")
            return

        log.info(f"Total links: {len(links)}")

        # Phase 2 — Extract details
        if not args.links_only:
            details_csv = f"{args.output}_details.csv"
            scrape_details(driver, links, details_csv, find_extra=not args.no_website)

    except KeyboardInterrupt:
        log.info("\nInterrupted by user. Progress has been saved.")
    finally:
        driver.quit()
        log.info("Browser closed. Done.")


if __name__ == "__main__":
    main()
