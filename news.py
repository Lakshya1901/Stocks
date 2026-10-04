"""
news.py — RSS headline fetching, company-name → NSE symbol extraction, and FinBERT sentiment.
"""

from config import get_logger
import feedparser
import requests
import json
from datetime import datetime, timezone, date, timedelta
from pathlib import Path
from config import RSS_FEEDS, BASE_DIR, MAX_ARTICLE_AGE_HOURS

logger = get_logger(__name__)

# Persistence file for deduplication across restarts
_SEEN_URLS_FILE = BASE_DIR / ".seen_urls.json"

# In-memory set of article URLs already processed this session.
# Prevents the same headline from triggering two trades if it appears
# in multiple feeds or if the bot restarts mid-session.
_seen_urls: set[str] = set()


def _load_seen_urls() -> None:
    """Load today's seen URLs from disk (survives mid-day restarts)."""
    global _seen_urls
    if not _SEEN_URLS_FILE.exists():
        return
    try:
        data = json.loads(_SEEN_URLS_FILE.read_text(encoding="utf-8"))
        saved_date = data.get("date", "")
        if saved_date == str(date.today()):
            _seen_urls = set(data.get("urls", []))
            logger.debug(f"Restored {len(_seen_urls)} seen URL(s) from disk")
        else:
            logger.debug("Seen URLs file is from a previous day — ignoring")
    except Exception as exc:
        logger.debug(f"Could not load seen URLs: {exc}")


def _save_seen_urls() -> None:
    """Persist seen URLs to disk with today's date."""
    try:
        data = {"date": str(date.today()), "urls": list(_seen_urls)}
        _SEEN_URLS_FILE.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass


_load_seen_urls()


def fetch_new_headlines() -> list[dict]:
    """
    Polls all configured RSS feeds and returns only new, unseen articles.

    Each returned item is a dict:
        {
            "headline": str,   # The article title
            "summary":  str,   # Short blurb (may be empty)
            "url":      str,   # Canonical link (used for deduplication)
            "source":   str,   # Feed name from config
            "published": str,  # Human-readable publish time
        }
    """
    new_articles: list[dict] = []

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }

    for source_name, feed_url in RSS_FEEDS.items():
        try:
            resp = requests.get(feed_url, headers=headers, timeout=10)
            if resp.status_code == 200:
                feed = feedparser.parse(resp.content)
                if feed.get("bozo"):
                    logger.warning(f"Feed '{source_name}' is malformed: {feed.bozo_exception}")
                    continue
            else:
                logger.warning(f"Feed '{source_name}' returned HTTP {resp.status_code}")
                continue
        except Exception as exc:
            logger.warning(f"Failed to fetch feed '{source_name}': {exc}")
            continue


        for entry in feed.entries:
            url = getattr(entry, "link", "")

            headline = getattr(entry, "title", "").strip()
            title_key = "title:" + " ".join(headline.lower().split())

            if not url or url in _seen_urls or title_key in _seen_urls:
                continue  # Already processed (same URL or same headline) or no URL to deduplicate on

            _seen_urls.add(url)
            _seen_urls.add(title_key)

            summary  = getattr(entry, "summary", "").strip()
            parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
            if not parsed:
                continue  # undated — can't tell if it's fresh news
            dt = datetime(*parsed[:6], tzinfo=timezone.utc)
            published = dt.strftime("%Y-%m-%d %H:%M UTC")
            if datetime.now(timezone.utc) - dt > timedelta(hours=MAX_ARTICLE_AGE_HOURS):
                continue  # stale article — not actionable news

            if headline:
                new_articles.append({
                    "headline": headline,
                    "summary":  summary,
                    "url":      url,
                    "source":   source_name,
                    "published": published,
                })
                logger.debug(f"New headline [{source_name}]: {headline}")

    logger.info(f"Fetched {len(new_articles)} new headline(s) across {len(RSS_FEEDS)} feed(s)")
    if new_articles:
        _save_seen_urls()
    return new_articles


def reset_seen_urls() -> None:
    """
    Clears the deduplication cache. Call this at the start of each trading day
    so that an article from yesterday that appears again today is reprocessed.
    """
    global _seen_urls
    _seen_urls = set()
    _save_seen_urls()
    logger.debug("Deduplication cache cleared for new trading day")


# =============================================================================
# Maps company names found in news headlines to NSE ticker symbols.
# =============================================================================
import re
import pandas as pd
from config import NSE_SYMBOLS_CSV

