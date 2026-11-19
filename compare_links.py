#!/usr/bin/env python3
"""
Measure how complete a big run is.

Search a few SMALL neighbourhoods the same way (one search finds nearly everything there),
then check how many of those places your big run also found:

    python main.py -q "stationery store" --area "Vile Parle" --links-only -o check_vileparle
    python compare_links.py check_vileparle_links.csv mumbai_links.csv

Recall of 90% means the big run found 9 out of 10 places in that neighbourhood.
Do this for 2-3 different neighbourhoods (one dense, one quiet) for a fair picture.
"""

import sys

import pandas as pd

from grid import place_key


def keys(path):
    df = pd.read_csv(path, dtype=str)
    col = "link" if "link" in df.columns else "google_maps_url"
    return {place_key(x) for x in df[col].dropna()}


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    ref, big = keys(sys.argv[1]), keys(sys.argv[2])
    hit = ref & big
    print(f"Reference area : {len(ref)} places")
    print(f"Found by big run: {len(hit)}   missed: {len(ref - big)}")
    if ref:
        print(f"Recall          : {len(hit) / len(ref):.0%}")


if __name__ == "__main__":
    main()
