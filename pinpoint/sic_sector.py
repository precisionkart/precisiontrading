"""sic_sector.py — map SEC SIC codes to Finviz-equivalent sector names.

Polygon/Massive's /v3/reference/tickers returns `sic_code` (a 4-digit string)
and `sic_description` — NOT GICS sectors. Downstream code (themes, sector
heatmap, industry-RS gate) expects Finviz-style sector names ("Technology",
"Financial Services", "Healthcare", ...). This module bridges the two.

The mapping follows the SEC SIC division structure but special-cases the
manufacturing codes that matter most for a momentum scanner — e.g. computers
(357x) and semiconductors/electronics (36xx) map to Technology, not generic
"manufacturing", and pharma (283x) / medical instruments map to Healthcare.
It's lossy by nature (SIC predates the software economy), so it's tuned to put
the names a swing trader actually scans in the right bucket.
"""

from __future__ import annotations

# Finviz canonical sector names we emit.
TECH = "Technology"
FIN = "Financial Services"
HEALTH = "Healthcare"
CONS_CYC = "Consumer Cyclical"
CONS_DEF = "Consumer Defensive"
ENERGY = "Energy"
INDUST = "Industrials"
MATERIALS = "Basic Materials"
REALEST = "Real Estate"
UTIL = "Utilities"
COMMS = "Communication Services"

# Ordered specific overrides (checked first); each is an inclusive (lo, hi).
# Order matters — narrower tech/health/aero codes precede the broad bands.
_SPECIFIC: list[tuple[int, int, str]] = [
    (2833, 2836, HEALTH),    # medicinal chemicals / pharmaceuticals / biologics
    (3570, 3579, TECH),      # computer & office equipment
    (3600, 3699, TECH),      # electronic & electrical equipment, semiconductors
    (3720, 3728, INDUST),    # aircraft & aerospace
    (3812, 3812, INDUST),    # search/navigation/defense systems
    (3826, 3829, HEALTH),    # lab/medical/measuring instruments
    (3840, 3851, HEALTH),    # surgical/medical instruments & supplies
    (4810, 4813, COMMS),     # telephone communications
    (4820, 4899, COMMS),     # telegraph / radio / TV / communications services
    (7370, 7379, TECH),      # computer programming, software, data processing
    (7800, 7841, COMMS),     # motion pictures / entertainment
    (7990, 7999, COMMS),     # recreation/entertainment services
    (5400, 5499, CONS_DEF),  # food stores (grocery)
    (2080, 2099, CONS_DEF),  # beverages
    (8000, 8099, HEALTH),    # health services
]

# Broad SIC divisions (inclusive lo, hi) checked after specifics.
_BANDS: list[tuple[int, int, str]] = [
    (100, 999, CONS_DEF),    # agriculture / forestry / fishing
    (1000, 1099, MATERIALS), # metal mining
    (1100, 1299, ENERGY),    # coal mining
    (1300, 1399, ENERGY),    # oil & gas extraction
    (1400, 1499, MATERIALS), # nonmetallic minerals mining
    (1500, 1799, INDUST),    # construction
    (2000, 2199, CONS_DEF),  # food & kindred / tobacco
    (2200, 2399, CONS_CYC),  # textiles & apparel
    (2400, 2599, CONS_CYC),  # lumber, furniture
    (2600, 2699, MATERIALS), # paper
    (2700, 2799, COMMS),     # printing & publishing
    (2800, 2899, MATERIALS), # chemicals (non-pharma)
    (2900, 2999, ENERGY),    # petroleum refining
    (3000, 3399, MATERIALS), # rubber, plastics, stone, primary metal
    (3400, 3569, INDUST),    # fabricated metal, industrial machinery
    (3580, 3599, INDUST),    # general industrial machinery
    (3700, 3799, CONS_CYC),  # transportation equipment (autos, etc.)
    (3800, 3999, INDUST),    # instruments & misc manufacturing
    (4000, 4799, INDUST),    # transportation (rail, trucking, air, pipelines)
    (4900, 4999, UTIL),      # electric/gas/sanitary utilities
    (5000, 5199, INDUST),    # wholesale trade
    (5200, 5999, CONS_CYC),  # retail trade
    (6000, 6199, FIN),       # depository & non-depository credit
    (6200, 6299, FIN),       # security & commodity brokers
    (6300, 6499, FIN),       # insurance
    (6500, 6599, REALEST),   # real estate
    (6700, 6799, FIN),       # holding & investment offices
    (7000, 7299, CONS_CYC),  # hotels & personal/business services
    (7300, 7399, TECH),      # business services (default toward tech/services)
    (7400, 7799, INDUST),    # misc services
    (8100, 8999, INDUST),    # legal/educational/social/engineering services
]


def sic_to_sector(sic_code) -> str:
    """Return a Finviz-style sector name for a SIC code (str/int/None).

    Empty string when the code is missing or unparseable — callers treat that
    as "unknown sector" (it simply won't fire the sector/theme layers)."""
    if sic_code is None:
        return ""
    try:
        n = int(str(sic_code).strip())
    except (TypeError, ValueError):
        return ""
    for lo, hi, sector in _SPECIFIC:
        if lo <= n <= hi:
            return sector
    for lo, hi, sector in _BANDS:
        if lo <= n <= hi:
            return sector
    return ""


def industry_from_description(sic_description) -> str:
    """Title-case the SIC description into an 'industry' string. Polygon gives
    things like 'ELECTRONIC COMPUTERS' / 'SEMICONDUCTORS & RELATED DEVICES'."""
    if not sic_description:
        return ""
    return " ".join(w.capitalize() for w in str(sic_description).split())
