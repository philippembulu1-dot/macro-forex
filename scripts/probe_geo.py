# Sonde ponctuelle : quelles sources géopolitiques gratuites répondent depuis GitHub ?
import json, time, urllib.request, io, zipfile, os
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
R = {}
def get(u, n=600000, hd=None):
    h = {"User-Agent": UA}; h.update(hd or {})
    t = time.time()
    with urllib.request.urlopen(urllib.request.Request(u, headers=h), timeout=40) as r:
        b = r.read(n if n else -1); return r.status, b, round(time.time() - t, 1), r.headers.get("Content-Type")
T = {
 "gdelt_last": "http://data.gdeltproject.org/gdeltv2/lastupdate.txt",
 "gdelt_doc": "https://api.gdeltproject.org/api/v2/doc/doc?query=%28war%20OR%20missile%20OR%20airstrike%29&mode=TimelineVolRaw&format=json&timespan=7d",
 "gdelt_doc_art": "https://api.gdeltproject.org/api/v2/doc/doc?query=%28airstrike%20OR%20missile%29%20sourcelang:english&mode=ArtList&format=json&maxrecords=5&timespan=1d",
 "gdelt_geo": "https://api.gdeltproject.org/api/v2/geo/geo?query=airstrike&format=GeoJSON&timespan=1d",
 "gpr_daily": "https://www.matteoiacoviello.com/gpr_files/data_gpr_daily_recent.xls",
 "gpr_export": "https://www.matteoiacoviello.com/gpr_files/data_gpr_export.xls",
 "gpr_daily_csv": "https://www.matteoiacoviello.com/gpr_files/data_gpr_daily_recent.csv",
 "travel_us": "https://cadataapi.state.gov/api/TravelAdvisories",
 "usgs": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_week.geojson",
 "reliefweb": "https://api.reliefweb.int/v1/disasters?appname=macro-forex&limit=3&sort[]=date:desc",
 "ofac_rss": "https://ofac.treasury.gov/recent-actions/rss.xml",
 "gnews_geo": "https://news.google.com/rss/search?q=gold+geopolitical+when:1d&hl=en-US&gl=US&ceid=US:en",
 "acled_x": "https://acleddata.com/",
 "crisisgroup": "https://www.crisisgroup.org/crisiswatch",
}
for k, u in T.items():
    try:
        st, b, dt, ct = get(u)
        R[k] = {"ok": True, "status": st, "s": dt, "ct": ct, "len": len(b), "head": b[:300].decode("utf-8", "replace")}
        if k == "gdelt_last":
            line = b.decode().split("\n")[0].split()
            url = line[2]
            st2, z, dt2, _ = get(url, 0)
            zf = zipfile.ZipFile(io.BytesIO(z)); name = zf.namelist()[0]; rows = zf.read(name).decode("utf-8", "replace").split("\n")
            R["gdelt_export"] = {"ok": True, "url": url, "zip_kb": len(z) // 1024, "rows": len(rows), "cols": len(rows[0].split("\t")), "row0": rows[0][:400], "s": dt2}
        if k == "gpr_daily" or k == "gpr_export":
            R[k]["magic"] = b[:8].hex()
    except Exception as e:
        R[k] = {"ok": False, "err": repr(e)[:200]}
    time.sleep(1)
try:
    import subprocess; subprocess.run(["pip", "install", "-q", "xlrd==2.0.1"], check=True, timeout=120)
    import xlrd
    st, b, dt, ct = get(T["gpr_export"], 0)
    wb = xlrd.open_workbook(file_contents=b); sh = wb.sheet_by_index(0)
    R["gpr_export_parse"] = {"sheets": wb.sheet_names(), "rows": sh.nrows, "hdr": sh.row_values(0)[:60], "last": sh.row_values(sh.nrows - 1)[:12]}
    st, b, dt, ct = get(T["gpr_daily"], 0)
    wb = xlrd.open_workbook(file_contents=b); sh = wb.sheet_by_index(0)
    R["gpr_daily_parse"] = {"rows": sh.nrows, "hdr": sh.row_values(0)[:20], "last": sh.row_values(sh.nrows - 1)[:12]}
except Exception as e:
    R["xlrd"] = repr(e)[:300]
os.makedirs("data", exist_ok=True)
json.dump(R, open("data/probe-geo.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps({k: v.get("ok") if isinstance(v, dict) else v for k, v in R.items()}))
