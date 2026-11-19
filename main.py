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
import json

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
import grid


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_DELAY = (2, 4)          # Random delay range between requests (seconds)
PAGE_LOAD_TIMEOUT = 15          # Max wait for page elements (seconds)
SCROLL_PAUSE = 1.5              # Pause between scrolls (seconds)
RESULT_CAP_HINT = 90            # Google lists ~120 max; at/above this a tile may be truncated
CELL_FEED_TIMEOUT = 8           # Grid mode: wait for a tile's results (empty tiles answer fast)
CELL_SCROLL_PAUSE = 1.0         # Grid mode: pause between scrolls
CELL_MAX_STALLS = 4             # Grid mode: stalls before giving up on a tile
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

END_OF_LIST = "reached the end of the list"


def _scroll_collect(driver, url, max_stalls, feed_timeout, scroll_pause):
    """Open a Maps search URL and scroll its result list to the end.

    Returns ({place_key: href}, ended). *ended* is True when Google showed its
    "reached the end of the list" marker, False if we gave up waiting for more.
    """
    driver.get(url if "hl=" in url else url + ("&" if "?" in url else "?") + "hl=en")

    try:
        WebDriverWait(driver, feed_timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'div[role="feed"]'))
        )
    except TimeoutException:
        # A search with exactly one hit opens that place directly (no list)
        if "/maps/place/" in driver.current_url:
            return {grid.place_key(driver.current_url): driver.current_url}, True
        return {}, True

    feed = driver.find_element(By.CSS_SELECTOR, 'div[role="feed"]')
    found = {}
    stalls = 0
    ended = False

    while True:
        prev = len(found)
        html = driver.page_source
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/maps/place/" in href:
                found.setdefault(grid.place_key(href), href)

        if END_OF_LIST in html:
            ended = True
            break
        if len(found) == prev:
            stalls += 1
            if stalls >= max_stalls:
                break
        else:
            stalls = 0

        # Scroll, then wait until the list actually grows (slow connections need longer
        # than a fixed pause, and giving up too early silently loses places).
        try:
            before = driver.execute_script("return arguments[0].scrollHeight", feed)
            driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight", feed)
            deadline = time.time() + scroll_pause * 3
            while time.time() < deadline:
                time.sleep(0.25)
                if driver.execute_script("return arguments[0].scrollHeight", feed) != before:
                    break
        except WebDriverException:
            time.sleep(scroll_pause)

    return found, ended


def collect_links(driver, url):
    """Scroll through one Google Maps search and collect its place links.

    Returns a sorted list of unique Google Maps place URLs. (Google shows at most
    ~120 places per search — use --area to cover a whole city.)
    """
    log.info("Phase 1 — Collecting place links ...")
    found, _ = _scroll_collect(driver, url, MAX_SCROLL_STALLS, PAGE_LOAD_TIMEOUT, SCROLL_PAUSE)
    if not found:
        log.error("No results found. Check your URL.")
    log.info(f"Phase 1 complete — {len(found)} links collected.")
    return sorted(found.values())


def _save_links(links, path):
    pd.DataFrame({"link": sorted(links.values())}).to_csv(path, index=False)


def _log_tile(path, label, cell, results, in_tile, new, ended, split, total):
    """Append one row per searched tile (lets you see exactly how the area was covered)."""
    new_file = not os.path.isfile(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["search", "lat", "lng", "tile_km", "depth", "results", "inside_tile",
                        "new_places", "list_ended", "split", "total_places"])
        w.writerow([label, f"{cell['lat']:.5f}", f"{cell['lng']:.5f}", f"{cell['km']:.3f}", cell["depth"],
                    results, in_tile, new, int(ended), int(split), total])


