"""Build the data file behind the Kenya Economic Dashboard.

Downloads World Bank WDI indicators for Kenya, the East African Community
and all African economies, then writes docs/data.json, which the dashboard
page (docs/index.html) reads. Key insights are generated from the data so
they stay correct when the numbers update.

Run from the repo root:  python src/build_data.py
"""
import json
from datetime import date
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "data.json"
RAW = ROOT / "data" / "wdi_raw.csv"
API = "https://api.worldbank.org/v2"
YEARS = "2000:2025"

INDICATORS = {
    "NY.GDP.MKTP.CD": "gdp_usd",
    "NY.GDP.MKTP.KD.ZG": "gdp_growth",
    "NY.GDP.PCAP.CD": "gdp_pc_usd",
    "FP.CPI.TOTL.ZG": "inflation",
    "NV.AGR.TOTL.ZS": "agriculture_share",
    "NV.IND.TOTL.ZS": "industry_share",
    "NV.SRV.TOTL.ZS": "services_share",
    "NE.EXP.GNFS.ZS": "exports_gdp",
    "NE.IMP.GNFS.ZS": "imports_gdp",
    "BX.TRF.PWKR.DT.GD.ZS": "remittances_gdp",
    "SP.POP.TOTL": "population",
}
EAC = ["KEN", "TZA", "UGA", "RWA", "BDI", "SSD", "COD", "SOM"]
NORTH_AFRICA = ["DZA", "EGY", "LBY", "MAR", "TUN"]


def african_countries() -> list[str]:
    rows = requests.get(f"{API}/country", params={"region": "SSF", "format": "json", "per_page": 100},
                        timeout=30).json()[1]
    return sorted({r["id"] for r in rows} | set(NORTH_AFRICA))


def fetch() -> pd.DataFrame:
    codes = ";".join(african_countries())
    frames = []
    for code, name in INDICATORS.items():
        rows = requests.get(f"{API}/country/{codes}/indicator/{code}",
                            params={"format": "json", "date": YEARS, "per_page": 5000}, timeout=60).json()[1]
        frames.append(pd.DataFrame(
            {"iso3": r["countryiso3code"], "country": r["country"]["value"], "year": int(r["date"]),
             "indicator": name, "value": r["value"]}
            for r in rows
        ))
    return pd.concat(frames).dropna(subset=["value"])


def load() -> pd.DataFrame:
    if not RAW.exists():
        RAW.parent.mkdir(exist_ok=True)
        fetch().to_csv(RAW, index=False)
    return pd.read_csv(RAW)


def latest(df: pd.DataFrame, iso3: str, indicator: str) -> tuple[int, float, float | None]:
    """Latest year, its value, and the previous year's value."""
    s = df[(df.iso3 == iso3) & (df.indicator == indicator)].set_index("year")["value"].sort_index()
    prev = s.iloc[-2] if len(s) > 1 else None
    return int(s.index[-1]), float(s.iloc[-1]), (float(prev) if prev is not None else None)


def latest_cross_section(df: pd.DataFrame, indicator: str, countries=None) -> pd.DataFrame:
    """Each country's most recent value (within the last 3 years of data)."""
    d = df[df.indicator == indicator]
    if countries is not None:
        d = d[d.iso3.isin(countries)]
    d = d[d.year >= d.year.max() - 2]
    return d.sort_values("year").groupby("iso3").tail(1)


def kpi(df, indicator, label, unit, scale=1.0, decimals=1, change="pct"):
    year, value, prev = latest(df, "KEN", indicator)
    if prev is None:
        delta = None
    elif change == "pp":  # rates compare in percentage points
        delta = value - prev
    else:
        delta = (value / prev - 1) * 100
    return {"label": label, "value": round(value * scale, decimals), "unit": unit, "year": year,
            "delta": None if delta is None else round(delta, 1), "delta_unit": "pp" if change == "pp" else "%"}


