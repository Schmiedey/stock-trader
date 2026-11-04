"""
Screener Engine for 3-Day Drop-Bounce Strategy.
Downloads real-time universe prices, calculates 3-day return performance,
and applies all edge-case exclusion and tie-breaker rules.
"""

import time
from typing import List, Dict, Set, Optional
import pandas as pd
import yfinance as yf
from src.universe import UniverseManager, get_canonical_symbol, clean_ticker


class DropBounceScreener:
    def __init__(self, universe_mgr: Optional[UniverseManager] = None):
        self.universe_mgr = universe_mgr or UniverseManager()

    def fetch_market_data(self, tickers: List[str]) -> pd.DataFrame:
        """
        Downloads the last 8 calendar days of daily price data for all universe tickers
        using yfinance multithreading.
        If today's daily Close bar contains NaNs (due to delayed EOD settlement on Yahoo Finance),
        automatically backfills today's latest price using intraday 1h bar data.
        """
        df = yf.download(
            tickers,
            period="8d",
            interval="1d",
            progress=False,
            group_by="ticker",
            threads=True
        )

        if not df.empty and len(tickers) > 0:
            try:
                sample_sym = tickers[0]
                has_nan = False
                if sample_sym in df and "Close" in df[sample_sym]:
                    sample_val = df[sample_sym]["Close"].iloc[-1]
                    if pd.isna(sample_val):
                        has_nan = True

                if has_nan:
                    df_today = yf.download(
                        tickers,
                        period="1d",
                        interval="1h",
                        progress=False,
                        group_by="ticker",
                        threads=True
                    )
                    for sym in tickers:
                        try:
                            if sym in df and sym in df_today and "Close" in df_today[sym]:
                                last_c = df_today[sym]["Close"].dropna()
                                if not last_c.empty:
                                    val = float(last_c.iloc[-1])
                                    df.loc[df.index[-1], (sym, "Close")] = val
                        except Exception:
                            pass
            except Exception:
                pass

        return df

    def scan_3day_drops(
        self,
        held_tickers: Optional[List[str]] = None,
        cooldown_tickers: Optional[List[str]] = None,
        limit: int = 15
    ) -> List[Dict]:
        """
        Runs the 3-day return ranking across the full S&P 500 + Nasdaq-100 universe.
        Applies:
        - Edge Case 1: Cooldown filter (tickers sold today on Day 5)
        - Edge Case 2: Already held tickers filter (no doubling down)
        - Edge Case 3: Dual-class shares deduplication (e.g. GOOG vs GOOGL, FOX vs FOXA)
        """
        held_tickers = held_tickers or []
        cooldown_tickers = cooldown_tickers or []

        # Canonicalize held and cooldown tickers
        held_canonical = {get_canonical_symbol(t) for t in held_tickers}
        cooldown_canonical = {get_canonical_symbol(t) for t in cooldown_tickers}

        universe = self.universe_mgr.get_universe()
        ticker_map = {item["symbol"]: item for item in universe}
        tickers = list(ticker_map.keys())

        # Download batch data
        data = self.fetch_market_data(tickers)

        results = []
        for sym in tickers:
            try:
                sub = data[sym]
                closes = sub["Close"].dropna()
                if len(closes) >= 4:
                    # closes.iloc[-1] = latest close / current price
                    # closes.iloc[-4] = price 3 trading days ago
                    p_now = float(closes.iloc[-1])
                    p_3d_ago = float(closes.iloc[-4])
                    if p_3d_ago > 0 and p_now > 0:
                        ret_3d = ((p_now - p_3d_ago) / p_3d_ago) * 100.0
                        results.append({
                            "ticker": sym,
                            "name": ticker_map.get(sym, {}).get("name", sym),
                            "indices": ticker_map.get(sym, {}).get("indices", []),
                            "current_price": round(p_now, 2),
                            "price_3d_ago": round(p_3d_ago, 2),
                            "return_3d_pct": round(ret_3d, 2),
                        })
            except Exception:
                continue

        # Sort from worst loser upward (ascending)
        results.sort(key=lambda x: x["return_3d_pct"])

        # Filter candidates applying edge case tie-breakers
        filtered_candidates = []
        seen_canonical: Set[str] = set(held_canonical)

        for cand in results:
            sym = cand["ticker"]
            canonical = get_canonical_symbol(sym)

            # Edge Case 1: Check cooldown
            if canonical in cooldown_canonical:
                cand["skip_reason"] = "COOLDOWN_DAY_5_EXIT"
                continue

            # Edge Case 2: Check already held
            if canonical in held_canonical:
                cand["skip_reason"] = "ALREADY_HELD"
                continue

            # Edge Case 3: Dual-class twin check
            if canonical in seen_canonical:
                cand["skip_reason"] = "DUAL_CLASS_TWIN_ALREADY_SELECTED"
                continue

            seen_canonical.add(canonical)
            filtered_candidates.append(cand)
            if len(filtered_candidates) >= limit:
                break

        return filtered_candidates

    def get_current_prices(self, tickers: List[str]) -> Dict[str, float]:
        """
        Fetches the latest current market price for a specific list of tickers.
        Uses yf.Tickers fast_info for instantaneous live pricing, falling back
        to 1h intraday close, then daily close if needed.
        """
        if not tickers:
            return {}
        prices = {}

        # 1. Primary: fast_info across all tickers in parallel
        try:
            batch = yf.Tickers(" ".join(tickers))
            for sym in tickers:
                try:
                    t = batch.tickers.get(sym) or yf.Ticker(sym)
                    p = getattr(t.fast_info, "last_price", None)
                    if p is None or pd.isna(p) or p <= 0:
                        p = getattr(t.fast_info, "regular_market_price", None)
                    if p is not None and not pd.isna(p) and p > 0:
                        prices[sym] = round(float(p), 2)
                except Exception:
                    pass
        except Exception:
            pass

        # 2. Secondary fallback for any missing tickers: 1d 1h intraday
        missing = [s for s in tickers if s not in prices]
        if missing:
            try:
                df_intra = yf.download(
                    missing,
                    period="1d",
                    interval="1h",
                    progress=False,
                    group_by="ticker",
                    threads=True
                )
                for sym in missing:
                    try:
                        sub = df_intra[sym] if len(missing) > 1 else df_intra
                        c = sub["Close"].dropna()
                        if not c.empty:
                            prices[sym] = round(float(c.iloc[-1]), 2)
                    except Exception:
                        pass
            except Exception:
                pass

        # 3. Tertiary fallback: 5d daily close
        missing = [s for s in tickers if s not in prices]
        if missing:
            try:
                df_daily = yf.download(
                    missing,
                    period="5d",
                    interval="1d",
                    progress=False,
                    group_by="ticker",
                    threads=True
                )
                for sym in missing:
                    try:
                        sub = df_daily[sym] if len(missing) > 1 else df_daily
                        c = sub["Close"].dropna()
                        if not c.empty:
                            prices[sym] = round(float(c.iloc[-1]), 2)
                    except Exception:
                        pass
            except Exception:
                pass

        return prices
