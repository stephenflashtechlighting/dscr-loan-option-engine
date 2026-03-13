from __future__ import annotations
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import re
import json
import urllib.request
import urllib.error
from typing import Optional


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def fetch_url(url: str, timeout: int = 10) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        # Try utf-8, fall back to latin-1
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("latin-1")


# ── Field extractors ───────────────────────────────────────────────────────────

def _clean_price(s: str) -> Optional[float]:
    s = s.replace(",", "").replace("$", "").strip()
    try:
        return float(s)
    except Exception:
        return None


def _extract_json_ld(html: str) -> dict:
    """Pull structured data from JSON-LD script tags."""
    pattern = re.compile(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S)
    for m in pattern.finditer(html):
        try:
            data = json.loads(m.group(1))
            if isinstance(data, list):
                for item in data:
                    if item.get("@type") in ("SingleFamilyResidence", "Residence", "RealEstateListing", "Product"):
                        return item
            elif isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def _parse_zillow(html: str, url: str) -> dict:
    result = {}

    # Zillow embeds data in a __NEXT_DATA__ or hdpApolloPreloadedData script
    nd = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if nd:
        try:
            data = json.loads(nd.group(1))
            # Walk into the props to find home data
            props = data.get("props", {}).get("pageProps", {})
            home = (
                props.get("componentProps", {}).get("gdpClientCache")
                or props.get("initialReduxState", {})
            )
            if isinstance(home, str):
                home = json.loads(home)
            # Try to find price, address, taxes in nested structure
            def _walk(obj, depth=0):
                if depth > 8 or not isinstance(obj, dict):
                    return
                if "price" in obj and isinstance(obj["price"], (int, float)):
                    result.setdefault("purchase_price", float(obj["price"]))
                if "taxAnnualAmount" in obj:
                    result.setdefault("annual_taxes", float(str(obj["taxAnnualAmount"]).replace(",", "")))
                if "streetAddress" in obj:
                    result.setdefault("address", obj["streetAddress"])
                if "city" in obj and "state" in obj:
                    city = obj.get("city", "")
                    state = obj.get("state", "")
                    zip_ = obj.get("zipcode", "")
                    if city and state:
                        result.setdefault("city_state_zip", f"{city}, {state} {zip_}".strip())
                for v in obj.values():
                    _walk(v, depth + 1)
            _walk(home)
        except Exception:
            pass

    # Regex fallbacks for price
    if "purchase_price" not in result:
        m = re.search(r'"price"\s*:\s*(\d{5,9})', html)
        if m:
            result["purchase_price"] = float(m.group(1))

    if "purchase_price" not in result:
        m = re.search(r'\$\s*([\d,]{5,})', html)
        if m:
            p = _clean_price(m.group(1))
            if p and p > 50000:
                result["purchase_price"] = p

    # Taxes
    if "annual_taxes" not in result:
        m = re.search(r'(?:annual tax|property tax)[^\d]*\$?\s*([\d,]+)', html, re.I)
        if m:
            result["annual_taxes"] = float(m.group(1).replace(",", ""))

    # Address from URL
    if "address" not in result:
        m = re.search(r'zillow\.com/homedetails/([^/]+)/', url)
        if m:
            slug = m.group(1).replace("-", " ").title()
            slug = re.sub(r'\s+\d+$', '', slug)
            result["address_hint"] = slug

    return result