logger = get_logger(__name__)

# Words that appear capitalised in financial headlines but are NOT company names
_STOP_WORDS = {
    # Articles / prepositions / pronouns
    "A", "An", "And", "Are", "As", "At", "Be", "But", "By", "For", "From",
    "He", "Her", "His", "How", "If", "In", "Is", "It", "Its", "Of", "On",
    "Or", "Our", "Rs", "She", "That", "The", "Their", "This", "To", "Up",
    "Was", "We", "What", "Who", "Will", "With", "You", "Your", "Why", "When",
    # Generic suffixes in NSE names we strip before matching
    "Ltd", "Limited", "Private", "Pvt", "Corp", "Corporation", "Inc",
    # Financial and market jargon common in headlines
    "Share", "Shares", "Price", "Prices", "Stock", "Stocks", "Market", "Markets",
    "Live", "Update", "Updates", "Details", "Daily", "Performance", "Snapshot",
    "Overview", "Report", "Outlook", "Review", "Analysis", "Sensex", "Nifty",
    "Alert", "Alerts", "Buzz", "Buzzing", "Target", "Rating", "Brokerage", "Brokerages",
    "Gain", "Gains", "Fall", "Falls", "Drop", "Drops", "Surge", "Surges", "Rally",
    "Rallies", "Plunge", "Plunges", "Tank", "Tanks", "Rise", "Rises", "Dip", "Dips",
    "Slump", "Slumps", "Climb", "Climbs", "Jump", "Jumps", "High", "Low", "Top", "Bottom",
    "Today", "Week", "Year", "Month", "Bulls", "Bears", "Bull", "Bear", "Expert",
    "Experts", "Check", "Buy", "Sell", "Hold", "Board", "Crore", "Deal", "Dividend",
    "Earnings", "Fund", "Funds", "Mutual", "Lakh", "Margin", "Million", "Billion",
    "Dollar", "Profit", "Quarter", "Revenue", "Trade", "India", "Indian",
    # Months / days
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    # Acronyms that are not listed company tickers
    "US", "UK", "EU", "RBI", "SEBI", "NSE", "BSE", "IPO", "CEO", "CFO", "MD",
    "GDP", "FII", "DII", "MF", "ETF", "NFO", "SIP", "NAV", "IT", "AI", "SME", "GMP",
    "Q1", "Q2", "Q3", "Q4", "FY24", "FY25", "FY26", "FY27", "FY28", "SMA", "EMA", "VWAP",
    # Geographic / institutional words that appear in headlines
    "World", "Global", "National", "International", "Federal", "Central",
    "Reserve", "Government", "Govt", "Ministry", "Parliament",
    "North", "South", "East", "West", "Europe", "European",
    "American", "China", "Chinese", "Pakistan", "Russia", "Japan", "Japanese",
}

# Common abbreviations and media colloquialisms mapped to official NSE tickers
_COMMON_ALIASES = {
    "RIL": "RELIANCE",
    "RELIANCE INDUSTRIES": "RELIANCE",
    "HUL": "HINDUNILVR",
    "HINDUSTAN UNILEVER": "HINDUNILVR",
    "SBI": "SBIN",
    "STATE BANK OF INDIA": "SBIN",
    "STATE BANK": "SBIN",
    "L&T": "LT",
    "LARSEN": "LT",
    "LARSEN & TOUBRO": "LT",
    "M&M": "M&M",
    "MAHINDRA & MAHINDRA": "M&M",
    "TATA MOTORS": "TMCV",
    "TATAMOTORS": "TMCV",
    "BHARTI AIRTEL": "BHARTIARTL",
    "AIRTEL": "BHARTIARTL",
    "HCL TECH": "HCLTECH",
    "HCLTECH": "HCLTECH",
    "KOTAK BANK": "KOTAKBANK",
    "KOTAK MAHINDRA": "KOTAKBANK",
    "TATA STEEL": "TATASTEEL",
    "TATA POWER": "TATAPOWER",
    "TATA CONSUMER": "TATACONSUM",
    "MARUTI": "MARUTI",
    "MARUTI SUZUKI": "MARUTI",
    "SUN PHARMA": "SUNPHARMA",
    "DR REDDY": "DRREDDY",
    "DR REDDYS": "DRREDDY",
    "ZOMATO": "ETERNAL",
    "PAYTM": "PAYTM",
    "JIO FINANCIAL": "JIOFIN",
    "JIO FINANCE": "JIOFIN",
    "INFOSYS": "INFY",
    "INFY": "INFY",
    "TCS": "TCS",
    "WIPRO": "WIPRO",
    "ITC": "ITC",
    "YES BANK": "YESBANK",
    "HDFC BANK": "HDFCBANK",
    "ICICI BANK": "ICICIBANK",
    "AXIS BANK": "AXISBANK",
    "INDUSIND BANK": "INDUSINDBK",
    "BAJAJ FINANCE": "BAJFINANCE",
    "BAJAJ FINSERV": "BAJAJFINSV",
    "BAJAJ AUTO": "BAJAJ-AUTO",
    "ASIAN PAINTS": "ASIANPAINT",
    "ULTRATECH CEMENT": "ULTRACEMCO",
    "ULTRATECH": "ULTRACEMCO",
    "TITAN": "TITAN",
    "TITAN COMPANY": "TITAN",
    "JSW STEEL": "JSWSTEEL",
    "ADANI ENTERPRISES": "ADANIENT",
    "ADANI PORTS": "ADANIPORTS",
    "ADANI POWER": "ADANIPOWER",
    "ADANI GREEN": "ADANIGREEN",
    "VEDANTA": "VEDL",
    "COAL INDIA": "COALINDIA",
    "POWER GRID": "POWERGRID",
    "NTPC": "NTPC",
}