def collect_links_grid(driver, query, bbox, label, params, state_path, links_csv, polys=None):
    """Cover *bbox* with map tiles, search *query* in each, return {place_key: href}.

    Tiles that return a "full" result list are split into 4 smaller tiles.
    Progress is checkpointed after every tile, so an interrupted run resumes.
    """
    cell_km, threshold, max_depth = params["cell_km"], params["split_threshold"], params["max_depth"]
    ident = {"v": 3, "query": query, "bbox": list(bbox),
             "poly": sum(len(r) for p in polys for r in p) if polys else 0, **params}

    state = None
    if os.path.isfile(state_path):
        try:
            with open(state_path, encoding="utf-8") as f:
                saved = json.load(f)
            if saved.get("ident") == ident:
                state = saved
                log.info(f"[{label}] Resuming: {len(saved['queue'])} tiles left, "
                         f"{len(saved['links'])} places so far.")
        except (OSError, ValueError):
            pass
    if state is None:
        cells = [c for c in grid.make_cells(bbox, cell_km) if grid.cell_touches_area(c, polys)]
        state = {"ident": ident, "queue": cells, "links": {}, "done": 0}

    queue, links = state["queue"], state["links"]
    tiles_csv = links_csv.replace("_links.csv", "") + "_tiles.csv"

    while queue:
        cell = queue.pop(0)
        found, ended = _scroll_collect(
            driver, grid.cell_url(query, cell), CELL_MAX_STALLS, CELL_FEED_TIMEOUT, CELL_SCROLL_PAUSE
        )
        new = 0
        for key, href in found.items():
            c = grid.link_coords(href)
            if key in links or not grid.in_bbox(c, bbox, margin_km=0.5) or not grid.point_in_area(c, polys):
                continue
            links[key] = href
            new += 1

        # Google pads a sparse tile's list with places from OUTSIDE the tile, so the raw
        # count says nothing about crowding. Count only places that lie inside the tile.
        coords = [grid.link_coords(h) for h in found.values()]
        known = sum(c is not None for c in coords)
        in_tile = sum(grid.in_cell(c, cell) for c in coords)
        if known >= 0.5 * len(found):
            crowded = len(found) >= RESULT_CAP_HINT and in_tile >= threshold
        else:                                   # no coordinates in the links: fall back to raw count
            crowded = len(found) >= RESULT_CAP_HINT
        split = crowded and cell["depth"] < max_depth
        if split:
            queue[:0] = [c for c in grid.subdivide(cell) if grid.cell_touches_area(c, polys)]
        state["done"] += 1

        log.info(
            f"  [{label}] tile {state['done']} ({cell['km']:.2f} km): {len(found)} results, "
            f"{in_tile} inside tile{'' if ended else ' (list cut short!)'}, {new} new, total {len(links)}, {len(queue)} queued" + ("  -> splitting" if split else "")
        )

        _log_tile(tiles_csv, label, cell, len(found), in_tile, new, ended, split, len(links))
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f)
        _save_links(links, links_csv)
        random_delay((0.5, 1.5))

    return links


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
        m = re.search(r"([\d,\.]+)\s+reviews?\b", label, re.I)
        if m and not data["reviews"]:
            data["reviews"] = re.sub(r"\D", "", m.group(1))
        if label.startswith("Price:") and not data["price_level"]:
            data["price_level"] = label.split(":", 1)[1].strip()

    if not data["reviews"]:      # fallback: the "(1,234)" next to the rating stars
        for span in soup.find_all("span", attrs={"aria-label": True}):
            m = re.fullmatch(r"\(?([\d,]+)\)?", span.get_text(strip=True))
            if m and "star" not in span["aria-label"].lower() and m.group(1).replace(",", "").isdigit():
                data["reviews"] = m.group(1).replace(",", "")
                break

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


class BrowserSession:
    """Holds the Chrome driver so it can be replaced if the browser dies mid-run."""

    def __init__(self, driver, headless):
        self.driver = driver
        self.headless = headless

    def restart(self):
        try:
            self.driver.quit()
        except Exception:
            pass
        log.warning("  Browser died — starting a fresh Chrome ...")
        self.driver = create_driver(headless=self.headless)

    def quit(self):
        try:
            self.driver.quit()
        except Exception:
            pass