def _parse_redfin(html: str, url: str) -> dict:
    result = {}

    # Redfin puts data in window.__reactServerState or script tags
    m = re.search(r'"listingPrice"\s*:\s*\{[^}]*"amount"\s*:\s*(\d+)', html)
    if m:
        result["purchase_price"] = float(m.group(1))

    if "purchase_price" not in result:
        m = re.search(r'"price"\s*:\s*(\d{5,9})', html)
        if m:
            result["purchase_price"] = float(m.group(1))

    # Taxes
    m = re.search(r'(?:property tax|annual tax)[^\d$]*\$?\s*([\d,]+)', html, re.I)
    if m:
        result["annual_taxes"] = float(m.group(1).replace(",", ""))

    # Address
    m = re.search(r'"streetAddress"\s*:\s*"([^"]+)"', html)
    if m:
        result["address"] = m.group(1)
    m2 = re.search(r'"addressLocality"\s*:\s*"([^"]+)"', html)
    m3 = re.search(r'"addressRegion"\s*:\s*"([^"]+)"', html)
    m4 = re.search(r'"postalCode"\s*:\s*"([^"]+)"', html)
    if m2 and m3:
        result["city_state_zip"] = f"{m2.group(1)}, {m3.group(1)} {m4.group(1) if m4 else ''}".strip()

    # Beds/baths/sqft for context
    m = re.search(r'"beds"\s*:\s*(\d+)', html)
    if m:
        result["beds"] = int(m.group(1))
    m = re.search(r'"baths"\s*:\s*([\d.]+)', html)
    if m:
        result["baths"] = float(m.group(1))
    m = re.search(r'"sqFt"\s*:\s*\{[^}]*"value"\s*:\s*(\d+)', html)
    if not m:
        m = re.search(r'"sqft"\s*:\s*(\d+)', html, re.I)
    if m:
        result["sqft"] = int(m.group(1))

    return result


def _parse_realtor(html: str, url: str) -> dict:
    result = {}

    # Realtor.com uses __NEXT_DATA__
    nd = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if nd:
        try:
            data = json.loads(nd.group(1))
            listing = (
                data.get("props", {})
                .get("pageProps", {})
                .get("initialState", {})
                .get("propertyDetails", {})
            )
            if listing:
                if listing.get("list_price"):
                    result["purchase_price"] = float(listing["list_price"])
                addr = listing.get("address", {})
                if addr.get("line"):
                    result["address"] = addr["line"]
                if addr.get("city") and addr.get("state_code"):
                    result["city_state_zip"] = f"{addr['city']}, {addr['state_code']} {addr.get('postal_code', '')}".strip()
                if listing.get("tax_history"):
                    taxes = listing["tax_history"]
                    if taxes and taxes[0].get("tax"):
                        result["annual_taxes"] = float(taxes[0]["tax"])
                result["beds"] = listing.get("beds")
                result["baths"] = listing.get("baths")
                result["sqft"] = listing.get("building_size", {}).get("size")
        except Exception:
            pass

    if "purchase_price" not in result:
        m = re.search(r'"list_price"\s*:\s*(\d{5,9})', html)
        if m:
            result["purchase_price"] = float(m.group(1))

    return result


