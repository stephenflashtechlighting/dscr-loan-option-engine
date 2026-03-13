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

    # ── 1. Walk __NEXT_DATA__ JSON with full deep recursion ───────────────────
    nd = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if nd:
        try:
            data = json.loads(nd.group(1))

            def _walk(obj, depth=0):
                if depth > 20:
                    return
                if isinstance(obj, dict):
                    # Price
                    for price_key in ("listingPrice", "price", "listPrice", "formattedPrice"):
                        v = obj.get(price_key)
                        if isinstance(v, (int, float)) and v > 50000:
                            result.setdefault("purchase_price", float(v))
                        elif isinstance(v, dict):
                            amt = v.get("amount") or v.get("value")
                            if amt:
                                try:
                                    cleaned = float(str(amt).replace(",","").replace("$",""))
                                    if cleaned > 50000:
                                        result.setdefault("purchase_price", cleaned)
                                except Exception:
                                    pass
                        elif isinstance(v, str):
                            cleaned = _clean_price(v)
                            if cleaned and cleaned > 50000:
                                result.setdefault("purchase_price", cleaned)

                    # Taxes
                    for tax_key in ("taxAnnualAmount", "annualTaxAmount", "annualTaxes",
                                    "propertyTaxes", "taxAmount", "yearlyTaxes"):
                        v = obj.get(tax_key)
                        if v is not None:
                            try:
                                val = float(str(v).replace(",", "").replace("$", "").strip())
                                if 100 <= val <= 100000:
                                    result.setdefault("annual_taxes", val)
                            except Exception:
                                pass
                    # taxes nested object
                    if "taxes" in obj and isinstance(obj["taxes"], dict):
                        for tk in ("annualAmount", "amount", "annual"):
                            v = obj["taxes"].get(tk)
                            if v is not None:
                                try:
                                    val = float(str(v).replace(",", "").replace("$", ""))
                                    if 100 <= val <= 100000:
                                        result.setdefault("annual_taxes", val)
                                except Exception:
                                    pass

                    # Address
                    if obj.get("streetAddress"):
                        result.setdefault("address", obj["streetAddress"])
                    if obj.get("city") and obj.get("state"):
                        zipcode = obj.get("zip") or obj.get("zipCode") or obj.get("postalCode") or ""
                        result.setdefault("city_state_zip", f"{obj['city']}, {obj['state']} {zipcode}".strip())

                    # Beds / baths / sqft / year
                    for k, rk in (("bedrooms","beds"), ("beds","beds"), ("bathrooms","baths"), ("baths","baths")):
                        if obj.get(k):
                            result.setdefault(rk, obj[k])
                    for k in ("livingArea", "squareFeet", "floorSize", "sqft"):
                        if obj.get(k):
                            try:
                                result.setdefault("sqft", int(float(str(obj[k]).replace(",",""))))
                            except Exception:
                                pass
                    if obj.get("yearBuilt"):
                        result.setdefault("year_built", obj["yearBuilt"])

                    for v in obj.values():
                        _walk(v, depth + 1)
                elif isinstance(obj, list):
                    for item in obj:
                        _walk(item, depth + 1)

            _walk(data)
        except Exception:
            pass

    # ── 2. Raw JSON string regex ──────────────────────────────────────────────
    if "annual_taxes" not in result:
        for pattern in [
            r'"taxAnnualAmount"\s*:\s*"?([\d,.]+)"?',
            r'"annualTaxAmount"\s*:\s*"?([\d,.]+)"?',
            r'"annualTaxes"\s*:\s*"?([\d,.]+)"?',
            r'"yearlyTaxes"\s*:\s*"?([\d,.]+)"?',
            r'"taxAmount"\s*:\s*"?([\d,.]+)"?',
        ]:
            m = re.search(pattern, html, re.I)
            if m:
                try:
                    val = float(m.group(1).replace(",", ""))
                    if 100 <= val <= 100000:
                        result["annual_taxes"] = val
                        break
                except Exception:
                    pass

    # ── 3. Trulia "Property Tax and Assessment" section ──────────────────────
    # Trulia renders taxes in a table under a heading with data-testid="styled-section-container-heading"
    if "annual_taxes" not in result:
        tax_section = re.search(
            r'Property Tax and Assessment.{0,2000}?</table>',
            html, re.S | re.I
        )
        if tax_section:
            # Find dollar amounts inside that table
            amounts = re.findall(r'\$\s*([\d,]+)', tax_section.group())
            for amt in amounts:
                try:
                    val = float(amt.replace(",", ""))
                    if 100 <= val <= 50000:
                        result["annual_taxes"] = val
                        break
                except Exception:
                    pass

    # ── 4. Rendered HTML text regex ───────────────────────────────────────────
    if "annual_taxes" not in result:
        for pattern in [
            r'tax[^$\d]{0,30}\$\s*([\d,]+)\s*/\s*(?:per\s+)?year',
            r'\$\s*([\d,]+)\s*/\s*(?:per\s+)?year[^a-z]{0,20}tax',
            r'[Pp]roperty\s+[Tt]ax(?:es)?[^$\d]{0,20}\$\s*([\d,]+)',
            r'[Aa]nnual\s+[Tt]ax(?:es)?[^$\d]{0,20}\$\s*([\d,]+)',
            r'[Tt]ax\s+[Aa]nnual[^$\d]{0,20}\$?\s*([\d,]+)',
        ]:
            m = re.search(pattern, html, re.I | re.S)
            if m:
                try:
                    val = float(m.group(1).replace(",", ""))
                    if 100 <= val <= 100000:
                        result["annual_taxes"] = val
                        break
                except Exception:
                    pass

    # ── 5. Address fallback ───────────────────────────────────────────────────
    if "address" not in result:
        m = re.search(r'"streetAddress"\s*:\s*"([^"]+)"', html)
        if m:
            result["address"] = m.group(1)
        m2 = re.search(r'"addressLocality"\s*:\s*"([^"]+)"', html)
        m3 = re.search(r'"addressRegion"\s*:\s*"([^"]+)"', html)
        m4 = re.search(r'"postalCode"\s*:\s*"([^"]+)"', html)
        if m2 and m3:
            result["city_state_zip"] = f"{m2.group(1)}, {m3.group(1)} {m4.group(1) if m4 else ''}".strip()

    # ── 6. Price fallback ─────────────────────────────────────────────────────
    if "purchase_price" not in result:
        for pattern in [
            r'"listingPrice"\s*:\s*\{"amount"\s*:\s*(\d+)',
            r'"price"\s*:\s*(\d{5,9})',
        ]:
            m = re.search(pattern, html)
            if m:
                p = _clean_price(m.group(1))
                if p and p > 50000:
                    result["purchase_price"] = p
                    break

    # ── 7. Tax last-resort: scan ALL numeric values adjacent to "tax" in raw HTML ──
    if "annual_taxes" not in result:
        # Try JSON-LD structured data first
        jld = _extract_json_ld(html)
        for tk in ("taxAnnualAmount", "annualTaxAmount", "propertyTaxes", "taxes"):
            v = jld.get(tk)
            if v is not None:
                try:
                    val = float(str(v).replace(",", "").replace("$", ""))
                    if 100 <= val <= 100000:
                        result["annual_taxes"] = val
                        break
                except Exception:
                    pass

    if "annual_taxes" not in result:
        # Broaden to catch any pattern like: "tax": 2400 or tax_amount: "2,400"
        for pattern in [
            r'"(?:tax|taxes|taxAmount|annualTax|yearlyTax|propertyTax)[^"]*"\s*:\s*"?([\d,]+\.?\d*)"?',
            r'(?:annual|yearly|property)\s+tax(?:es)?\s*[:\-$]\s*([\d,]+)',
            r'taxes\s*\(annual\)[:\s$]*([\d,]+)',
            r'"amount"\s*:\s*([\d.]+)[^}]*"tax',   # amount before "tax" in same object
        ]:
            m = re.search(pattern, html, re.I)
            if m:
                try:
                    val = float(m.group(1).replace(",", ""))
                    if 100 <= val <= 100000:
                        result["annual_taxes"] = val
                        break
                except Exception:
                    pass

    # ── 8. Store debug info so UI can show what keys were found ───────────────
    try:
        nd2 = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
        if nd2:
            raw_json = nd2.group(1)
            # Collect all keys that contain "tax" anywhere
            tax_keys_found = list(set(re.findall(r'"([^"]*[Tt]ax[^"]*)"', raw_json)))[:20]
            result["_debug_tax_keys"] = tax_keys_found
            # Collect all tax key:value pairs
            tax_pairs = re.findall(r'"([^"]*[Tt]ax[^"]*)":\s*([^,}\]]{1,60})', raw_json)
            result["_debug_tax_pairs"] = [f"{k}: {v.strip()}" for k, v in tax_pairs[:15]]
    except Exception:
        pass

    # ── 9. AI extraction for taxes — last resort when all regex failed ────────
    if "annual_taxes" not in result:
        try:
            import anthropic
            client = anthropic.Anthropic()
            stripped = re.sub(r'<[^>]+>', ' ', html)
            stripped = re.sub(r'\s+', ' ', stripped)
            tax_windows = [m.group() for m in re.finditer(r'.{0,200}[Tt]ax.{0,200}', stripped)]
            snippet = ' | '.join(tax_windows[:10])[:3000] if tax_windows else stripped[:3000]
            resp = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=100,
                messages=[{"role": "user", "content": (
                    "From this real estate listing text, find the annual property tax amount in dollars. "
                    "Return ONLY a plain number like 2400. If not found, return null.\n\n" + snippet
                )}],
            )
            raw_tax = resp.content[0].text.strip()
            if raw_tax.lower() != "null":
                val = float(raw_tax.replace(",", "").replace("$", "").strip())
                if 100 <= val <= 100000:
                    result["annual_taxes"] = val
        except Exception:
            pass

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

