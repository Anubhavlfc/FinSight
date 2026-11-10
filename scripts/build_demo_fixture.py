"""Generate the SYNTHETIC offline demo fixture in fixtures/demo/.

Everything produced here is made up. "DEMO ANALYTICS CORP" is not a real company.
The files mimic the shape of SEC EDGAR's company_tickers.json, submissions and
XBRL companyfacts APIs so the full pipeline runs offline.

Built-in traps the pipeline must handle (asserted in tests/unit/test_pipeline_demo.py):

* FY2024 revenue and gross profit restated: originally 1,010M / 610M in the FY2024
  10-K, 1,000M / 600M in later 10-Ks (latest-filing policy must pick 1,000M / 600M).
* FY2025 capex amended by a 10-K/A: 85M in the original 10-K, 90M in the 10-K/A.
* A Q4 three-month revenue fact labelled fy=2026, fp=FY inside the FY2026 10-K.
* A 10-Q fact, duplicate rows (same filing, with and without a frame) and
  a competing revenue tag (us-gaap:Revenues) for FY2024 only.
* Net loss in FY2024 -> profit in FY2025; free cash flow turns negative in FY2026.

Run:  python scripts/build_demo_fixture.py
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "demo"
CIK = "0009999901"
M = 1_000_000
LABEL = "SYNTHETIC FIXTURE - NOT REAL DATA"

FIELDS = {  # standardized name -> (us-gaap tag, period type)
    "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax", "duration"),
    "cost_of_revenue": ("CostOfGoodsAndServicesSold", "duration"),
    "gross_profit": ("GrossProfit", "duration"),
    "operating_income": ("OperatingIncomeLoss", "duration"),
    "net_income": ("NetIncomeLoss", "duration"),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities", "duration"),
    "capital_expenditure": ("PaymentsToAcquirePropertyPlantAndEquipment", "duration"),
    "cash": ("CashAndCashEquivalentsAtCarryingValue", "instant"),
    "current_assets": ("AssetsCurrent", "instant"),
    "current_liabilities": ("LiabilitiesCurrent", "instant"),
    "assets": ("Assets", "instant"),
    "liabilities": ("Liabilities", "instant"),
    "equity": ("StockholdersEquity", "instant"),
    "liabilities_and_equity": ("LiabilitiesAndStockholdersEquity", "instant"),
    "ltd_noncurrent": ("LongTermDebtNoncurrent", "instant"),
    "ltd_current": ("LongTermDebtCurrent", "instant"),
    "commercial_paper": ("CommercialPaper", "instant"),
}

# Latest presentation of each fiscal year, in USD millions.
TRUTH = {
    2022: dict(revenue=820, cost_of_revenue=340, gross_profit=480, operating_income=120, net_income=90,
               operating_cash_flow=150, capital_expenditure=50, cash=200, current_assets=600,
               current_liabilities=300, assets=1700, liabilities=1000, equity=700, liabilities_and_equity=1700,
               ltd_noncurrent=520, ltd_current=40),
    2023: dict(revenue=900, cost_of_revenue=370, gross_profit=530, operating_income=140, net_income=100,
               operating_cash_flow=170, capital_expenditure=55, cash=230, current_assets=650,
               current_liabilities=320, assets=1850, liabilities=1100, equity=750, liabilities_and_equity=1850,
               ltd_noncurrent=510, ltd_current=45),
    2024: dict(revenue=1000, cost_of_revenue=400, gross_profit=600, operating_income=150, net_income=-20,
               operating_cash_flow=180, capital_expenditure=60, cash=250, current_assets=700,
               current_liabilities=350, assets=2000, liabilities=1200, equity=800, liabilities_and_equity=2000,
               ltd_noncurrent=500, ltd_current=50),
    2025: dict(revenue=1180, cost_of_revenue=460, gross_profit=720, operating_income=230, net_income=150,
               operating_cash_flow=260, capital_expenditure=90, cash=310, current_assets=800,
               current_liabilities=380, assets=2300, liabilities=1350, equity=950, liabilities_and_equity=2300,
               ltd_noncurrent=480, ltd_current=50, commercial_paper=100),
    2026: dict(revenue=1239, cost_of_revenue=520, gross_profit=719, operating_income=205, net_income=160,
               operating_cash_flow=240, capital_expenditure=300, cash=200, current_assets=760,
               current_liabilities=420, assets=2600, liabilities=1550, equity=1050, liabilities_and_equity=2600,
               ltd_noncurrent=900, ltd_current=60, commercial_paper=0),
}

FILINGS = [  # accession, form, fiscal year covered, filing date, per-filing overrides {(fy, field): value}
    ("0009999901-22-000010", "10-K", 2022, "2022-08-01", {}),
    ("0009999901-23-000010", "10-K", 2023, "2023-07-31", {}),
    ("0009999901-24-000010", "10-K", 2024, "2024-07-30", {(2024, "revenue"): 1010, (2024, "gross_profit"): 610}),
    ("0009999901-25-000010", "10-K", 2025, "2025-07-30", {(2025, "capital_expenditure"): 85}),
    ("0009999901-25-000020", "10-K/A", 2025, "2025-10-15", {}),
    ("0009999901-26-000010", "10-K", 2026, "2026-07-29", {}),
]
QUARTERLIES = [  # 10-Q filings (only appear in the filing list and one revenue fact)
    ("0009999901-25-000030", "10-Q", "2025-10-28", "2025-09-30"),
    ("0009999901-26-000002", "10-Q", "2026-01-27", "2025-12-31"),
    ("0009999901-26-000005", "10-Q", "2026-04-28", "2026-03-31"),
    ("0009999901-26-000030", "10-Q", "2026-10-27", "2026-09-30"),
]


def fy_end(fy: int) -> date:
    return date(fy, 6, 30)


def fy_start(fy: int) -> date:
    return date(fy - 1, 7, 1)


def build_companyfacts() -> dict:
    facts: dict[str, dict] = {}

    def add(tag: str, row: dict) -> None:
        concept = facts.setdefault(tag, {"label": tag, "description": LABEL, "units": {"USD": []}})
        concept["units"]["USD"].append(row)

    for accn, form, fy, filed, overrides in FILINGS:
        for field, (tag, kind) in FIELDS.items():
            years = [fy, fy - 1, fy - 2] if kind == "duration" else [fy, fy - 1]
            for y in years:
                if y not in TRUTH or field not in TRUTH[y]:
                    continue
                # Restated values: earlier filings show the original figure.
                val = TRUTH[y][field]
                if (y, field) in overrides:
                    val = overrides[(y, field)]
                row = {"end": fy_end(y).isoformat(), "val": val * M, "accn": accn, "fy": fy, "fp": "FY",
                       "form": form, "filed": filed}
                if kind == "duration":
                    row = {"start": fy_start(y).isoformat(), **row}
                add(tag, row)
    # Duplicate row in the same filing (with a frame), as companyfacts does.
    add(FIELDS["revenue"][0], {"start": "2025-07-01", "end": "2026-06-30", "val": 1239 * M,
                               "accn": "0009999901-26-000010", "fy": 2026, "fp": "FY", "form": "10-K",
                               "filed": "2026-07-29", "frame": "CY2025"})
    # Q4 three-month revenue inside the 10-K, labelled fy=2026 fp=FY: must not be taken as annual.
    add(FIELDS["revenue"][0], {"start": "2026-04-01", "end": "2026-06-30", "val": 320 * M,
                               "accn": "0009999901-26-000010", "fy": 2026, "fp": "FY", "form": "10-K",
                               "filed": "2026-07-29", "frame": "CY2026Q2"})
    # A 10-Q quarter (irrelevant to annual periods).
    add(FIELDS["revenue"][0], {"start": "2025-07-01", "end": "2025-09-30", "val": 300 * M,
                               "accn": "0009999901-25-000030", "fy": 2026, "fp": "Q1", "form": "10-Q",
                               "filed": "2025-10-28"})
    # Competing tag reported only in the FY2024 10-K (with the original, pre-restatement figure).
    add("Revenues", {"start": "2023-07-01", "end": "2024-06-30", "val": 1010 * M,
                     "accn": "0009999901-24-000010", "fy": 2024, "fp": "FY", "form": "10-K",
                     "filed": "2024-07-30"})
    return {"cik": int(CIK), "entityName": "DEMO ANALYTICS CORP (SYNTHETIC FIXTURE)",
            "_fixture_notice": LABEL, "facts": {"us-gaap": facts}}


def build_submissions() -> dict:
    rows = [(a, f, filed, fy_end(fy).isoformat()) for a, f, fy, filed, _ in FILINGS]
    rows += [(a, f, filed, rep) for a, f, filed, rep in QUARTERLIES]
    rows.sort(key=lambda r: r[2], reverse=True)
    return {
        "_fixture_notice": LABEL,
        "cik": CIK.lstrip("0"), "entityType": "operating", "sic": "7372",
        "sicDescription": "Services-Prepackaged Software", "name": "DEMO ANALYTICS CORP (SYNTHETIC FIXTURE)",
        "tickers": ["DEMO"], "exchanges": ["SYNTH"], "fiscalYearEnd": "0630", "stateOfIncorporation": "DE",
        "filings": {
            "recent": {
                "accessionNumber": [r[0] for r in rows],
                "filingDate": [r[2] for r in rows],
                "reportDate": [r[3] for r in rows],
                "form": [r[1] for r in rows],
                "primaryDocument": [f"demo-{r[3].replace('-', '')}.htm" for r in rows],
            },
            "files": [],
        },
    }


def build_bank_submissions() -> dict:
    return {
        "_fixture_notice": LABEL, "cik": "9999902", "entityType": "operating", "sic": "6022",
        "sicDescription": "State Commercial Banks", "name": "SAMPLE BANK CORP (SYNTHETIC FIXTURE)",
        "tickers": ["SBNK"], "exchanges": ["SYNTH"], "fiscalYearEnd": "1231",
        "filings": {"recent": {"accessionNumber": ["0009999902-26-000001"], "filingDate": ["2026-02-20"],
                               "reportDate": ["2025-12-31"], "form": ["10-K"],
                               "primaryDocument": ["sbnk-20251231.htm"]}, "files": []},
    }


def main() -> None:
    (ROOT / "submissions").mkdir(parents=True, exist_ok=True)
    (ROOT / "companyfacts").mkdir(parents=True, exist_ok=True)
    tickers = {
        "0": {"cik_str": 9999901, "ticker": "DEMO", "title": "DEMO ANALYTICS CORP (SYNTHETIC FIXTURE)"},
        "1": {"cik_str": 9999902, "ticker": "SBNK", "title": "SAMPLE BANK CORP (SYNTHETIC FIXTURE)"},
        "2": {"cik_str": 9999903, "ticker": "DROB", "title": "DEMO ROBOTICS INC (SYNTHETIC FIXTURE)"},
    }
    dump = lambda p, o: p.write_text(json.dumps(o, indent=1) + "\n")  # noqa: E731
    dump(ROOT / "company_tickers.json", tickers)
    dump(ROOT / "submissions" / f"CIK{CIK}.json", build_submissions())
    dump(ROOT / "submissions" / "CIK0009999902.json", build_bank_submissions())
    dump(ROOT / "companyfacts" / f"CIK{CIK}.json", build_companyfacts())
    print(f"wrote fixtures to {ROOT}")


if __name__ == "__main__":
    main()
