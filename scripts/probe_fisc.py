import json, urllib.request, time, os
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
E = "https://data-api.ecb.europa.eu/service/data/"
P = {
 "cnbc_hist": "https://ts-api.cnbc.com/harmony/app/charts/1Y.json?symbol=FR10Y-FR",
 "cnbc_hist5": "https://ts-api.cnbc.com/harmony/app/charts/5Y.json?symbol=IT10Y-IT",
 "cnbc_2y": "https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol?symbols=DE2Y-DE%7CFR2Y-FR%7CIT2Y-IT%7CNL10Y-NL%7CBE10Y-BE%7CAT10Y-AT&requestMethod=itv&noform=1&partnerId=2&fund=1&exthrs=1&output=json",
 "us_mts_ytd": "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/mts/mts_table_1?filter=classification_desc:eq:Year-to-Date&sort=-record_date&page[size]=6",
 "us_mts_m": "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/mts/mts_table_1?filter=record_date:eq:2026-09-30&page[size]=40&fields=classification_desc,current_month_dfct_sur_amt,current_month_gross_rcpt_amt,current_month_gross_outly_amt,record_fiscal_year",
 "ons_j5ij_tail": "https://www.ons.gov.uk/generator?format=csv&uri=/economy/governmentpublicsectorandtaxes/publicsectorfinance/timeseries/j5ij/pusf",
 "eurostat_q_sca": "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/gov_10q_ggnfa?format=JSON&na_item=B9&sector=S13&unit=PC_GDP&s_adj=SCA&lastTimePeriod=2&geo=EA20&geo=FR&geo=IT&geo=DE",
 "eurostat_debt": "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/gov_10dd_edpt1?format=JSON&na_item=GD&sector=S13&unit=PC_GDP&lastTimePeriod=1&geo=EA20&geo=FR",
 "oecd_all": "https://sdmx.oecd.org/public/rest/data/OECD.ECO.MAD,DSD_EO@DF_EO,/USA+JPN+CAN+AUS+NZL+CHE+GBR+EA17.NLGQ+GGFLQ.A?startPeriod=2024&dimensionAtObservation=AllDimensions&format=csvfilewithlabels",
 "oecd_df": "https://sdmx.oecd.org/public/rest/dataflow/OECD.ECO.MAD/DSD_EO@DF_EO/latest",
}
out = {}
for k, u in P.items():
    t = time.time()
    try:
        r = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA, "Accept": "*/*"}), timeout=45)
        b = r.read().decode("utf-8", "replace")
        out[k] = {"ok": r.status, "len": len(b), "head": b[:3000], "tail": b[-1500:], "s": round(time.time()-t, 1)}
        if k in ("ecb_ciss_all", "ecb_tgb", "ecb_fm_list"):
            import csv, io
            out[k]["keys"] = sorted({row.get("KEY", "") + " " + row.get("TIME_PERIOD", "") + " " + row.get("OBS_VALUE", "") + " | " + (row.get("TITLE") or "")[:80] for row in csv.DictReader(io.StringIO(b))})[:400]
    except Exception as e:
        out[k] = {"err": repr(e)[:300], "s": round(time.time()-t, 1)}
os.makedirs("data", exist_ok=True)
json.dump(out, open("data/probe-fisc.json", "w"), ensure_ascii=False, indent=1)
