"""
Universe manager for S&P 500 + Nasdaq-100 constituents (~515 stocks).
Handles caching, Wikipedia updates, ticker standardization, and dual-class share deduplication.
"""

import os
import json
import ssl
import time
import urllib.request
from io import StringIO
from typing import List, Dict, Set, Tuple
import pandas as pd

# Dual-class mappings: map twin share class to the more liquid / primary index constituent
# Edge Case 3: Treat dual-class shares of the same company as a single ticker.
DUAL_CLASS_MAP: Dict[str, str] = {
    "GOOG": "GOOGL",
    "GOOGL": "GOOGL",
    "FOX": "FOXA",
    "FOXA": "FOXA",
    "NWS": "NWSA",
    "NWSA": "NWSA",
    "UA": "UAA",
    "UAA": "UAA",
    "BRK-A": "BRK-B",
    "BRK-B": "BRK-B",
    "BRK.A": "BRK-B",
    "BRK.B": "BRK-B",
    "CWEN-A": "CWEN",
    "CWEN.A": "CWEN",
}


def clean_ticker(t: str) -> str:
    """Normalizes ticker symbol for yfinance and market consistency."""
    return t.strip().upper().replace(".", "-")


def get_canonical_symbol(ticker: str) -> str:
    """Returns the canonical/preferred ticker for dual-class shares."""
    c = clean_ticker(ticker)
    return DUAL_CLASS_MAP.get(c, c)


def are_dual_class_twins(ticker1: str, ticker2: str) -> bool:
    """Returns True if ticker1 and ticker2 represent the same underlying company."""
    return get_canonical_symbol(ticker1) == get_canonical_symbol(ticker2)


class UniverseManager:
    def __init__(self, cache_file: str = "data/universe.json", cache_ttl_days: int = 7):
        self.cache_file = cache_file
        self.cache_ttl_seconds = cache_ttl_days * 86400

    def fetch_sp500_tickers(self) -> List[Dict[str, str]]:
        """Scrapes S&P 500 constituents from Wikipedia."""
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        html = urllib.request.urlopen(req, context=ctx).read().decode("utf-8")
        tables = pd.read_html(StringIO(html))
        df = tables[0]
        results = []
        for _, row in df.iterrows():
            sym = clean_ticker(str(row["Symbol"]))
            name = str(row.get("Security", ""))
            results.append({"symbol": sym, "name": name, "index": "SP500"})
        return results

    def fetch_nasdaq100_tickers(self) -> List[Dict[str, str]]:
        """Scrapes Nasdaq-100 constituents from Wikipedia."""
        url = "https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        html = urllib.request.urlopen(req, context=ctx).read().decode("utf-8")
        tables = pd.read_html(StringIO(html))
        df = tables[0]
        col_sym = "Ticker" if "Ticker" in df.columns else "Symbol"
        col_name = "Company" if "Company" in df.columns else df.columns[0]
        results = []
        for _, row in df.iterrows():
            sym = clean_ticker(str(row[col_sym]))
            name = str(row.get(col_name, ""))
            results.append({"symbol": sym, "name": name, "index": "NASDAQ100"})
        return results

    def get_universe(self, force_refresh: bool = False) -> List[Dict[str, str]]:
        """
        Retrieves the combined S&P 500 + Nasdaq-100 universe.
        Uses local JSON cache if fresh, otherwise re-scrapes and updates cache.
        """
        if not force_refresh and os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r") as f:
                    cached_data = json.load(f)
                cached_time = cached_data.get("timestamp", 0)
                if time.time() - cached_time < self.cache_ttl_seconds:
                    return cached_data.get("constituents", [])
            except Exception:
                pass

        # Fetch fresh
        sp500 = self.fetch_sp500_tickers()
        nasdaq = self.fetch_nasdaq100_tickers()

        # Combine and deduplicate
        combined_dict = {}
        for item in sp500:
            combined_dict[item["symbol"]] = {
                "symbol": item["symbol"],
                "name": item["name"],
                "indices": ["SP500"],
            }
        for item in nasdaq:
            sym = item["symbol"]
            if sym in combined_dict:
                combined_dict[sym]["indices"].append("NASDAQ100")
            else:
                combined_dict[sym] = {
                    "symbol": sym,
                    "name": item["name"],
                    "indices": ["NASDAQ100"],
                }

        constituents = sorted(list(combined_dict.values()), key=lambda x: x["symbol"])

        os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
        with open(self.cache_file, "w") as f:
            json.dump({
                "timestamp": time.time(),
                "total_count": len(constituents),
                "constituents": constituents,
            }, f, indent=2)

        return constituents

    def get_ticker_list(self, force_refresh: bool = False) -> List[str]:
        """Returns just the list of cleaned ticker strings."""
        constituents = self.get_universe(force_refresh=force_refresh)
        return [c["symbol"] for c in constituents]