BROWSER_DEAD_HINTS = ("invalid session id", "disconnected", "no such window", "chrome not reachable",
                      "target window already closed", "session deleted", "connection refused",
                      "max retries exceeded", "unable to receive message")
MAX_CONSECUTIVE_FAILURES = 5


def _browser_died(exc):
    return any(h in str(exc).lower() for h in BROWSER_DEAD_HINTS)


def _load_done(output_path):
    """Rows already saved in a previous run + the set of place ids they cover."""
    if not os.path.isfile(output_path):
        return [], set()
    try:
        df = pd.read_csv(output_path, dtype=str, encoding="utf-8-sig")
    except (OSError, ValueError, pd.errors.EmptyDataError):
        return [], set()
    rows = [{k: (None if pd.isna(v) else v) for k, v in r.items()}
            for r in df.reindex(columns=COLUMNS).to_dict("records")]
    done = set()
    for r in rows:
        if r.get("place_id"):
            done.add(r["place_id"])
        if r.get("google_maps_url"):
            done.add(grid.place_key(r["google_maps_url"]))
    return rows, done


def scrape_details(session, links, output_path, find_extra=True, resume=True, polys=None):
    """Iterate through all links, extract details, and save to CSV.

    * Resumes: places already in *output_path* are skipped (use resume=False to start over).
    * Crash-safe: the CSV is rewritten after every place.
    * If Chrome dies, a new one is started and the place is retried.
    """
    results, done = _load_done(output_path) if resume else ([], set())
    if polys and results:                       # drop carried-over rows that lie outside the area
        def inside(r):
            try:
                return grid.point_in_area((float(r["latitude"]), float(r["longitude"])), polys)
            except (TypeError, ValueError):
                return True
        kept = [r for r in results if inside(r)]
        if len(kept) < len(results):
            log.info(f"Removed {len(results) - len(kept)} saved places that lie outside the area.")
            results = kept
            pd.DataFrame(results).reindex(columns=COLUMNS).to_csv(output_path, index=False, encoding="utf-8-sig")
    todo = [l for l in links if grid.place_key(l) not in done]
    if results:
        log.info(f"Resuming: {len(results)} places already saved in {output_path}, "
                 f"{len(todo)} of {len(links)} left.")
    log.info(f"Phase 2 — Extracting details for {len(todo)} places ...")

    failures = 0
    for i, link in enumerate(todo, 1):
        log.info(f"  [{i}/{len(todo)}] Scraping ...")
        data = None
        for attempt in (1, 2):
            try:
                data = extract_details(session.driver, link)
                if data and data.get("name") and find_extra and data.get("website"):
                    data.update({k: v for k, v in enrich_from_website(
                        data["website"], render=lambda u: _render(session.driver, u)).items() if v})
                break
            except WebDriverException as exc:
                if attempt == 1 and _browser_died(exc):
                    try:
                        session.restart()
                        continue
                    except WebDriverException as exc2:
                        log.error(f"    -> Could not restart Chrome: {exc2}")
                log.error(f"    -> WebDriver error: {str(exc).splitlines()[0]}")
                break

        if data and data.get("name"):
            failures = 0
            data["best_contact"] = best_contact(data)
            results.append(data)
            log.info(
                f"    -> {data['name']}  |  phone: {data.get('phone') or '-'}"
                f"  |  email: {data.get('shop_email') or data.get('owner_email') or '-'}"
                f"  |  owner: {data.get('owner_name') or '-'}"
            )
            pd.DataFrame(results).reindex(columns=COLUMNS).to_csv(output_path, index=False, encoding="utf-8-sig")
        else:
            failures += 1
            log.warning("    -> No data extracted")
            if failures >= MAX_CONSECUTIVE_FAILURES:
                log.error(f"{failures} failures in a row — stopping so nothing is wasted. "
                          f"Progress is saved; re-run the same command to continue.")
                break

        random_delay()

    log.info(f"Phase 2 finished — {len(results)} businesses saved in {output_path}")
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
        "-q", "--query", action="append",
        help="Search term. Repeat for several: -q 'grocery store' -q pharmacy",
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
        "--fresh",
        action="store_true",
        help="Start the details step from scratch instead of continuing the existing details CSV.",
    )
    parser.add_argument(
        "--no-website",
        action="store_true",
        help="Skip visiting business websites for emails / owner / socials (faster).",
    )
    g = parser.add_argument_group("area search (cover a whole city / district)")
    g.add_argument("--area", help="Place name to cover completely, e.g. 'Mumbai'. "
                   "Use with -q (do NOT put the place name inside -q).")
    g.add_argument("--areas-file", metavar="TXT", help="Text file with one area per line.")
    g.add_argument("--polygon-file", metavar="GEOJSON",
                   help="Use your own boundary from a GeoJSON file (draw one at geojson.io).")
    g.add_argument("--bbox", metavar="S,W,N,E",
                   help="Cover an explicit box: south,west,north,east (decimal degrees).")
    g.add_argument("--cell-km", type=float, default=3.0,
                   help="Starting tile size in km (default 3). Smaller = more thorough, slower.")
    g.add_argument("--split-threshold", type=int, default=25,
                   help="Split a tile into 4 when at least this many results lie INSIDE it (default 25).")
    g.add_argument("--max-depth", type=int, default=3,
                   help="How many times a tile may be split (default 3 -> ~375 m tiles).")
    g.add_argument("--yes", action="store_true", help="Don't ask for confirmation on very large areas.")
    return parser.parse_args()


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "area"


