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

CRITICAL_FIELDS = ("purchase_price", "address", "annual_taxes")


def fetch_url(url: str, timeout: int = 10) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
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


def _visible_text(html: str) -> str:
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.I | re.S)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_json_ld(html: str) -> dict:
    pattern = re.compile(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S)
    for m in pattern.finditer(html):
        try:
            data = json.loads(m.group(1))
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and item.get("@type") in ("SingleFamilyResidence", "Residence", "RealEstateListing", "Product"):
                        return item
            elif isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def _extract_labeled_currency(text: str, labels: list[str]) -> Optional[float]:
    labels_re = "|".join(re.escape(label) for label in labels)
    patterns = [
        rf"(?:{labels_re})\s*[:\-]?\s*\$\s*([\d,]+(?:\.\d+)?)",
        rf"(?:{labels_re})[^\d$]{{0,20}}\$\s*([\d,]+(?:\.\d+)?)",
        rf"(?:{labels_re})\s*(?:for\s*\d{{4}})?\s*[:\-]?\s*([\d,]+(?:\.\d+)?)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.I | re.S)
        if m:
            try:
                val = float(m.group(1).replace(",", ""))
                if 1 <= val <= 200000:
                    return val
            except Exception:
                pass
    return None


def _extract_trulia_tax_table(html: str) -> Optional[float]:
    visible = _visible_text(html)
    direct = _extract_labeled_currency(visible, ["Tax", "Taxes", "Property Tax", "Annual Tax"])
    if direct is not None:
        return direct

    for pattern in [
        r"Year\s*\d{4}\s*Tax\s*\$\s*([\d,]+(?:\.\d+)?)\s*Assessment",
        r"Tax\s*\$\s*([\d,]+(?:\.\d+)?)\s*Assessment",
        r"Tax\s*([\d,]+(?:\.\d+)?)\s*Assessment",
    ]:
        m = re.search(pattern, visible, re.I | re.S)
        if m:
            try:
                val = float(m.group(1).replace(",", ""))
                if 1 <= val <= 200000:
                    return val
            except Exception:
                pass
    return None


def _extract_tax_value(text: str) -> Optional[float]:
    visible = _visible_text(text)

    direct = _extract_labeled_currency(
        visible,
        ["Tax", "Taxes", "Property Tax", "Property Taxes", "Annual Tax", "Annual Taxes"],
    )
    if direct is not None:
        return direct

    patterns = [
        r'(?:property\s*tax(?:es)?|annual\s*tax(?:es)?|taxes|\btax\b)[^\d$]{0,50}\$\s*([\d,]+(?:\.\d+)?)',
        r'"taxAnnualAmount"\s*:\s*"?([\d,]+(?:\.\d+)?)"?',
        r'"propertyTaxRate"\s*:\s*"?([\d,]+(?:\.\d+)?)"?',
        r'"tax"\s*:\s*([\d,]+(?:\.\d+)?)',
        r'"taxes"\s*:\s*\{[^\}]{0,120}?"total"\s*:\s*([\d,]+(?:\.\d+)?)',
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.I | re.S) or re.search(pattern, visible, re.I | re.S)
        if m:
            try:
                val = float(m.group(1).replace(",", ""))
                if 1 <= val <= 200000:
                    return val
            except Exception:
                pass

    trulia_val = _extract_trulia_tax_table(text)
    if trulia_val is not None:
        return trulia_val

    return None


def _parse_zillow(html: str, url: str) -> dict:
    result = {}
    nd = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if nd:
        try:
            data = json.loads(nd.group(1))
            props = data.get("props", {}).get("pageProps", {})
            home = props.get("componentProps", {}).get("gdpClientCache") or props.get("initialReduxState", {})
            if isinstance(home, str):
                home = json.loads(home)

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
    if "annual_taxes" not in result:
        tax = _extract_tax_value(html)
        if tax is not None:
            result["annual_taxes"] = tax
    if "address" not in result:
        m = re.search(r'zillow\.com/homedetails/([^/]+)/', url)
        if m:
            slug = m.group(1).replace("-", " ").title()
            slug = re.sub(r'\s+\d+$', '', slug)
            result["address_hint"] = slug
    return result


def _parse_redfin(html: str, url: str) -> dict:
    result = {}
    m = re.search(r'"listingPrice"\s*:\s*\{[^}]*"amount"\s*:\s*(\d+)', html)
    if m:
        result["purchase_price"] = float(m.group(1))
    if "purchase_price" not in result:
        m = re.search(r'"price"\s*:\s*(\d{5,9})', html)
        if m:
            result["purchase_price"] = float(m.group(1))
    tax = _extract_tax_value(html)
    if tax is not None:
        result["annual_taxes"] = tax
    m = re.search(r'"streetAddress"\s*:\s*"([^"]+)"', html)
    if m:
        result["address"] = m.group(1)
    m2 = re.search(r'"addressLocality"\s*:\s*"([^"]+)"', html)
    m3 = re.search(r'"addressRegion"\s*:\s*"([^"]+)"', html)
    m4 = re.search(r'"postalCode"\s*:\s*"([^"]+)"', html)
    if m2 and m3:
        result["city_state_zip"] = f"{m2.group(1)}, {m3.group(1)} {m4.group(1) if m4 else ''}".strip()
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
    nd = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if nd:
        try:
            data = json.loads(nd.group(1))
            listing = data.get("props", {}).get("pageProps", {}).get("initialState", {}).get("propertyDetails", {})
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
    if "annual_taxes" not in result:
        tax = _extract_tax_value(html)
        if tax is not None:
            result["annual_taxes"] = tax
    return result


def _parse_trulia(html: str, url: str) -> dict:
    result = {}
    nd = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if nd:
        try:
            data = json.loads(nd.group(1))
            blob = json.dumps(data)
            m = re.search(r'"price"\s*:\s*([0-9]{5,9})', blob)
            if m:
                result["purchase_price"] = float(m.group(1))
            m = re.search(r'"streetAddress"\s*:\s*"([^"]+)"', blob)
            if m:
                result["address"] = m.group(1)
            city = re.search(r'"addressLocality"\s*:\s*"([^"]+)"', blob)
            state = re.search(r'"addressRegion"\s*:\s*"([^"]+)"', blob)
            zip_ = re.search(r'"postalCode"\s*:\s*"([^"]+)"', blob)
            if city and state:
                result["city_state_zip"] = f"{city.group(1)}, {state.group(1)} {zip_.group(1) if zip_ else ''}".strip()
            beds = re.search(r'"beds"\s*:\s*([\d.]+)', blob)
            baths = re.search(r'"baths"\s*:\s*([\d.]+)', blob)
            sqft = re.search(r'"floorSpace"\s*":?\s*\{[^\}]*"value"\s*:\s*(\d+)', blob)
            if beds:
                result["beds"] = float(beds.group(1)) if "." in beds.group(1) else int(beds.group(1))
            if baths:
                result["baths"] = float(baths.group(1))
            if sqft:
                result["sqft"] = int(sqft.group(1))
        except Exception:
            pass

    jld = _extract_json_ld(html)
    offers = jld.get("offers", {}) if isinstance(jld, dict) else {}
    if offers.get("price") and "purchase_price" not in result:
        result["purchase_price"] = float(offers["price"])
    addr = jld.get("address", {}) if isinstance(jld, dict) else {}
    if addr.get("streetAddress") and "address" not in result:
        result["address"] = addr["streetAddress"]
        result["city_state_zip"] = f"{addr.get('addressLocality','')}, {addr.get('addressRegion','')} {addr.get('postalCode','')}".strip()

    tax = _extract_trulia_tax_table(html) or _extract_tax_value(html)
    if tax is not None:
        result["annual_taxes"] = tax

    if "purchase_price" not in result:
        m = re.search(r'\$\s*([\d,]{5,})', html)
        if m:
            p = _clean_price(m.group(1))
            if p and p > 50000:
                result["purchase_price"] = p

    return result


def _parse_generic(html: str, url: str) -> dict:
    result = {}
    jld = _extract_json_ld(html)
    offers = jld.get("offers", {}) if isinstance(jld, dict) else {}
    if offers.get("price"):
        result["purchase_price"] = float(offers["price"])
    addr = jld.get("address", {}) if isinstance(jld, dict) else {}
    if addr.get("streetAddress"):
        result["address"] = addr["streetAddress"]
        city = addr.get("addressLocality", "")
        state = addr.get("addressRegion", "")
        zip_ = addr.get("postalCode", "")
        result["city_state_zip"] = f"{city}, {state} {zip_}".strip()

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
    tax = _extract_tax_value(html)
    if tax is not None:
        result["annual_taxes"] = tax
    return result


# ── AI-assisted extraction fallback ───────────────────────────────────────────

def ai_extract_listing(html_snippet: str) -> dict:
    try:
        import anthropic
        client = anthropic.Anthropic()
        text = re.sub(r'<[^>]+>', ' ', html_snippet)
        text = re.sub(r'\s+', ' ', text).strip()[:9000]

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
    state_upper = state.upper().strip()
    if state_upper in ("LA", "MS", "AL", "FL", "TX", "SC", "NC", "GA"):
        rate = 0.010
    else:
        rate = 0.006
    return round(purchase_price * rate, 0)


# ── Main public interface ──────────────────────────────────────────────────────

def import_listing(url: str, use_ai_fallback: bool = True) -> dict:
    url = url.strip()

    try:
        html = fetch_url(url)
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code} — listing may require login or be unavailable."}
    except urllib.error.URLError as e:
        return {"error": f"Could not reach URL: {e.reason}"}
    except Exception as e:
        return {"error": str(e)}

    lower = url.lower()
    if "trulia.com" in lower:
        result = _parse_trulia(html, url)
        source = "Trulia"
    elif "zillow.com" in lower:
        result = _parse_zillow(html, url)
        source = "Zillow"
    elif "redfin.com" in lower:
        result = _parse_redfin(html, url)
        source = "Redfin"
    elif "realtor.com" in lower:
        result = _parse_realtor(html, url)
        source = "Realtor.com"
    else:
        result = _parse_generic(html, url)
        source = "Listing page"

    missing_critical = [f for f in CRITICAL_FIELDS if not result.get(f)]
    if use_ai_fallback and missing_critical:
        ai_result = ai_extract_listing(html[:25000])
        if "error" not in ai_result:
            for k, v in ai_result.items():
                if v is not None and not result.get(k):
                    result[k] = v
            source += " (AI-assisted)"

    address_parts = []
    if result.get("address"):
        address_parts.append(result["address"])
    if result.get("city_state_zip"):
        address_parts.append(result["city_state_zip"])
    if address_parts:
        result["full_address"] = ", ".join(address_parts)

    if result.get("purchase_price") and "annual_insurance_estimate" not in result:
        state = ""
        czp = result.get("city_state_zip", "")
        m = re.search(r',\s*([A-Z]{2})\b', czp)
        if m:
            state = m.group(1)
        result["annual_insurance_estimate"] = estimate_insurance(result["purchase_price"], state)

    result["source"] = source
    result["source_url"] = url
    found_fields = [k for k in CRITICAL_FIELDS if result.get(k)]
    result["confidence"] = "high" if len(found_fields) == 3 else "medium" if len(found_fields) >= 1 else "low"
    if not result.get("annual_taxes"):
        result.setdefault("warnings", []).append(
            "Tax field may be visible on the listing page, but the current parser did not match it reliably. Verify against the listing or county record."
        )
    return result
