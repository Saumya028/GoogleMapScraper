"""
Website enrichment for the Google Maps scraper.

Given a business website, visit the homepage plus contact / about / team pages
and collect publicly published contact details:

    emails (shop + owner), phone numbers, WhatsApp, social profiles,
    owner / manager / founder names.

Only information the business itself publishes on its own website is collected.
"""

import json
import re
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

REQUEST_TIMEOUT = 10
MAX_EXTRA_PAGES = 6  # contact / about / team pages checked besides the homepage

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en",
}

# Pages worth visiting (matched against link path + link text)
PAGE_HINTS = (
    "contact", "about", "team", "our-story", "story", "who-we-are", "reach",
    "support", "impressum", "kontakt", "contacto", "owner", "founder", "people",
)

# Tried when the homepage has no link to a contact page
DEFAULT_PATHS = ("/contact", "/contact-us", "/contact-us/", "/contactus", "/about", "/about-us")

# Websites that are never the business's own site
SKIP_DOMAINS = (
    "facebook.com", "instagram.com", "twitter.com", "x.com", "linkedin.com",
    "youtube.com", "tiktok.com", "wa.me", "whatsapp.com", "t.me", "google.com",
    "goo.gl", "maps.app.goo.gl",
)

SOCIAL_DOMAINS = {
    "facebook": ("facebook.com", "fb.com"),
    "instagram": ("instagram.com",),
    "twitter": ("twitter.com", "x.com"),
    "linkedin": ("linkedin.com",),
    "youtube": ("youtube.com",),
    "tiktok": ("tiktok.com",),
}
# Share / widget URLs that are not the business's own profile
SOCIAL_NOISE = ("/sharer", "/share", "/intent/", "/plugins/", "/dialog/", "/tr?", "/login")

# --------------------------------------------------------------------------- emails

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
JUNK_EMAIL_PARTS = (
    "example.com", "domain.com", "email.com", "yourdomain", "yourname", "sentry.",
    "wixpress.com", "godaddy.com", "schema.org", "w3.org", "u003e", "no-reply",
    "noreply", "donotreply",
)
JUNK_EMAIL_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js")
# Role mailboxes — these belong to the shop, not to a specific person
GENERIC_LOCALS = {
    "info", "contact", "hello", "hi", "hey", "sales", "support", "admin", "office",
    "enquiry", "enquiries", "inquiry", "inquiries", "mail", "email", "care",
    "booking", "bookings", "reservations", "reservation", "orders", "order",
    "service", "services", "team", "help", "store", "shop", "accounts", "billing",
    "marketing", "hr", "jobs", "careers", "press", "feedback", "customercare",
    "reception", "general", "webmaster", "manager",
}

# --------------------------------------------------------------------------- phones

PHONE_RE = re.compile(r"(?<![\w/])(\+?\d[\d\s().\-]{7,18}\d)(?![\w])")

# --------------------------------------------------------------------------- owners

TITLE_WORDS = (
    "owner", "co-owner", "proprietor", "founder", "co-founder", "managing director",
    "general manager", "manager", "director", "chef-owner", "head chef", "ceo",
    "principal", "partner",
)
_TITLES = "|".join(re.escape(t) for t in sorted(TITLE_WORDS, key=len, reverse=True))
_NAME = r"([A-Z][a-zA-Z'\-]+(?:\s+[A-Z][a-zA-Z'\-]+){1,2})"
OWNER_PATTERNS = [
    # "Owner: Jane Doe"  /  "Founder - Jane Doe"
    re.compile(rf"\b({_TITLES})\b\s*[:\-–—|]\s*{_NAME}", re.I),
    # "Jane Doe, Owner"  /  "Jane Doe - Founder"
    re.compile(rf"{_NAME}\s*[,\-–—|(]\s*({_TITLES})\b", re.I),
    # "Founded by Jane Doe" / "Owned and operated by Jane Doe" / "Run by"
    re.compile(rf"\b(?:founded|owned|started|opened|managed|run|operated)(?:\s+and\s+\w+)?\s+by\s+{_NAME}"),
    # "Meet Jane Doe" / "Hi, I'm Jane Doe, the owner"
    re.compile(rf"\bI(?:'|’)?m\s+{_NAME}\s*,?\s*(?:the\s+)?({_TITLES})\b", re.I),
]
# Capitalised words that look like names but are not
NAME_STOPWORDS = {
    "contact", "about", "our", "the", "your", "team", "us", "home", "menu", "read",
    "more", "learn", "privacy", "policy", "terms", "get", "call", "email", "phone",
    "address", "opening", "hours", "open", "closed", "monday", "tuesday", "wednesday",
    "thursday", "friday", "saturday", "sunday", "and", "for", "with", "services",
    "service", "restaurant", "cafe", "hotel", "shop", "store", "clinic", "studio",
}