def main():
    df = load()
    ken = df[df.iso3 == "KEN"]

    def series(ind):
        s = ken[ken.indicator == ind].sort_values("year")
        return {"years": s.year.tolist(), "values": s.value.round(2).tolist()}

    kpis = [
        kpi(df, "gdp_usd", "GDP (current US$)", "bn", scale=1e-9, decimals=1),
        kpi(df, "gdp_growth", "Real GDP growth", "%", change="pp"),
        kpi(df, "inflation", "Inflation (CPI)", "%", change="pp"),
        kpi(df, "gdp_pc_usd", "GDP per capita", "US$", decimals=0),
    ]

    # Sector composition of GDP; the remainder is net product taxes.
    shares = {}
    for ind, name in [("services_share", "Services"), ("agriculture_share", "Agriculture"),
                      ("industry_share", "Industry")]:
        year, value, _ = latest(df, "KEN", ind)
        shares[name] = round(value, 1)
    shares["Net taxes on products"] = round(100 - sum(shares.values()), 1)

    names = df.drop_duplicates("iso3").set_index("iso3")["country"].to_dict()
    names.update({"SOM": "Somalia", "COD": "DR Congo", "TZA": "Tanzania", "SSD": "South Sudan", "EGY": "Egypt"})

    eac_growth = latest_cross_section(df, "gdp_growth", EAC).sort_values("value", ascending=False)
    eac_gdp = latest_cross_section(df, "gdp_usd", EAC).sort_values("value", ascending=False)
    gdp_pc = latest_cross_section(df, "gdp_pc_usd", EAC).set_index("iso3")["value"]
    infl = latest_cross_section(df, "inflation", EAC).set_index("iso3")["value"]
    growth = eac_growth.set_index("iso3")["value"]
    table = (pd.DataFrame({"gdp_pc": gdp_pc, "growth": growth, "inflation": infl})
             .sort_values("gdp_pc", ascending=False).head(5))
    africa = latest_cross_section(df, "gdp_growth")

    # Insights, computed from the data.
    g_year, g, g_prev = latest(df, "KEN", "gdp_growth")
    i_year, i, i_prev = latest(df, "KEN", "inflation")
    rank = list(eac_gdp.iso3).index("KEN") + 1
    eac_share = eac_gdp.set_index("iso3").loc["KEN", "value"] / eac_gdp.value.sum() * 100
    top_service = max(shares, key=shares.get)
    growth_2000s = ken[(ken.indicator == "gdp_growth") & ken.year.between(2010, 2019)].value.mean()
    africa_median = africa.value.median()
    ken_pos = (africa.value < africa.set_index("iso3").loc["KEN", "value"]).mean() * 100
    _, rem, _ = latest(df, "KEN", "remittances_gdp")
    _, exp, _ = latest(df, "KEN", "exports_gdp")
    _, imp, _ = latest(df, "KEN", "imports_gdp")

    insights = [
        f"Real GDP grew {g:.1f}% in {g_year}, {'broadly unchanged from' if abs(g - g_prev) < 0.05 else 'up from' if g > g_prev else 'down from'} {g_prev:.1f}% a year earlier "
        f"(2010–19 average: {growth_2000s:.1f}%).",
        f"Inflation was {i:.1f}% in {i_year}, {'inside' if 2.5 <= i <= 7.5 else 'outside'} the Central Bank of Kenya's "
        f"5% ± 2.5 target band.",
        f"{top_service} is the largest sector at {shares[top_service]:.0f}% of GDP; agriculture still contributes "
        f"{shares['Agriculture']:.0f}%.",
        f"Kenya is the #{rank} economy in the EAC, with {eac_share:.0f}% of the bloc's output.",
        f"Growth was faster than {ken_pos:.0f}% of African economies (Africa median: {africa_median:.1f}%).",
    ]
    considerations = [
        f"Imports ({imp:.0f}% of GDP) exceed exports ({exp:.0f}%): export diversification and value addition "
        f"would ease external-balance pressure.",
        f"Remittances ({rem:.1f}% of GDP) are a key foreign-exchange source; lowering transfer costs supports "
        f"household incomes.",
        "Raising agricultural productivity matters for both growth and food-price inflation.",
        "Regional infrastructure links (roads, rail, power) deepen EAC trade where Kenya is the largest economy.",
    ]

    data = {
        "updated": date.today().isoformat(),
        "kpis": kpis,
        "trend": {"growth": series("gdp_growth"), "inflation": series("inflation")},
        "sectors": shares,
        "eac_growth": {"countries": [names[c] for c in eac_growth.iso3], "values": eac_growth.value.round(1).tolist(),
                       "years": eac_growth.year.tolist()},
        "eac_gdp": {"countries": [names[c] for c in eac_gdp.iso3], "values": (eac_gdp.value / 1e9).round(1).tolist()},
        "eac_table": [{"country": names[c], "gdp_pc": round(r.gdp_pc), "growth": round(r.growth, 1),
                       "inflation": None if pd.isna(r.inflation) else round(r.inflation, 1)}
                      for c, r in table.iterrows()],
        "africa_map": {"iso3": africa.iso3.tolist(), "country": [names.get(c, c) for c in africa.iso3],
                       "values": africa.value.round(1).tolist()},
        "insights": insights,
        "considerations": considerations,
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(data, indent=1))
    print(json.dumps({k: data[k] for k in ["kpis", "sectors", "insights"]}, indent=1))


if __name__ == "__main__":
    main()