def ai_extract_listing(text_or_html: str) -> dict:
    """
    Use Anthropic API to extract deal fields from pasted listing text or HTML.
    Returns a flat dict with normalized field names.
    """
    try:
        import anthropic
        client = anthropic.Anthropic()

        # Strip HTML tags, collapse whitespace
        text = re.sub(r'<[^>]+>', ' ', text_or_html)
        text = re.sub(r'\s+', ' ', text).strip()[:8000]

        system = """You are a real estate listing data extractor.
Your job is to find specific numeric and text values from listing content and return them as JSON.

Return ONLY a valid JSON object. No markdown fences, no explanation, no extra text.

Required JSON fields (use null if not found):
{
  "purchase_price": <number — the asking/list price, e.g. 189000>,
  "address": <string — street address only, e.g. "1149 Hodges St">,
  "city_state_zip": <string — city, state ZIP, e.g. "Lake Charles, LA 70601">,
  "annual_taxes": <number — annual property tax in dollars. Look for: "taxes/yr", "taxes/year", "annual tax", "property tax $X/yr", "tax $X". Convert monthly to annual by multiplying by 12. e.g. 2400>,
  "beds": <number>,
  "baths": <number>,
  "sqft": <number — living area square footage>,
  "year_built": <number>,
  "hoa_monthly": <number — monthly HOA fee, null if none>
}

IMPORTANT rules:
- All dollar values as plain numbers without $ or commas
- annual_taxes: if you see "$200/mo taxes" that is 200*12 = 2400 annual. If you see "$2,400/yr" that is 2400.
- If a field is genuinely not present anywhere in the text, return null for it
- Do not guess or fabricate values"""

        resp = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=600,
            system=system,
            messages=[{"role": "user", "content": f"Extract listing data from this text:\n\n{text}"}],
        )
        raw = resp.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
        parsed = json.loads(raw)

        result = {}
        for num_field in ("purchase_price", "annual_taxes", "beds", "baths", "sqft", "year_built", "hoa_monthly"):
            v = parsed.get(num_field)
            if v is not None:
                try:
                    result[num_field] = float(str(v).replace(",", "").replace("$", ""))
                except Exception:
                    pass
        for str_field in ("address", "city_state_zip"):
            v = parsed.get(str_field)
            if v:
                result[str_field] = str(v).strip()
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