# =========================================================================== helpers

def _fetch(url):
    try:
        r = requests.get(url, headers=HTTP_HEADERS, timeout=REQUEST_TIMEOUT)
        if r.status_code == 200 and "html" in r.headers.get("Content-Type", "").lower():
            return r.text
    except requests.RequestException:
        pass
    return None


def _clean_email(raw):
    email = unquote(raw).strip().strip(".,;:<>()[]\"'").lower()
    if not EMAIL_RE.fullmatch(email):
        return None
    if email.endswith(JUNK_EMAIL_SUFFIXES):
        return None
    if any(part in email for part in JUNK_EMAIL_PARTS):
        return None
    return email


def _decode_cfemail(hexstr):
    """Decode Cloudflare's email-protection hex string."""
    try:
        key = int(hexstr[:2], 16)
        return "".join(chr(int(hexstr[i:i + 2], 16) ^ key) for i in range(2, len(hexstr), 2))
    except ValueError:
        return None


def _clean_phone(raw):
    digits = re.sub(r"\D", "", raw)
    if not 8 <= len(digits) <= 15:
        return None
    if len(set(digits)) <= 2:           # 0000000000, 1111111111
        return None
    return re.sub(r"\s+", " ", raw).strip()


def _valid_name(name):
    parts = name.split()
    if not 2 <= len(parts) <= 3:
        return False
    return not any(p.lower().strip(".,") in NAME_STOPWORDS for p in parts)


def _title_case(s):
    return " ".join(w.capitalize() for w in s.split())


# =========================================================================== per-page

def parse_page(html):
    """Extract everything useful from one HTML page."""
    soup = BeautifulSoup(html, "html.parser")
    out = {"emails": [], "phones": [], "whatsapp": [], "social": {}, "owners": []}

    # --- Cloudflare "email protected" placeholders (very common on small-business sites)
    for el in soup.find_all(attrs={"data-cfemail": True}):
        e = _clean_email(_decode_cfemail(el["data-cfemail"]) or "")
        if e:
            out["emails"].append(e)
    for a in soup.find_all("a", href=True):
        if "/cdn-cgi/l/email-protection#" in a["href"]:
            e = _clean_email(_decode_cfemail(a["href"].split("#", 1)[1]) or "")
            if e:
                out["emails"].append(e)

    # --- links: mailto / tel / whatsapp / social
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        low = href.lower()
        if low.startswith("mailto:"):
            for piece in re.split(r"[,;]", href[7:].split("?")[0]):
                e = _clean_email(piece)
                if e:
                    out["emails"].append(e)
        elif low.startswith("tel:"):
            p = _clean_phone(unquote(href[4:]))
            if p:
                out["phones"].append(p)
        elif "wa.me/" in low or "api.whatsapp.com" in low or "whatsapp.com/send" in low:
            m = re.search(r"(?:wa\.me/|phone=)(\+?\d{8,15})", href)
            if m:
                out["whatsapp"].append("+" + m.group(1).lstrip("+"))
        else:
            host = urlparse(href).netloc.lower().replace("www.", "")
            if host and not any(n in low for n in SOCIAL_NOISE):
                for name, domains in SOCIAL_DOMAINS.items():
                    if any(host == d or host.endswith("." + d) for d in domains):
                        path = urlparse(href).path.strip("/")
                        if path:                       # skip bare "facebook.com"
                            out["social"].setdefault(name, href.split("?")[0])

    # --- JSON-LD structured data (best source for phone / email / founder)
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (ValueError, TypeError):
            continue
        _walk_jsonld(data, out)

    # --- visible text
    text = soup.get_text(" ")
    text = re.sub(r"\s*[\[\(\{]\s*at\s*[\]\)\}]\s*", "@", text, flags=re.I)
    text = re.sub(r"\s*[\[\(\{]\s*dot\s*[\]\)\}]\s*", ".", text, flags=re.I)
    text = re.sub(r"\s+", " ", text)

    for m in EMAIL_RE.findall(text):
        e = _clean_email(m)
        if e:
            out["emails"].append(e)

    # Plain-text phones: only trust numbers that sit next to a phone-ish label
    for m in re.finditer(r"(?:tel|phone|call|mobile|mob|cell|whatsapp|contact)[^\d+]{0,15}(\+?\d[\d\s().\-]{7,18}\d)",
                         text, flags=re.I):
        p = _clean_phone(m.group(1))
        if p:
            out["phones"].append(p)

    for pat in OWNER_PATTERNS:
        for m in pat.finditer(text):
            groups = m.groups()
            name = next((g for g in groups if g and g[0].isupper() and " " in g.strip()
                         and g.lower() not in TITLE_WORDS), None)
            title = next((g for g in groups if g and g.lower() in TITLE_WORDS), None)
            if name and _valid_name(name):
                out["owners"].append((_title_case(name), (title or "owner").lower()))

    return out