def _parse_trulia(html: str, url: str) -> dict:
    result = {}

    # Trulia uses __NEXT_DATA__ like most React sites
    nd = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if nd:
        try:
            data = json.loads(nd.group(1))
            # Walk props to find home data
            def _walk(obj, depth=0):
                if depth > 10 or not isinstance(obj, dict):
                    return
                # Price
                for price_key in ("listingPrice", "price", "listPrice"):
                    if price_key in obj and isinstance(obj[price_key], (int, float)):
                        result.setdefault("purchase_price", float(obj[price_key]))
                    elif price_key in obj and isinstance(obj[price_key], dict):
                        amt = obj[price_key].get("amount") or obj[price_key].get("value")
                        if amt:
                            result.setdefault("purchase_price", float(amt))
                # Taxes
                for tax_key in ("taxAnnualAmount", "annualTaxAmount", "propertyTaxes"):
                    if tax_key in obj:
                        try:
                            result.setdefault("annual_taxes", float(str(obj[tax_key]).replace(",", "")))
                        except Exception:
                            pass
                # Address
                if "streetAddress" in obj and obj["streetAddress"]:
                    result.setdefault("address", obj["streetAddress"])
                if "city" in obj and "state" in obj and obj.get("city"):
                    result.setdefault("city_state_zip", f"{obj['city']}, {obj['state']} {obj.get('zip', obj.get('zipCode', ''))}".strip())
                # Beds/baths/sqft
                if "bedrooms" in obj and obj["bedrooms"]:
                    result.setdefault("beds", obj["bedrooms"])
                if "bathrooms" in obj and obj["bathrooms"]:
                    result.setdefault("baths", obj["bathrooms"])
                for sqft_key in ("livingArea", "squareFeet", "floorSize"):
                    if sqft_key in obj and obj[sqft_key]:
                        try:
                            result.setdefault("sqft", int(float(str(obj[sqft_key]).replace(",", ""))))
                        except Exception:
                            pass
                if "yearBuilt" in obj and obj["yearBuilt"]:
                    result.setdefault("year_built", obj["yearBuilt"])
                for v in obj.values():
                    if isinstance(v, (dict, list)):
                        _walk(v if isinstance(v, dict) else (v[0] if v else {}), depth + 1)
            _walk(data)
        except Exception:
            pass

    # Regex fallbacks
    if "purchase_price" not in result:
        for pattern in [
            r'"listingPrice"\s*:\s*\{"amount"\s*:\s*(\d+)',
            r'"price"\s*:\s*(\d{5,9})',
            r'\$\s*([\d,]{6,})',
        ]:
            m = re.search(pattern, html)
            if m:
                p = _clean_price(m.group(1))
                if p and p > 50000:
                    result["purchase_price"] = p
                    break

    if "annual_taxes" not in result:
        for pattern in [
            r'"taxAnnualAmount"\s*:\s*"?([\d,]+)',
            r'"annualTaxAmount"\s*:\s*"?([\d,]+)',
            r'property\s+tax(?:es)?[^<\d$]{0,20}\$\s*([\d,]+)',
            r'annual\s+tax(?:es)?[^<\d$]{0,20}\$\s*([\d,]+)',
        ]:
            m = re.search(pattern, html, re.I)
            if m:
                try:
                    val = float(m.group(1).replace(",", ""))
                    # Sanity check — annual taxes should be between $200 and $50,000
                    if 200 <= val <= 50000:
                        result["annual_taxes"] = val
                        break
                except Exception:
                    pass

    if "address" not in result:
        m = re.search(r'"streetAddress"\s*:\s*"([^"]+)"', html)
        if m:
            result["address"] = m.group(1)
        m2 = re.search(r'"addressLocality"\s*:\s*"([^"]+)"', html)
        m3 = re.search(r'"addressRegion"\s*:\s*"([^"]+)"', html)
        m4 = re.search(r'"postalCode"\s*:\s*"([^"]+)"', html)
        if m2 and m3:
            result["city_state_zip"] = f"{m2.group(1)}, {m3.group(1)} {m4.group(1) if m4 else ''}".strip()

    return result



    """Generic fallback using JSON-LD and regex."""
    result = {}
    jld = _extract_json_ld(html)
    if jld.get("offers", {}).get("price"):
        result["purchase_price"] = float(jld["offers"]["price"])
    if jld.get("address", {}).get("streetAddress"):
        result["address"] = jld["address"]["streetAddress"]
        city = jld["address"].get("addressLocality", "")
        state = jld["address"].get("addressRegion", "")
        zip_ = jld["address"].get("postalCode", "")
        result["city_state_zip"] = f"{city}, {state} {zip_}".strip()

    # Generic price regex
    if "purchase_price" not in result:
        for pattern in [
            r'listing[_ ]price["\s:]+\$?([\d,]+)',
            r'asking[_ ]price["\s:]+\$?([\d,]+)',
            r'"price"\s*:\s*(\d{5,9})',
        ]:
            m = re.search(pattern, html, re.I)
            if m:
                p = _clean_price(m.group(1))
                if p and p > 50000:
                    result["purchase_price"] = p
                    break

    return result


# ── AI-assisted extraction fallback ───────────────────────────────────────────

