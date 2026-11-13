#!/usr/bin/env python3
"""
Re-run the website step on an existing details CSV (no Google Maps re-scrape).

    python add_emails.py my_results_details.csv -o my_results_emails.csv
    python add_emails.py my_results_details.csv --render      # also use Chrome for JS-heavy sites

Fills shop_email / owner_email / owner_name / socials for rows that have a website but
no email yet, and adds a `best_contact` column (email > WhatsApp > phone) so every
row shows its best way to be reached.
"""

import argparse
import logging
import time

import pandas as pd

from enrich import best_contact, enrich_from_website

logging.basicConfig(level=logging.INFO, format="%(asctime)s  [%(levelname)s]  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("add-emails")

EMAIL_COLS = ["shop_email", "owner_email", "other_emails"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", help="details CSV produced by main.py")
    ap.add_argument("-o", "--output", help="output CSV (default: <input>_emails.csv)")
    ap.add_argument("--render", action="store_true",
                    help="use Chrome for sites that need JavaScript (slower, finds more)")
    args = ap.parse_args()
    out_path = args.output or args.csv.rsplit(".", 1)[0] + "_emails.csv"

    df = pd.read_csv(args.csv, dtype=str, encoding="utf-8-sig")
    for col in EMAIL_COLS + ["owner_name", "owner_title", "website_phones", "whatsapp",
                             "facebook", "instagram", "twitter", "linkedin", "youtube", "tiktok"]:
        if col not in df:
            df[col] = None

    driver, render = None, None
    if args.render:
        from main import create_driver   # imported lazily: Selenium only needed here
        driver = create_driver(headless=True)

        def render(url):
            try:
                driver.get(url)
                time.sleep(2.5)
                return driver.page_source
            except Exception:
                return None

    todo = df[df["website"].notna() & ~df[EMAIL_COLS].notna().any(axis=1)]
    log.info(f"{len(todo)} rows have a website but no email yet.")
    try:
        for n, (idx, row) in enumerate(todo.iterrows(), 1):
            found = enrich_from_website(row["website"], render=render)
            for k, v in found.items():
                if v and pd.isna(df.at[idx, k]):
                    df.at[idx, k] = v
            got = found.get("shop_email") or found.get("owner_email") or found.get("other_emails")
            log.info(f"  [{n}/{len(todo)}] {row['name'][:40]:40s} -> {got or '-'}")
            df.to_csv(out_path, index=False, encoding="utf-8-sig")
    except KeyboardInterrupt:
        log.info("Interrupted — saving what we have.")
    finally:
        if driver:
            driver.quit()

    df["best_contact"] = df.apply(best_contact, axis=1)
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    has_email = df[EMAIL_COLS].notna().any(axis=1).sum()
    log.info(f"Done. {has_email}/{len(df)} rows have an email; "
             f"{df['best_contact'].notna().sum()}/{len(df)} have at least one contact. Saved: {out_path}")


if __name__ == "__main__":
    main()
