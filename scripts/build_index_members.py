"""Rebuild ``src/portfolio_dashboard/index_members.csv`` from public member lists.

Manually started; never run in CI and never imported by the app. Reads the
Wikipedia member lists, keeps hand-maintained ticker maps where a page has no
Yahoo ticker, and stops with an error when a page lists a company the map does
not know (a new index member). Tickers the app cannot price (symbol format,
exchange not connected; ``index_members.processable``) are left out. Every other
ticker is confirmed once on Yahoo (symbol, quote type EQUITY); only the
``TOP_N`` largest members by market cap are kept,
so one click on the start page loads at most ``TOP_N`` companies instead of
up to 500 (Yahoo rate limit on the shared host IP). Nikkei 225 names come from
Yahoo because the source lists Japanese names.

    uv run --locked --with lxml python scripts/build_index_members.py

Review the CSV diff before committing, then update ``SOURCE_DATE`` in
``index_members.py``.
"""

import argparse
import csv
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from pathlib import Path
from urllib.parse import quote

import lxml.html
import pandas as pd
import yfinance as yf

from portfolio_dashboard.index_members import processable

OUTPUT = Path(__file__).parents[1] / "src" / "portfolio_dashboard" / "index_members.csv"
TOP_N = 50

MDAX = {
    "Aixtron": "AIXA", "Aroundtown": "AT1", "Aumovio": "AMV0", "Aurubis": "NDA", "Auto1 Group": "AG1",
    "Bechtle": "BC8", "Bilfinger": "GBF", "CTS Eventim": "EVD", "Delivery Hero": "DHER",
    "Deutsche Lufthansa": "LHA", "Deutz": "DEZ", "DWS Group": "DWS", "Elmos Semiconductor": "ELG",
    "Evonik Industries": "EVK", "Flatexdegiro": "FTK", "Fraport": "FRA", "Freenet": "FNTN",
    "Fuchs": "FPE3", "Hella": "HLE", "Hensoldt": "HAG", "Ionos Group": "IOS", "Jenoptik": "JEN",
    "K+S": "SDF", "Kion Group": "KGX", "Knorr-Bremse": "KBX", "Krones": "KRN", "Lanxess": "LXS",
    "LEG Immobilien": "LEG", "Nemetschek": "NEM", "Nordex": "NDX1", "Porsche AG": "P911",
    "Porsche Automobil Holding": "PAH3", "Puma": "PUM", "Rational": "RAA", "Renk": "R3NK",
    "RTL Group": "RRTL", "Salzgitter": "SZG", "Sartorius": "SRT3", "Schaeffler": "SHA0",
    "Siltronic": "WAF", "Ströer": "SAX", "Suss Microtec": "SMHN", "TAG Immobilien": "TEG",
    "Talanx": "TLX", "Thyssenkrupp": "TKA", "TKMS": "TKMS", "Traton": "8TRA", "TUI": "TUI1",
    "United Internet": "UTDI", "Wacker Chemie": "WCH",
}
NASDAQ_100 = {
    "Adobe": "ADBE", "ADP": "ADP", "AMD": "AMD", "Airbnb": "ABNB", "Alnylam Pharmaceuticals": "ALNY",
    "Alphabet": "GOOGL", "Amazon": "AMZN", "AEP": "AEP", "Amgen": "AMGN", "Analog Devices": "ADI",
    "Apple": "AAPL", "Applied Materials": "AMAT", "AppLovin": "APP", "Arm": "ARM", "ASML": "ASML",
    "Astera Labs": "ALAB", "Autodesk": "ADSK", "Axon": "AXON", "Baker Hughes": "BKR",
    "Booking Holdings": "BKNG", "Broadcom": "AVGO", "Cadence": "CDNS", "Cintas": "CTAS",
    "Cisco": "CSCO", "Coca-Cola Europacific Partners": "CCEP", "Comcast": "CMCSA",
    "Constellation Energy": "CEG", "Copart": "CPRT", "CoreWeave": "CRWV", "Costco": "COST",
    "CrowdStrike": "CRWD", "CSX": "CSX", "Datadog": "DDOG", "Dexcom": "DXCM",
    "Diamondback Energy": "FANG", "DoorDash": "DASH", "Exelon": "EXC", "Fastenal": "FAST",
    "Ferrovial": "FER", "Fortinet": "FTNT", "GE HealthCare": "GEHC", "Gilead Sciences": "GILD",
    "Honeywell Aerospace": "HONA", "Honeywell Technologies": "HON", "Idexx Laboratories": "IDXX",
    "Intel": "INTC", "Intuit": "INTU", "Intuitive Surgical": "ISRG", "Keurig Dr Pepper": "KDP",
    "KLA Corporation": "KLAC", "Lam Research": "LRCX", "Linde": "LIN", "Lumentum": "LITE",
    "Marriott International": "MAR", "Marvell Technology": "MRVL", "MercadoLibre": "MELI",
    "Meta Platforms": "META", "Microchip Technology": "MCHP", "Micron Technology": "MU",
    "Microsoft": "MSFT", "MicroStrategy": "MSTR", "Moderna": "MRNA",
    "Mondelez International": "MDLZ", "Monolithic Power Systems": "MPWR",
    "Monster Beverage": "MNST", "Nebius Group": "NBIS", "Netflix": "NFLX", "Nvidia": "NVDA",
    "NXP": "NXPI", "O'Reilly Auto Parts": "ORLY", "Old Dominion": "ODFL", "Paccar": "PCAR",
    "Palantir": "PLTR", "Palo Alto Networks": "PANW", "Paychex": "PAYX", "PayPal": "PYPL",
    "PDD Holdings": "PDD", "PepsiCo": "PEP", "Qualcomm": "QCOM", "Regeneron": "REGN",
    "Rocket Lab": "RKLB", "Roper Technologies": "ROP", "Ross Stores": "ROST", "Sandisk": "SNDK",
    "Seagate Technology": "STX", "Shopify": "SHOP", "SpaceX": "SPCX", "Starbucks": "SBUX",
    "Synopsys": "SNPS", "T-Mobile US": "TMUS", "Take-Two Interactive": "TTWO", "Teradyne": "TER",
    "Tesla": "TSLA", "Texas Instruments": "TXN", "Thomson Reuters": "TRI", "Vertex": "VRTX",
    "Walmart": "WMT", "Western Digital": "WDC", "Workday": "WDAY", "Xcel Energy": "XEL",
}
DOW_JONES = {
    "3M": "MMM", "Alphabet": "GOOGL", "Amazon": "AMZN", "American Express": "AXP", "Amgen": "AMGN",
    "Apple": "AAPL", "Boeing": "BA", "Caterpillar": "CAT", "Chevron": "CVX", "Cisco": "CSCO",
    "Coca-Cola": "KO", "Disney": "DIS", "Goldman Sachs": "GS", "Home Depot": "HD",
    "Honeywell Technologies": "HON", "IBM": "IBM", "Johnson & Johnson": "JNJ",
    "JPMorgan Chase": "JPM", "McDonald's": "MCD", "Merck": "MRK", "Microsoft": "MSFT", "Nike": "NKE",
    "Nvidia": "NVDA", "Procter & Gamble": "PG", "Salesforce": "CRM", "Sherwin-Williams": "SHW",
    "Travelers": "TRV", "UnitedHealth": "UNH", "Visa": "V", "Walmart": "WMT",
}
# Second share classes report the same company market cap; one class per company.
SP500_SKIP = {"GOOG", "FOX", "NWS"}