def _walk_jsonld(node, out, depth=0):
    if depth > 6:
        return
    if isinstance(node, list):
        for n in node:
            _walk_jsonld(n, out, depth + 1)
        return
    if not isinstance(node, dict):
        return
    for key in ("email",):
        v = node.get(key)
        if isinstance(v, str):
            e = _clean_email(v.replace("mailto:", ""))
            if e:
                out["emails"].append(e)
    v = node.get("telephone")
    if isinstance(v, str):
        p = _clean_phone(v)
        if p:
            out["phones"].append(p)
    for key, title in (("founder", "founder"), ("founders", "founder"), ("owner", "owner")):
        v = node.get(key)
        for person in (v if isinstance(v, list) else [v]):
            name = person.get("name") if isinstance(person, dict) else person
            if isinstance(name, str) and _valid_name(name.strip()):
                out["owners"].append((name.strip(), title))
    if node.get("@type") == "Person" and isinstance(node.get("jobTitle"), str):
        jt = node["jobTitle"].lower()
        name = node.get("name")
        if any(t in jt for t in TITLE_WORDS) and isinstance(name, str) and _valid_name(name.strip()):
            out["owners"].append((name.strip(), jt))
    for v in node.values():
        if isinstance(v, (dict, list)):
            _walk_jsonld(v, out, depth + 1)


def _candidate_pages(html, base_url):
    base_host = urlparse(base_url).netloc.replace("www.", "")
    soup = BeautifulSoup(html, "html.parser")
    pages = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        full = urljoin(base_url, href).split("#")[0]
        p = urlparse(full)
        if p.scheme not in ("http", "https") or p.netloc.replace("www.", "") != base_host:
            continue
        hint = (p.path + " " + a.get_text(" ")).lower()
        if any(h in hint for h in PAGE_HINTS) and full not in pages and full.rstrip("/") != base_url.rstrip("/"):
            pages.append(full)
    return pages


# =========================================================================== classification

def _dedupe(seq):
    return list(dict.fromkeys(seq))


def _own_domain(email, website):
    host = urlparse(website).netloc.lower().replace("www.", "")
    root = ".".join(host.split(".")[-2:]) if host else ""
    return bool(root) and email.split("@")[1].endswith(root)


def _split_emails(emails, owner_names, website):
    """Return (shop_emails, owner_emails, other_emails)."""
    name_tokens = set()
    for n in owner_names:
        name_tokens.update(t.lower() for t in re.split(r"[\s\-']", n) if len(t) > 2)

    shop, owner, other = [], [], []
    for e in emails:
        local = e.split("@")[0]
        local_clean = re.sub(r"[^a-z]", "", local)
        if local_clean in GENERIC_LOCALS or local in GENERIC_LOCALS:
            shop.append(e)
        elif any(t in local_clean for t in name_tokens) and name_tokens:
            owner.append(e)
        elif _own_domain(e, website):
            other.append(e)             # a person on the shop's domain, name unknown
        else:
            other.append(e)
    return shop, owner, other


# =========================================================================== public API

def _url_variants(url):
    """The URL as given, then http/https and www/non-www alternatives."""
    p = urlparse(url)
    host = p.netloc
    alt_host = host[4:] if host.startswith("www.") else "www." + host
    out = [url]
    for scheme in (p.scheme, "https" if p.scheme == "http" else "http"):
        for h in (host, alt_host):
            v = p._replace(scheme=scheme, netloc=h).geturl()
            if v not in out:
                out.append(v)
    return out