def resolve_areas(args):
    """Turn --area / --areas-file / --bbox into [(label, bbox, polygons_or_None)]. Exits on failure."""
    names = []
    if args.area:
        names.append(args.area)
    if args.areas_file:
        with open(args.areas_file, encoding="utf-8") as f:
            names += [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]

    areas = []
    if args.bbox:
        try:
            areas.append(("custom-bbox", grid.parse_bbox(args.bbox), None))
        except ValueError as exc:
            log.error(f"--bbox: {exc}")
            sys.exit(1)
    if args.polygon_file:
        try:
            bbox, polys = grid.load_polygon_file(args.polygon_file)
        except (OSError, ValueError) as exc:
            log.error(f"--polygon-file: {exc}")
            sys.exit(1)
        log.info(f"Using your boundary from {args.polygon_file}")
        areas.append((os.path.splitext(os.path.basename(args.polygon_file))[0], bbox, polys))
    for i, name in enumerate(names):
        if i:
            time.sleep(1.1)                      # Nominatim allows ~1 request/second
        found = grid.geocode_area(name)
        if not found:
            log.error(f"Could not find the area '{name}'. Check the spelling, "
                      f"or pass --bbox south,west,north,east instead.")
            sys.exit(1)
        if found["exact"]:
            log.info(f"Area '{name}' -> using OpenStreetMap boundary: {found['name']}")
            log.info("  Only places inside that boundary are collected. If the name above is not what "
                     "you meant, try a more specific --area.")
        else:
            log.warning(f"Area '{name}' -> OpenStreetMap only has a POINT for it ({found['name']}), so the "
                        f"search box is a rough square and may include neighbouring towns or miss edges. "
                        f"Try a more specific name, draw your own boundary (--polygon-file), or use --bbox.")
            if not args.yes and input("Search that rough box anyway? [y/N] ").strip().lower() != "y":
                sys.exit(0)
        areas.append((name, found["bbox"], found["polys"]))
    return areas