def ai_extract_listing(html_snippet: str) -> dict:
    """
    Use Anthropic API to extract deal fields from a listing page snippet or plain text.
    Accepts both raw HTML and plain pasted listing text.
    """
    try:
        import anthropic
        client = anthropic.Anthropic()

        # Strip HTML tags if present, otherwise use as-is
        text = re.sub(r'<[^>]+>', ' ', html_snippet)
        text = re.sub(r'\s+', ' ', text).strip()[:6000]

        system = """You are a real estate listing data extractor.
Extract property details from the text and return ONLY a JSON object, no markdown, no explanation.

JSON schema:
{
  "purchase_price": number or null,
  "address": string or null,
  "city": string or null,
  "state": string or null,
  "zip": string or null,
  "annual_taxes": number or null,
  "beds": number or null,
  "baths": number or null,
  "sqft": number or null,
  "hoa_monthly": number or null,
  "year_built": number or null
}

Rules:
- purchase_price is the list/asking price as a plain number
- annual_taxes is the yearly property tax as a plain number
- Return null for any field not found
"""
        resp = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            system=system,
            messages=[{"role": "user", "content": f"Extract from this listing:\n\n{text}"}],
        )
        raw = resp.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
        parsed = json.loads(raw)
        # Normalize
        result = {}
        if parsed.get("purchase_price"):
            result["purchase_price"] = float(parsed["purchase_price"])
        if parsed.get("address"):
            result["address"] = parsed["address"]
        parts = [parsed.get("city"), parsed.get("state"), parsed.get("zip")]
        parts = [p for p in parts if p]
        if parts:
            result["city_state_zip"] = ", ".join(parts[:2]) + (" " + parts[2] if len(parts) > 2 else "")
        if parsed.get("annual_taxes"):
            result["annual_taxes"] = float(parsed["annual_taxes"])
        for k in ("beds", "baths", "sqft", "hoa_monthly", "year_built"):
            if parsed.get(k) is not None:
                result[k] = parsed[k]
        return result
    except Exception as e:
        return {"error": str(e)}


# ── Insurance estimate ─────────────────────────────────────────────────────────

def estimate_insurance(purchase_price: float, state: str = "") -> float:
    """
    Rough annual insurance estimate.
    Gulf Coast / Louisiana: higher rate due to hurricane/flood exposure.
    """
    state_upper = state.upper().strip()
    if state_upper in ("LA", "MS", "AL", "FL", "TX", "SC", "NC", "GA"):
        rate = 0.010  # ~1.0% for coastal/high-risk states
    else:
        rate = 0.006  # ~0.6% national average
    return round(purchase_price * rate, 0)


# ── Main public interface ──────────────────────────────────────────────────────

def import_listing(url: str, use_ai_fallback: bool = True) -> dict:
    """
    Fetch and parse a real estate listing URL.
    Returns a dict with deal-compatible fields and a 'source' key.

    Fields returned (when available):
        purchase_price, address, city_state_zip, annual_taxes,
        annual_insurance (estimated), beds, baths, sqft,
        hoa_monthly, year_built, source_url, confidence
    """
    url = url.strip()

    try:
        html = fetch_url(url)
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code} — listing may require login or be unavailable."}
    except urllib.error.URLError as e:
        return {"error": f"Could not reach URL: {e.reason}"}
    except Exception as e:
        return {"error": str(e)}

    # Route to site-specific parser
    if "zillow.com" in url:
        result = _parse_zillow(html, url)
        source = "Zillow"
    elif "redfin.com" in url:
        result = _parse_redfin(html, url)
        source = "Redfin"
    elif "realtor.com" in url:
        result = _parse_realtor(html, url)
        source = "Realtor.com"
    elif "trulia.com" in url:
        result = _parse_trulia(html, url)
        source = "Trulia"
    else:
        result = _parse_generic(html, url)
        source = "Listing page"

    # AI fallback if we didn't get a price
    if "purchase_price" not in result and use_ai_fallback:
        ai_result = ai_extract_listing(html[:20000])
        if "error" not in ai_result:
            result.update({k: v for k, v in ai_result.items() if k not in result})
            source += " (AI-assisted)"

    # Build full address string
    address_parts = []
    if result.get("address"):
        address_parts.append(result["address"])
    if result.get("city_state_zip"):
        address_parts.append(result["city_state_zip"])
    if address_parts:
        result["full_address"] = ", ".join(address_parts)

    # Estimate insurance if we have a price
    if result.get("purchase_price") and "annual_insurance" not in result:
        state = ""
        czp = result.get("city_state_zip", "")
        m = re.search(r',\s*([A-Z]{2})\b', czp)
        if m:
            state = m.group(1)
        result["annual_insurance_estimate"] = estimate_insurance(result["purchase_price"], state)

    result["source"] = source
    result["source_url"] = url

    # Confidence summary
    found_fields = [k for k in ("purchase_price", "address", "annual_taxes") if k in result]
    result["confidence"] = "high" if len(found_fields) == 3 else "medium" if len(found_fields) >= 1 else "low"

    return result
