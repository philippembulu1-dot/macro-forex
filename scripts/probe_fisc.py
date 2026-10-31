import json, urllib.request, time, os
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
E = "https://data-api.ecb.europa.eu/service/data/"
P = {
 "ecb_fm_10y": E + "FM/D.DE+FR+IT+ES.EUR.4F.BB.DE10YT_RR+FR10YT_RR+IT10YT_RR+ES10YT_RR.YLD?lastNObservations=3&format=csvdata",
 "ecb_fm_list": E + "FM/D..EUR.4F.BB..YLD?lastNObservations=1&format=csvdata",
 "ecb_irs": E + "IRS/M.DE+FR+IT+ES+NL+BE+PT+GR+AT+IE+FI.L.L40.CI.0000.EUR.N.Z?lastNObservations=3&format=csvdata",
 "ecb_ciss_all": E + "CISS?lastNObservations=1&format=csvdata",
 "ecb_tgb": E + "TGB?lastNObservations=1&format=csvdata",
 "ecb_gfs_a": E + "GFS/A.N..W0.S13.S1.N.B.B9._Z._Z._Z.XDC_R_B1GQ._Z.S.V.N._T?lastNObservations=2&format=csvdata",
 "ecb_gfs_q": E + "GFS/Q.N..W0.S13.S1.N.B.B9._Z._Z._Z.XDC_R_B1GQ._Z.S.V.N._T?lastNObservations=2&format=csvdata",
 "ecb_gfs_debt": E + "GFS/A.N..W0.S13.S1.C.L.LE.GD.T._Z.XDC_R_B1GQ._Z.F.V.N._T?lastNObservations=2&format=csvdata",
 "bbk_bund": "https://api.statistiken.bundesbank.de/rest/data/BBSIS/D.I.ZST.ZI.EUR.S1311.B.A604.R10XX.R.A.A._Z._Z.A?lastNObservations=3&format=csv",
 "eurostat_edp": "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/gov_10dd_edpt1?format=JSON&na_item=B9&sector=S13&unit=PC_GDP&lastTimePeriod=2",
 "eurostat_q": "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/gov_10q_ggnfa?format=JSON&na_item=B9&sector=S13&unit=PC_GDP&s_adj=NSA&lastTimePeriod=2",
 "us_mts": "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/mts/mts_table_1?sort=-record_date&page[size]=30",
 "oecd_eo": "https://sdmx.oecd.org/public/rest/data/OECD.ECO.MAD,DSD_EO@DF_EO,/USA+JPN+CAN+AUS+NZL+CHE+GBR+EA17+FRA+DEU+ITA.NLGQ.A?startPeriod=2023&dimensionAtObservation=AllDimensions&format=csvfile",
 "oecd_eo2": "https://sdmx.oecd.org/public/rest/data/OECD.ECO.MAD,DSD_EO@DF_EO,/USA.NLGQ+GNFLQ+NLG.A?startPeriod=2024&format=csvfile",
 "cnbc": "https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol?symbols=DE10Y-DE%7CFR10Y-FR%7CIT10Y-IT%7CES10Y-ES%7CGR10Y-GR%7CPT10Y-PT&requestMethod=itv&noform=1&partnerId=2&fund=1&exthrs=1&output=json",
 "ons_psnb": "https://www.ons.gov.uk/generator?format=csv&uri=/economy/governmentpublicsectorandtaxes/publicsectorfinance/timeseries/j5ij/pusf",
 "ons_psnb2": "https://www.ons.gov.uk/generator?format=csv&uri=/economy/governmentpublicsectorandtaxes/publicsectorfinance/timeseries/dzls/pusf",
 "wb_bal": "https://api.worldbank.org/v2/country/USA;JPN;CAN;AUS;NZL;CHE;GBR/indicator/GC.NLD.TOTL.GD.ZS?format=json&mrnev=1",
 "imf_dm": "https://www.imf.org/external/datamapper/api/v1/GGXCNL_NGDP/USA/JPN?periods=2025,2026",
}
out = {}
for k, u in P.items():
    t = time.time()
    try:
        r = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA, "Accept": "*/*"}), timeout=45)
        b = r.read().decode("utf-8", "replace")
        out[k] = {"ok": r.status, "len": len(b), "head": b[:3000], "s": round(time.time()-t, 1)}
        if k in ("ecb_ciss_all", "ecb_tgb", "ecb_fm_list"):
            import csv, io
            out[k]["keys"] = sorted({row.get("KEY", "") + " " + row.get("TIME_PERIOD", "") + " " + row.get("OBS_VALUE", "") + " | " + (row.get("TITLE") or "")[:80] for row in csv.DictReader(io.StringIO(b))})[:400]
    except Exception as e:
        out[k] = {"err": repr(e)[:300], "s": round(time.time()-t, 1)}
os.makedirs("data", exist_ok=True)
json.dump(out, open("data/probe-fisc.json", "w"), ensure_ascii=False, indent=1)