def main():
    """Entry point."""
    args = parse_args()
    print(BANNER)

    grid_mode = bool(args.area or args.areas_file or args.bbox or args.polygon_file)

    # --- Resolve what to search ----------------------------------------------
    areas = []
    search_urls = []
    if args.from_links:
        if not os.path.isfile(args.from_links):
            log.error(f"File not found: {args.from_links}")
            sys.exit(1)
    elif grid_mode:
        if not args.query:
            log.error("--area / --areas-file / --bbox need a search term, e.g. -q 'grocery store'.")
            sys.exit(1)
        areas = resolve_areas(args)
        total_cells = 0
        for label, bbox, polys in areas:
            n = sum(grid.cell_touches_area(c, polys) for c in grid.make_cells(bbox, args.cell_km))
            total_cells += n
            log.info(f"Area '{label}': box S{bbox[0]:.3f} W{bbox[1]:.3f} N{bbox[2]:.3f} E{bbox[3]:.3f} "
                     f"-> {n} starting tiles of {args.cell_km:g} km")
        log.info(f"Rough time: ~{total_cells * 0.5:.0f}-{total_cells * 1.5:.0f} min for link collection "
                 f"(more if tiles get split).")
        if total_cells > 1500 and not args.yes:
            if input("That's a very large area. Continue? [y/N] ").strip().lower() != "y":
                sys.exit(0)
    elif args.url:
        search_urls = [args.url]
    elif args.query:
        search_urls = [f"https://www.google.com/maps/search/{q.replace(' ', '+')}/" for q in args.query]
    else:
        log.error("Provide --query with --area (whole city), or --url / --query (see --help).")
        sys.exit(1)

    # --- Internet check -------------------------------------------------------
    if not check_internet():
        log.error("No internet connection detected. Aborting.")
        sys.exit(1)

    log.info("Starting Chrome driver ...")
    driver = create_driver(headless=args.headless)
    session = BrowserSession(driver, args.headless)

    try:
        links_csv = f"{args.output}_links.csv"

        # Phase 1 — Collect links
        if args.from_links:
            log.info(f"Loading links from {args.from_links}")
            with open(args.from_links, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                links = [
                    row[0] for row in reader
                    if row and "/maps/place/" in row[0]
                ]
        elif grid_mode:
            log.info("Phase 1 — Covering the area tile by tile ...")
            params = {"cell_km": args.cell_km, "split_threshold": args.split_threshold,
                      "max_depth": args.max_depth}
            merged = {}
            for label, bbox, polys in areas:
                for q in args.query:
                    state_path = f"{args.output}_grid_{_slug(label)}_{_slug(q)}.json"
                    merged.update(collect_links_grid(driver, q, bbox, f"{label} / {q}", params,
                                                     state_path, links_csv, polys))
                    _save_links(merged, links_csv)
            links = sorted(merged.values())
            log.info(f"Phase 1 complete — {len(links)} unique places. Saved to {links_csv}")
        else:
            found = {}
            for url in search_urls:
                for href in collect_links(driver, url):
                    found.setdefault(grid.place_key(href), href)
            links = sorted(found.values())
            if links:
                pd.DataFrame({"link": links}).to_csv(links_csv, index=False)
                log.info(f"Links saved to {links_csv}")

        if not links:
            log.warning("No links found. Nothing to scrape.")
            return

        log.info(f"Total links: {len(links)}")

        # Phase 2 — Extract details
        if not args.links_only:
            details_csv = f"{args.output}_details.csv"
            all_polys = None
            if grid_mode and areas and all(p for _, _, p in areas):
                all_polys = [poly for _, _, p in areas for poly in p]
            scrape_details(session, links, details_csv, find_extra=not args.no_website,
                           resume=not args.fresh, polys=all_polys)

    except KeyboardInterrupt:
        log.info("\nInterrupted by user. Progress has been saved (re-run the same command to resume).")
    finally:
        session.quit()
        log.info("Browser closed. Done.")


if __name__ == "__main__":
    main()