# These are common English / sector words that appear in many NSE company names
# AND in general market headlines. Matching them as a single word would cause
# far too many false positives, so they are only allowed to form part of a
# multi-word phrase (N >= 2) that is matched in Pass 2.
_GENERIC_INDUSTRY_WORDS = {
    "AUTOMATION", "STEEL", "POWER", "ENERGY", "TECH", "TECHNOLOGY",
    "TECHNOLOGIES", "FINANCE", "FINANCIAL", "AUTO", "AUTOMOBILE",
    "AUTOMOBILES", "PHARMA", "HEALTH", "HEALTHCARE", "DIGITAL", "MEDIA",
    "CEMENT", "CHEMICAL", "CHEMICALS", "TEXTILE", "TEXTILES", "MINING",
    "BANK", "BANKS", "BANKING", "INSURANCE", "REALTY", "INFRA", "INFRASTRUCTURE",
    "LOGISTICS", "RETAIL", "CONSUMER", "AGRO", "AGRICULTURE", "FOOD",
    "BEVERAGES", "HOUSING", "TRANSPORT", "ENGINEERING", "CONSTRUCTION",
    "MOTORS", "PAINTS", "GLASS", "PAPER", "PLASTIC", "RUBBER",
}
_NSE_GENERICS = {
    "LTD", "LIMITED", "PRIVATE", "PVT", "AND", "THE", "CO", "CORP",
    "CORPORATION", "INC", "ENTERPRISES", "SOLUTIONS", "SERVICES",
    "HOLDINGS", "INVESTMENTS", "VENTURES", "CAPITAL",
}

# --- Load NSE symbol table at startup ---
# _name_words maps symbol -> frozenset of significant words in the company name
# e.g. "TATAMOTORS" -> frozenset({"TATA", "MOTORS"})
_name_to_symbol: dict[str, str] = {}       # "RELIANCE INDUSTRIES LTD" -> "RELIANCE"
_symbol_to_words: dict[str, frozenset] = {} # "RELIANCE" -> frozenset({"RELIANCE","INDUSTRIES"})
_all_symbols: set[str] = set()


import os
import time

def _ensure_up_to_date_symbols() -> None:
    """
    Downloads the latest NSE equity list if it doesn't exist or is older than 7 days.
    This ensures we pick up new IPOs and company name changes automatically.
    """
    need_download = True
    if os.path.exists(NSE_SYMBOLS_CSV):
        file_age_days = (time.time() - os.path.getmtime(NSE_SYMBOLS_CSV)) / (24 * 3600)
        if file_age_days < 7:
            need_download = False
            
    if need_download:
        logger.info("Downloading latest NSE symbols list from NSE India...")
        url = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 200 and len(resp.content) > 1000:
                with open(NSE_SYMBOLS_CSV, "wb") as f:
                    f.write(resp.content)
                logger.info("Successfully updated NSE symbols list.")
            else:
                logger.warning(f"NSE returned status {resp.status_code} while updating symbols")
        except Exception as e:
            logger.error(f"Failed to update NSE symbols list: {e}")