def enrich_from_website(website, render=None):
    """Collect public contact details from a business website.

    *render* is an optional function ``url -> html`` that loads a page in a real
    browser (needed for sites that build their pages with JavaScript). It is
    only used when plain HTTP fetching finds no email.

    Returns a dict with keys:
        shop_email, owner_email, other_emails, website_phones, whatsapp,
        owner_name, owner_title, facebook, instagram, twitter, linkedin,
        youtube, tiktok
    All values are strings ('; '-joined where several) or None.
    """
    empty = dict.fromkeys(
        ["shop_email", "owner_email", "other_emails", "website_phones", "whatsapp",
         "owner_name", "owner_title", "facebook", "instagram", "twitter",
         "linkedin", "youtube", "tiktok"]
    )
    if not website:
        return empty
    if not website.startswith(("http://", "https://")):
        website = "http://" + website
    host = urlparse(website).netloc.lower()
    if any(host == d or host.endswith("." + d) for d in SKIP_DOMAINS):
        return empty

    agg = {"emails": [], "phones": [], "whatsapp": [], "social": {}, "owners": []}

    def merge(page):
        agg["emails"] += page["emails"]
        agg["phones"] += page["phones"]
        agg["whatsapp"] += page["whatsapp"]
        agg["owners"] += page["owners"]
        for k, v in page["social"].items():
            agg["social"].setdefault(k, v)

    def crawl(fetch):
        """Crawl homepage + contact-ish pages with the given fetch function."""
        home, base = None, website
        for variant in _url_variants(website):
            home = fetch(variant)
            if home:
                base = variant
                break
        if not home:
            return False
        merge(parse_page(home))
        pages = _candidate_pages(home, base)
        root = f"{urlparse(base).scheme}://{urlparse(base).netloc}"
        for path in DEFAULT_PATHS:
            if root + path not in pages and root + path + "/" not in pages:
                pages.append(root + path)
        seen = 0
        for page_url in pages:
            if seen >= MAX_EXTRA_PAGES:
                break
            html = fetch(page_url)
            if html:
                merge(parse_page(html))
                seen += 1
        return True

    reached = crawl(_fetch)
    if render and not agg["emails"]:
        reached = crawl(render) or reached
    if not reached:
        return empty

    emails = _dedupe(agg["emails"])
    # Most-mentioned owner wins
    owner_name = owner_title = None
    if agg["owners"]:
        counts = {}
        for n, t in agg["owners"]:
            counts[(n, t)] = counts.get((n, t), 0) + 1
        (owner_name, owner_title), _ = max(counts.items(), key=lambda kv: kv[1])

    shop, owner, other = _split_emails(
        emails, [n for n, _ in agg["owners"]], website
    )
    # If we have an owner name but no name-matching email, and there is exactly
    # one personal-looking address on the shop's own domain, offer it as the owner's.
    if owner_name and not owner:
        personal_own = [e for e in other if _own_domain(e, website)]
        if len(personal_own) == 1:
            owner, other = personal_own, [e for e in other if e not in personal_own]

    # No role mailbox but some own-domain address? Better as shop email than nothing.
    if not shop and not owner and other:
        own = [e for e in other if _own_domain(e, website)]
        if own:
            shop, other = own[:1], [e for e in other if e not in own[:1]]

    result = dict(empty)
    result.update(
        shop_email="; ".join(shop) or None,
        owner_email="; ".join(owner) or None,
        other_emails="; ".join(other) or None,
        website_phones="; ".join(_dedupe(agg["phones"])) or None,
        whatsapp="; ".join(_dedupe(agg["whatsapp"])) or None,
        owner_name=owner_name,
        owner_title=owner_title,
    )
    for k, v in agg["social"].items():
        result[k] = v
    return result


def best_contact(row):
    """Pick the best way to reach a business: email first, then WhatsApp, then phone."""
    def first(v):
        return str(v).split(";")[0].strip() if v and str(v) != "nan" else None

    for key, label in (("shop_email", "email"), ("owner_email", "email"),
                       ("other_emails", "email"), ("whatsapp", "whatsapp"),
                       ("phone", "phone"), ("website_phones", "phone")):
        v = first(row.get(key))
        if v:
            return f"{label}: {v}"
    return None