def page(lang: str, title: str) -> str:
    """:param lang: Wikipedia language code.
    :param title: Article title.
    :return: Article HTML.
    """
    url = f"https://{lang}.wikipedia.org/wiki/{quote(title)}"
    request = urllib.request.Request(url, headers={"User-Agent": "index-members-script/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")


def tables(html: str, *columns: str) -> list[pd.DataFrame]:
    """:param html: Page HTML.
    :param columns: Column names a member table must contain.
    :return: All tables with these columns.
    """
    found = pd.read_html(StringIO(html), flavor="lxml")
    return [t for t in found if set(columns) <= set(map(str, t.columns))]


def navbox(html: str, title: str) -> list[str]:
    """:param html: Page HTML.
    :param title: Navbox title, e.g. "Components of the Dow Jones Industrial Average".
    :return: Company names listed in that navbox.
    """
    for box in lxml.html.fromstring(html).xpath('//div[contains(@class, "navbox")]'):
        heading = box.xpath('.//th[contains(@class, "navbox-title")]')
        if heading and title.casefold() in heading[0].text_content().casefold():
            return [li.text_content().strip() for li in box.xpath('.//td[contains(@class, "navbox-list")]//li')]
    raise SystemExit(f"Navbox not found: {title}")


def mapped(index: str, names: list[str], tickers: dict[str, str], match) -> list[tuple[str, str]]:
    """Map page names to hand-maintained tickers; stop on unknown or missing members.

    :param index: Index name for error messages.
    :param names: Member names from the page.
    :param tickers: Short name -> Yahoo ticker.
    :param match: ``match(page_name, short_name) -> bool``.
    :return: (short name, ticker) per member.
    """
    rows = []
    for name in names:
        hits = [short for short in tickers if match(name, short)]
        if len(hits) != 1:
            raise SystemExit(f"{index}: {name!r} matches {hits}; update the ticker map")
        rows.append((hits[0], tickers[hits[0]]))
    if len(rows) != len(tickers):
        missing = sorted(set(tickers) - {short for short, _ in rows})
        raise SystemExit(f"{index}: no longer listed {missing}; update the ticker map")
    return rows


def build() -> list[tuple[str, str, str]]:
    """:return: (index, company, Yahoo ticker) rows for all supported indices."""
    rows = []

    def add(index, pairs):
        rows.extend((index, str(name).strip(), ticker.strip()) for name, ticker in pairs)

    dax = tables(page("de", "DAX"), "Name", "Symbol")[0]
    add("DAX", ((n, s + ".DE") for n, s in zip(dax["Name"], dax["Symbol"])))

    mdax = next(t for t in tables(page("de", "MDAX"), "Name", "Branche") if len(t) >= 45)
    names = [n for n in mdax["Name"].dropna()]
    add("MDAX", ((n, s + ".DE") for n, s in mapped(
        "MDAX", names, MDAX, lambda page_name, short: page_name.casefold().startswith(short.casefold())
    )))

    stoxx = tables(page("en", "EURO STOXX 50"), "Ticker", "Name")[0]
    add("Euro Stoxx 50", zip(stoxx["Name"], stoxx["Ticker"]))
    cac = tables(page("en", "CAC 40"), "Company", "Ticker")[0]
    add("CAC 40", zip(cac["Company"], cac["Ticker"]))
    smi = tables(page("en", "Swiss Market Index"), "Name", "Ticker")[0].dropna(subset=["Ticker"])
    add("SMI", ((n, s + ".SW") for n, s in zip(smi["Name"], smi["Ticker"])))

    sp500 = tables(page("en", "List of S&P 500 companies"), "Symbol", "Security")[0]
    add("S&P 500", ((n, s.replace(".", "-")) for n, s in zip(sp500["Security"], sp500["Symbol"])
                    if s not in SP500_SKIP))
    add("Nasdaq 100", mapped(
        "Nasdaq 100", navbox(page("en", "Nasdaq-100"), "NASDAQ-100"), NASDAQ_100, str.__eq__
    ))
    add("Dow Jones", mapped(
        "Dow Jones", navbox(page("en", "Dow Jones Industrial Average"), "Components of the Dow Jones"),
        DOW_JONES, str.__eq__,
    ))

    nikkei = pd.concat(tables(page("ja", "日経平均株価"), "証券コード", "銘柄"))
    # Placeholder names; replaced by Yahoo's English names in ``main``.
    add("Nikkei 225", ((code, str(code) + ".T") for code in nikkei["証券コード"]))

    hsi = tables(page("en", "Hang Seng Index"), "Ticker", "Name", "Sub-index")[0]
    add("Hang Seng", ((n, t.split(":")[-1].strip().replace("\xa0", "").zfill(4) + ".HK")
                      for t, n in zip(hsi["Ticker"], hsi["Name"])))
    nifty = tables(page("en", "NIFTY 50"), "Company name", "Symbol")[0].dropna(subset=["Symbol"])
    add("Nifty 50", ((n, s + ".NS") for n, s in zip(nifty["Company name"], nifty["Symbol"])))
    return rows


def yahoo_infos(tickers: set[str]) -> dict[str, dict]:
    """:param tickers: Yahoo tickers.
    :return: Ticker -> confirmed info; stops listing every ticker Yahoo does not confirm.
    """
    def info(ticker):
        try:
            data = yf.Ticker(ticker).get_info()
        except Exception as error:  # noqa: BLE001 - collect all failures, then stop
            return ticker, f"{type(error).__name__}"
        if data.get("symbol") != ticker or data.get("quoteType") != "EQUITY":
            return ticker, "symbol/quote type not confirmed"
        return ticker, data

    # Few workers: about 1,300 requests in one run would otherwise trip the rate limit.
    with ThreadPoolExecutor(4) as pool:
        result = dict(pool.map(info, sorted(tickers)))
    problems = [f"{ticker}: {value}" for ticker, value in result.items() if isinstance(value, str)]
    if problems:
        raise SystemExit("Not confirmed on Yahoo:\n" + "\n".join(problems))
    return result


def largest(rows: list[tuple[str, str, str]], infos: dict[str, dict]) -> list[tuple[str, str, str]]:
    """:param rows: (index, company, ticker) rows of all members.
    :param infos: Confirmed Yahoo info per ticker.
    :return: Up to ``TOP_N`` rows per index, largest market cap first, missing caps last.
    """
    result = []
    for index in dict.fromkeys(row[0] for row in rows):
        members = [row for row in rows if row[0] == index]
        members.sort(key=lambda row: infos[row[2]].get("marketCap") or 0, reverse=True)
        result.extend(members[:TOP_N])
    return result


def main() -> None:
    """Build, confirm and rank all members, then write the CSV."""
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args()
    rows = build()
    skipped = sorted({ticker for _, _, ticker in rows if not processable(ticker)})
    if skipped:
        # Symbol format or exchange not connected: these would stay "NA" in the app.
        print(f"Left out (not priceable in the app): {', '.join(skipped)}")
    rows = [row for row in rows if processable(row[2])]
    infos = yahoo_infos({row[2] for row in rows})
    rows = [
        (index, infos[ticker].get("longName") or infos[ticker].get("shortName") or ticker, ticker)
        if index == "Nikkei 225" else (index, name, ticker)
        for index, name, ticker in largest(rows, infos)
    ]
    with OUTPUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(("index", "unternehmen", "yahoo_ticker"))
        writer.writerows(rows)
    counts = pd.Series([row[0] for row in rows]).value_counts(sort=False).to_dict()
    print(f"{OUTPUT.name}: {counts}")
    missing = [ticker for _, _, ticker in rows if not infos[ticker].get("marketCap")]
    if missing:
        print(f"Without market cap (no tile on the start page): {', '.join(missing)}")


if __name__ == "__main__":
    main()