def _load_nse_symbols() -> None:
    global _name_to_symbol, _symbol_to_words, _all_symbols
    _ensure_up_to_date_symbols()
    try:
        df = pd.read_csv(NSE_SYMBOLS_CSV, on_bad_lines="skip", usecols=["SYMBOL", "NAME OF COMPANY"])
        df["SYMBOL"] = df["SYMBOL"].astype(str).str.strip().str.upper()
        df["NAME OF COMPANY"] = df["NAME OF COMPANY"].astype(str).str.strip().str.upper()

        # Filter out empty rows
        df = df[(df["SYMBOL"].str.len() > 0) & (df["NAME OF COMPANY"].str.len() > 0)]

        for symbol in df["SYMBOL"].unique():
            _all_symbols.add(symbol)

        for _, row in df.iterrows():
            symbol = row["SYMBOL"]
            name   = row["NAME OF COMPANY"]
            _name_to_symbol[name] = symbol
            # Strip generic words to get the "significant" words of the company
            words = frozenset(
                w for w in name.split() if w not in _NSE_GENERICS and len(w) > 1
            )
            _symbol_to_words[symbol] = words
        logger.info(f"Loaded {len(_name_to_symbol)} NSE symbols from {NSE_SYMBOLS_CSV}")
    except FileNotFoundError:
        logger.error(
            f"NSE equity list not found at '{NSE_SYMBOLS_CSV}'. "
            "Run: curl -L -o nse_equity_list.csv "
            "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
        )


_load_nse_symbols()

# Matches any word starting with an uppercase letter (Title-Case or ALL-CAPS)
_CAP_WORD_RE = re.compile(r"\b([A-Z][a-zA-Z0-9&]*)\b")
# Only truly ALL-CAPS words (for Pass 1 direct ticker check)
_ALLCAPS_RE  = re.compile(r"\b([A-Z][A-Z0-9&]{1,14})\b")


def _word_matches_company_word(phrase_word: str, company_word: str) -> bool:
    """
    True if phrase_word equals company_word, or if company_word starts with
    phrase_word (minimum 5 chars). This handles common abbreviations where the
    headline uses a shortened form:
      PHARMA (5) -> matches PHARMACEUTICAL  ✓
      TECH   (4) -> no prefix match (< 5 chars), exact match only
    Intentionally does NOT match phrase_word.startswith(company_word) to avoid
    "AUTOMATION" matching "AUTO" in company names.
    """
    if phrase_word == company_word:
        return True
    # Only allow phrase word as a prefix of the company word (not reverse)
    if len(phrase_word) >= 5 and company_word.startswith(phrase_word):
        return True
    return False


def _word_inclusion_match(phrase_words: list[str]) -> str | None:
    """
    Find the NSE company whose significant words contain ALL phrase words
    (using prefix matching for words >= 5 chars).
    Among candidates, prefer the company with fewest unmatched extra words.
    Returns the NSE symbol or None.
    """
    if not phrase_words:
        return None

    upper_phrase = [w.upper() for w in phrase_words]
    best_symbol  = None
    best_extra   = float("inf")

    for symbol, company_words in _symbol_to_words.items():
        company_word_list = list(company_words)

        # Check every phrase word has at least one match in the company name
        all_matched = True
        for pw in upper_phrase:
            if not any(_word_matches_company_word(pw, cw) for cw in company_word_list):
                all_matched = False
                break

        if not all_matched:
            continue

        # Count company words that are not covered by any phrase word
        extra = sum(
            1 for cw in company_word_list
            if not any(_word_matches_company_word(pw, cw) for pw in upper_phrase)
        )
        if extra < best_extra:
            best_extra  = extra
            best_symbol = symbol

    if best_symbol:
        logger.debug(
            f"Word-inclusion: {phrase_words} -> {best_symbol} (extra_words={best_extra})"
        )
    return best_symbol


