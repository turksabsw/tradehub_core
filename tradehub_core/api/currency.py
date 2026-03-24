import frappe
from frappe import _


# Supported currencies with metadata
SUPPORTED_CURRENCIES = {
    "USD": {"symbol": "$", "name": "US Dollar", "name_tr": "Amerikan Dolari", "decimal_places": 2},
    "EUR": {"symbol": "\u20ac", "name": "Euro", "name_tr": "Euro", "decimal_places": 2},
    "TRY": {"symbol": "\u20ba", "name": "Turkish Lira", "name_tr": "T\u00fcrk Liras\u0131", "decimal_places": 2},
    "GBP": {"symbol": "\u00a3", "name": "British Pound", "name_tr": "\u0130ngiliz Sterlini", "decimal_places": 2},
    "CNY": {"symbol": "\u00a5", "name": "Chinese Yuan", "name_tr": "\u00c7in Yuan\u0131", "decimal_places": 2},
}

# Country to default currency mapping
COUNTRY_CURRENCY_MAP = {
    "TR": "TRY",
    "US": "USD",
    "GB": "GBP",
    "DE": "EUR",
    "FR": "EUR",
    "IT": "EUR",
    "ES": "EUR",
    "NL": "EUR",
    "BE": "EUR",
    "AT": "EUR",
    "PT": "EUR",
    "GR": "EUR",
    "IE": "EUR",
    "FI": "EUR",
    "CN": "CNY",
    "HK": "CNY",
    "TW": "CNY",
}


@frappe.whitelist(allow_guest=True)
def get_currency_settings():
    """Get supported currencies, exchange rates, and default currency.

    Returns all data needed for the frontend currency selector and price conversion.
    """
    # Get exchange rates from Currency Rate DocType
    rates = {}
    currency_rates = frappe.get_all(
        "Currency Rate",
        filters={"is_active": 1},
        fields=["from_currency", "usd_rate", "eur_rate", "try_rate", "gbp_rate", "cny_rate", "last_updated"],
    )

    for cr in currency_rates:
        rates[cr.from_currency] = {
            "USD": cr.usd_rate or 1,
            "EUR": cr.eur_rate or 1,
            "TRY": cr.try_rate or 1,
            "GBP": cr.gbp_rate or 1,
            "CNY": cr.cny_rate or 1,
            "last_updated": str(cr.last_updated) if cr.last_updated else None,
        }

    # If no rates configured, use defaults (USD-based)
    if not rates:
        rates = {
            "USD": {"USD": 1, "EUR": 0.92, "TRY": 38.50, "GBP": 0.79, "CNY": 7.25},
        }

    # Detect user's country from request headers
    detected_country = _detect_country()
    default_currency = COUNTRY_CURRENCY_MAP.get(detected_country, "USD")

    # Build currencies list with metadata
    currencies = []
    for code, meta in SUPPORTED_CURRENCIES.items():
        currencies.append({
            "code": code,
            "symbol": meta["symbol"],
            "name": meta["name"],
            "nameTr": meta["name_tr"],
            "decimalPlaces": meta["decimal_places"],
        })

    return {
        "currencies": currencies,
        "rates": rates,
        "defaultCurrency": default_currency,
        "detectedCountry": detected_country,
        "baseCurrency": "USD",
    }


@frappe.whitelist(allow_guest=True)
def convert_price(amount, from_currency="USD", to_currency="TRY"):
    """Convert a price from one currency to another.

    Args:
        amount: The price amount to convert
        from_currency: Source currency code (default: USD)
        to_currency: Target currency code (default: TRY)

    Returns:
        Converted amount and formatted string
    """
    amount = float(amount)

    if from_currency == to_currency:
        return {
            "amount": amount,
            "formatted": _format_currency(amount, to_currency),
            "currency": to_currency,
            "rate": 1,
        }

    rate = _get_exchange_rate(from_currency, to_currency)
    converted = round(amount * rate, 2)

    return {
        "amount": converted,
        "formatted": _format_currency(converted, to_currency),
        "currency": to_currency,
        "rate": rate,
    }


def _get_exchange_rate(from_currency, to_currency):
    """Get exchange rate between two currencies."""
    # Try to get from Currency Rate DocType
    cr = frappe.db.get_value(
        "Currency Rate",
        from_currency,
        [f"{to_currency.lower()}_rate"],
        as_dict=True,
    )

    if cr:
        rate_field = f"{to_currency.lower()}_rate"
        rate = cr.get(rate_field)
        if rate:
            return float(rate)

    # Fallback: try reverse lookup
    cr_reverse = frappe.db.get_value(
        "Currency Rate",
        to_currency,
        [f"{from_currency.lower()}_rate"],
        as_dict=True,
    )

    if cr_reverse:
        rate_field = f"{from_currency.lower()}_rate"
        rate = cr_reverse.get(rate_field)
        if rate and float(rate) > 0:
            return 1.0 / float(rate)

    # Fallback defaults
    defaults = {
        ("USD", "TRY"): 38.50,
        ("USD", "EUR"): 0.92,
        ("USD", "GBP"): 0.79,
        ("USD", "CNY"): 7.25,
        ("EUR", "TRY"): 41.85,
        ("EUR", "USD"): 1.087,
        ("TRY", "USD"): 0.026,
        ("TRY", "EUR"): 0.024,
        ("GBP", "USD"): 1.27,
        ("CNY", "USD"): 0.138,
    }

    return defaults.get((from_currency, to_currency), 1.0)


def _format_currency(amount, currency_code):
    """Format amount with currency symbol."""
    meta = SUPPORTED_CURRENCIES.get(currency_code, {"symbol": currency_code, "decimal_places": 2})
    symbol = meta["symbol"]
    decimals = meta["decimal_places"]

    if currency_code == "TRY":
        # Turkish format: \u20ba1.234,56
        formatted = f"{amount:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"{symbol}{formatted}"
    else:
        # International format: $1,234.56
        return f"{symbol}{amount:,.{decimals}f}"


def _detect_country():
    """Detect user's country from request headers."""
    if not frappe.request:
        return "TR"

    # Check CF-IPCountry (Cloudflare)
    country = frappe.request.headers.get("CF-IPCountry", "")
    if country and country != "XX":
        return country.upper()

    # Check X-Country header (custom proxy)
    country = frappe.request.headers.get("X-Country", "")
    if country:
        return country.upper()

    # Check Accept-Language header as fallback
    accept_lang = frappe.request.headers.get("Accept-Language", "")
    if accept_lang:
        lang = accept_lang.split(",")[0].split("-")
        if len(lang) > 1:
            return lang[1].upper()
        # Map language to country
        lang_country = {
            "tr": "TR", "en": "US", "de": "DE", "fr": "FR",
            "it": "IT", "es": "ES", "zh": "CN", "ja": "JP",
        }
        return lang_country.get(lang[0].lower(), "US")

    return "US"