def extract_symbol(headline: str) -> str | None:
    """
    Returns an NSE ticker symbol if a company can be reliably identified
    in the headline, otherwise returns None.
    """
    clean_headline = headline.strip()
    upper_headline = clean_headline.upper()

    # --- Pass 0: Common financial media aliases & acronyms ---
    # Prioritise longest multi-word aliases first ("Tata Motors" before "Tata")
    for alias in sorted(_COMMON_ALIASES.keys(), key=len, reverse=True):
        pattern = r"\b" + re.escape(alias) + r"\b"
        if re.search(pattern, upper_headline):
            sym = _COMMON_ALIASES[alias]
            logger.debug(f"Pass0 alias match: '{alias}' -> {sym}")
            return sym

    # --- Pass 1: ALL-CAPS ticker already written in the original headline ---
    for word in _ALLCAPS_RE.findall(clean_headline):
        word_up = word.upper()
        if len(word) >= 2 and word_up in _all_symbols and word_up not in _STOP_WORDS:
            logger.debug(f"Pass1 direct ticker: {word_up}")
            return word_up

    # --- Build pool of candidate words for phrase matching ---
    # Include ALL capitalised words (Title-Case + ALL-CAPS), except stop words.
    candidate_words = [
        w for w in _CAP_WORD_RE.findall(clean_headline)
        if w not in _STOP_WORDS and w.upper() not in _STOP_WORDS
    ]

    if not candidate_words:
        return None

    # --- Pass 2: N-gram phrase matching (4 words down to 2) ---
    for n in range(min(4, len(candidate_words)), 1, -1):
        for i in range(len(candidate_words) - n + 1):
            phrase = candidate_words[i : i + n]
            sym = _word_inclusion_match(phrase)
            if sym:
                return sym

    # --- Pass 3: Single capitalised word fallback ---
    # Require at least 4 characters and exclude generic industry & stop words
    for word in candidate_words:
        word_up = word.upper()
        if len(word) < 4 or word_up in _GENERIC_INDUSTRY_WORDS or word_up in _STOP_WORDS:
            continue
        # Exact word, and only if exactly one company has it: prefix matching turned
        # "Micron" into 20MICRONS, and shared words turned "Gold" into SENCO, "Adani" into ADANIENT
        matches = [sym for sym, words in _symbol_to_words.items() if word_up in words]
        if len(matches) > 1:
            # "Cyient" = CYIENT (name is exactly that word), not CYIENTDLM
            matches = [sym for sym in matches if _symbol_to_words[sym] == {word_up}]
        if len(matches) == 1:
            logger.debug(f"Pass3 single word: {word} -> {matches[0]}")
            return matches[0]

    logger.debug(f"No symbol found in: '{headline[:70]}'")
    return None


# =============================================================================
# Financial sentiment classifier powered by FinBERT.
# =============================================================================
os.environ["USE_TF"] = "0"
os.environ["USE_TORCH"] = "1"

import torch
from transformers import AutoTokenizer, AutoModelForSequenceClassification

logger = get_logger(__name__)

# Load model and tokenizer once at module import time
logger.info("Loading FinBERT model...")
_MODEL_NAME = "ProsusAI/finbert"
_tokenizer = AutoTokenizer.from_pretrained(_MODEL_NAME)
_model = AutoModelForSequenceClassification.from_pretrained(_MODEL_NAME)
_model.eval()
logger.info("FinBERT model ready")

_ID2LABEL = _model.config.id2label  # {0: 'positive', 1: 'negative', 2: 'neutral'}


def analyze(text: str) -> tuple[str, float]:
    """
    Runs the given text through FinBERT and returns a (label, score) tuple.

    label is one of:  "positive" | "negative" | "neutral"
    score is a float between 0.0 and 1.0 (model confidence)

    Examples:
        "Reliance Industries posts record quarterly profits" -> ("positive", 0.97)
        "Tata Motors recalls 12,000 vehicles due to brake defect" -> ("negative", 0.92)
        "SEBI releases new circular on margin norms"  -> ("neutral", 0.78)
    """
    try:
        inputs = _tokenizer(
            text,
            return_tensors="pt",
            max_length=512,
            truncation=True,
            padding=False,
        )
        with torch.no_grad():
            logits = _model(**inputs).logits
            probs = torch.nn.functional.softmax(logits, dim=-1)[0]
            top_idx = int(torch.argmax(probs).item())
            label = _ID2LABEL[top_idx].lower()
            score = round(float(probs[top_idx].item()), 4)

        logger.debug(f"Sentiment [{label.upper()} {score:.2f}]: {text[:80]}...")
        return label, score
    except Exception as exc:
        logger.error(f"FinBERT inference failed: {exc}")
        return "neutral", 0.0
