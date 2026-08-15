#!/usr/bin/env python3
"""
TripIt -> Google Maps overlay sync script (v2 - title-first geocoding).
Fetches a TripIt public share link, parses itinerary items, geocodes new
locations (cached), rebuilds KML + Leaflet HTML map + driving sheet, and
publishes to a GitHub Pages repo.
"""
import argparse, json, os, re, subprocess, sys, time, unicodedata, urllib.request

ICELAND_BBOX = {"min_lat": 63.2, "max_lat": 67.5, "min_lon": -25.0, "max_lon": -12.5}

STOPWORDS = {"arrive", "depart", "drop", "off", "pick", "up", "check", "in", "out",
             "and", "the", "of", "at", "an", "a", "hotel", "restaurant", "manor",
             "cafe", "lodge", "location", "name", "address", "experience", "enjoy",
             "thermal", "waters", "viewpoint", "walk", "trail", "loop", "roadside"}

def norm(s):
    """NFKD normalize + strip combining marks (ö->o, á->a) for matching."""
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).lower()

def in_iceland(hit):
    if not hit:
        return False
    return (ICELAND_BBOX["min_lat"] <= hit["lat"] <= ICELAND_BBOX["max_lat"] and
            ICELAND_BBOX["min_lon"] <= hit["lon"] <= ICELAND_BBOX["max_lon"])

SHARE_URL = "https://www.tripit.com/p/960A1FBC22D205E8C2D68AAA76746AD5"
REPO = "pkooney/iceland-trip-map"
WORKDIR = os.path.expanduser("~/tripit_map")
CACHE_FILE = os.path.join(WORKDIR, "geocode_cache.json")
DAY_COLORS = ["#ff3b30", "#ff9500", "#34c759", "#007aff", "#af52de"]
KML_COLORS = {"#ff3b30": "ff0000ff", "#ff9500": "ff0088ff", "#34c759": "ff59c734",
              "#007aff": "ffff7a00", "#af52de": "ffde52af"}
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# Title fragments (lowercase, matched with 'in') -> good Nominatim query.
ALIASES = [
    ("perlan", "Perlan Reykjavik"),
    ("flyovericeland", "Perlan Reykjavik"),
    ("baejarins beztu", "Baejarins Beztu Pylsur, Tryggvagata 1, Reykjavik, Iceland"),
    ("hot dog", "Baejarins Beztu Pylsur, Tryggvagata 1, Reykjavik, Iceland"),
    ("edition reykjavik", "Austurbakki 2, Reykjavik, Iceland"),
    ("caves of laugarvatn", "Laugarvatn, Iceland"),
    ("fontana", "Fontana Laugarvatn, Iceland"),
    ("fridheima", "Fridheimar, Reykholt 806, Iceland"),
    ("vinstofa", "Fridheimar, Reykholt 806, Iceland"),
    ("geysir", "Strokkur, Iceland"),
    ("strokkur", "Strokkur, Iceland"),
    ("haukadalur", "Strokkur, Iceland"),
    ("gullfoss", "Gullfoss, Iceland"),
    ("ranga restaurant", "Hotel Ranga, Hella, Iceland"),
    ("hotel ranga", "Hotel Ranga, Hella, Iceland"),
    ("skogafoss", "Skogafoss waterfall, Iceland"),
    ("solheimajokull", "Solheimajokull glacier, Iceland"),
    ("reynisfjara", "Reynisfjara, Iceland"),
    ("skalakot", "Skalakot, Iceland"),
    ("keri", "Kerid crater, Iceland"),
    ("seljalandsfoss", "Seljalandsfoss waterfall, Iceland"),
    ("eyjafjallajokull", "Thorvaldseyri, Iceland"),
    ("bridge between continents", "Bridge Between Continents, Reykjanes, Iceland"),
    ("reykjanesviti", "Reykjanesviti lighthouse, Reykjanes, Iceland"),
    ("valahnukamol", "Reykjanesviti lighthouse, Reykjanes, Iceland"),
    ("gunnuhver", "Gunnuhver, Iceland"),
    ("brimketill", "Brimketill, Iceland"),
    ("retreat at blue lagoon", "Retreat at Blue Lagoon, Nordurljosavegur 11, Grindavik, Iceland"),
    ("blue lagoon", "Blue Lagoon, Nordurljosavegur 9, Grindavik, Iceland"),
    ("lava restaurant", "Lava Restaurant, Blue Lagoon, Grindavik, Iceland"),
    ("moss restaurant", "Moss Restaurant, Blue Lagoon, Grindavik, Iceland"),
    ("hertz car rental", "Keflavik International Airport, Iceland"),
    ("thingvell", "Thingvellir, Iceland"),
    ("dill restaurant", "Dill Restaurant, Laugavegur 59, Reykjavik, Iceland"),
    ("sky lagoon", "Sky Lagoon, Kopavogur, Iceland"),
    ("laugaras", "Laugaras Lagoon, Skalholtsvegur 1, Laugaras, Iceland"),
    ("keflavik international airport", "Keflavik International Airport, Iceland"),
]

STRIP_PREFIXES = ["arrive ", "depart ", "drop off ", "pick up ", "check in ", "check out "]

def fetch_tripit(share_url):
    jina = "https://r.jina.ai/" + share_url
    try:
        req = urllib.request.Request(jina, headers=UA)
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read().decode("utf-8", "replace")
    except Exception:
        out = subprocess.run(["curl", "-sL", "--max-time", "120", jina],
                             capture_output=True, text=True)
        return out.stdout

def parse_tripit(text):
    items = []
    day_map = {}
    for m in re.finditer(r"^##\s+(\w+,\s+Sep\s+\d+)", text, re.M):
        day_map[m.start()] = m.group(1)
    date_re = re.compile(r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*$", re.M)
    matches = list(date_re.finditer(text))
    for i, m in enumerate(matches):
        mm, dd, yyyy = m.group(1), m.group(2), m.group(3)
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[m.end():end]
        title = ""
        for line in block.splitlines():
            line = line.strip()
            if line and not line.startswith("![") and "TripIt" not in line and not re.match(r"^###?\s", line):
                title = re.sub(r"\s+", " ", line).strip().rstrip("▼").strip()
                break
        if not title:
            continue
        tm = None
        tm_m = re.search(r"###?\s*(\d{1,2}:\d{2})\s*(AM|PM)\s*([A-Z]{2,4})", block)
        if not tm_m:
            tm_m = re.search(r"(\d{1,2}:\d{2})\s*(AM|PM)\s*([A-Z]{2,4})", block)
        if tm_m:
            tm = f"{tm_m.group(1)} {tm_m.group(2)} {tm_m.group(3)}"
        addr = None
        am = re.search(r"\[([^\]]+)\]\(https://www\.tripit\.com/trip/publicMap\?", block)
        if am:
            addr = am.group(1).strip()
        day = ""
        for pos, d in sorted(day_map.items()):
            if pos <= m.start():
                day = d
            else:
                break
        if not day:
            day = f"{mm}/{dd}/{yyyy}"
        items.append({"day": day, "date": f"{yyyy}-{mm}-{dd}", "title": title,
                      "time": tm, "address": addr, "raw": block})
    return items

def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_cache(cache):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

def geocode(query, cache):
    key = query.strip().lower()
    if key in cache:
        return cache[key]
    maps_client = r"C:\Users\pkoon\AppData\Local\hermes\skills\productivity\maps\scripts\maps_client.py"
    try:
        r = subprocess.run([sys.executable, maps_client, "search", query],
                           capture_output=True, text=True, timeout=60)
        data = json.loads(r.stdout)
        res = (data.get("results") or [])
        if res:
            hit = {"lat": res[0]["lat"], "lon": res[0]["lon"], "display": res[0].get("display_name", "")}
            cache[key] = hit
            return hit
    except Exception as e:
        print(f"  geocode error {query}: {e}")
    time.sleep(1.1)
    return None

def query_for_title(title):
    low = norm(title)
    for frag, q in ALIASES:
        if norm(frag) in low:
            return q
    return None

def resolve_stop(item, cache):
    """Return list of (name, lat, lon, desc) — may be several pins for combined items."""
    title = item["title"]
    low = norm(title)
    if re.search(r"\b(jfk|kef)\s*(to|->)\s*(kef|jfk)\b", low) or "icelandair" in low:
        return []
    # combined items: split on ' - ' and ', ' where each part is a real POI
    parts = [p.strip() for p in re.split(r"\s*-\s*|\s*,\s*", title) if p.strip()]
    resolved = []
    seen = set()
    for part in parts:
        p_low = norm(part)
        if len(part) < 4 or p_low in STOPWORDS:
            continue
        q = query_for_title(part) or (part + ", Iceland")
        hit = geocode(q, cache)
        if hit and in_iceland(hit):
            key = (round(hit["lat"], 4), round(hit["lon"], 4))
            if key in seen:
                continue
            seen.add(key)
            desc = title if len(parts) > 1 else (item["time"] + " - " + title if item["time"] else title)
            resolved.append({"name": part if len(parts) > 1 else title,
                             "lat": hit["lat"], "lon": hit["lon"],
                             "desc": desc, "day": item["day"], "date": item["date"],
                             "time": item["time"]})
    if not resolved:
        # fallback: whole title, then address
        qs = []
        tq = query_for_title(title)
        if tq:
            qs.append(tq)
        else:
            qs.append(title)
        if item["address"]:
            qs.append(item["address"])
        for q in qs:
            if not q:
                continue
            hit = geocode(q, cache)
            if hit and in_iceland(hit):
                desc = (item["time"] + " - " if item["time"] else "") + title
                resolved.append({"name": title, "lat": hit["lat"], "lon": hit["lon"],
                                 "desc": desc, "day": item["day"], "date": item["date"],
                                 "time": item["time"]})
                break
    return resolved

def kml_color(rgb):
    r, g, b = rgb[1:3], rgb[3:5], rgb[5:7]
    return "ff" + b + g + r

def fetch_route(points):
    if len(points) < 2:
        return None
    coords = ";".join(f"{lon},{lat}" for lat, lon in points)
    url = f"https://router.project-osrm.org/route/v1/driving/{coords}?overview=full&geometries=geojson"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "hermes-trip-builder/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        if data.get("code") == "Ok" and data.get("routes"):
            return data["routes"][0]["geometry"]["coordinates"]
    except Exception as e:
        print(f"  OSRM fail: {e}")
    return None

def osrm_leg(a, b):
    url = f"https://router.project-osrm.org/route/v1/driving/{a['lon']},{a['lat']};{b['lon']},{b['lat']}?overview=false"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "hermes-trip-builder/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        if data.get("code") == "Ok" and data.get("routes"):
            r = data["routes"][0]
            return r["distance"] / 1000.0, r["duration"] / 60.0
    except Exception:
        pass
    return None, None

def group_by_day(stops):
    days, order = {}, []
    for s in stops:
        if s["day"] not in days:
            days[s["day"]] = []
            order.append(s["day"])
        days[s["day"]].append(s)
    return days, order

def xml_escape(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

def build_kml(days, order):
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>',
           "<name>Iceland Trip Sep 2026</name>"]
    for di, day in enumerate(order):
        color = list(KML_COLORS.keys())[di % len(KML_COLORS)]
        stops = days[day]
        out.append(f"<Folder><name>{day}</name>")
        pts = [(s["lat"], s["lon"]) for s in stops]
        route = fetch_route(pts)
        if route:
            out.append(f'<Placemark><name>{day} - driving route</name><styleUrl>#route_{color[1:]}</styleUrl>')
            out.append("<LineString><tessellate>1</tessellate><altitudeMode>clampToGround</altitudeMode><coordinates>")
            for lon, lat in route:
                out.append(f"{lon:.6f},{lat:.6f},0")
            out.append("</coordinates></LineString></Placemark>")
            time.sleep(1.0)
        for s in stops:
            out.append(f"<Placemark><name>{xml_escape(s['name'])}</name>")
            out.append(f"<description><![CDATA[{s['desc']}]]></description>")
            out.append(f"<styleUrl>#pin_{color[1:]}</styleUrl>")
            out.append(f"<Point><coordinates>{s['lon']:.6f},{s['lat']:.6f},0</coordinates></Point>")
            out.append("</Placemark>")
        out.append("</Folder>")
    for color in KML_COLORS:
        c = kml_color(color)
        cid = color[1:]
        out.append(f'<Style id="pin_{cid}"><IconStyle><color>{c}</color><scale>1.1</scale><Icon><href>http://maps.google.com/mapfiles/kml/pushpin/ylw-pushpin.png</href></Icon></IconStyle></Style>')
        out.append(f'<Style id="route_{cid}"><LineStyle><color>{c}</color><width>4</width></LineStyle></Style>')
    out.append("</Document></kml>")
    return "\n".join(out)

def build_html(days, order):
    markers = []
    for di, day in enumerate(order):
        color = DAY_COLORS[di % len(DAY_COLORS)]
        stops = days[day]
        for s in stops:
            nm = s["name"].replace("'", "&#39;")
            desc = s["desc"].replace("'", "&#39;")
            markers.append(f"L.marker([{s['lat']},{s['lon']}], {{icon: coloredIcon('{color}')}}).addTo(map).bindPopup('<b>{nm}</b><br>{desc}');")
        pts = ",".join(f"[{s['lat']},{s['lon']}]" for s in stops)
        markers.append(f"L.polyline([{pts}], {{color:'{color}', weight:4, opacity:0.7, dashArray:'8 8'}}).addTo(map);")
    legend_items = "".join(
        f"<div><i style=\"background:{DAY_COLORS[i % 5]}\"></i>{day}</div>" for i, day in enumerate(order))
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Iceland Trip Sep 2026 - Map</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>html,body{{margin:0;height:100%}}#map{{height:100vh}}
.legend{{background:#fff;padding:10px 12px;border-radius:8px;box-shadow:0 1px 4px rgba(0,0,0,.3);font:13px/1.6 sans-serif}}
.legend b{{display:block;margin-bottom:4px}}.legend i{{display:inline-block;width:12px;height:12px;border-radius:50%;margin-right:6px}}</style>
</head><body><div id="map"></div><script>
function coloredIcon(c){{return L.divIcon({{className:'',html:`<div style="width:14px;height:14px;background:${{c}};border:2px solid #fff;border-radius:50%;box-shadow:0 0 4px rgba(0,0,0,.5)"></div>`,iconSize:[18,18],iconAnchor:[9,9]}});}}
var map=L.map('map');
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',{{maxZoom:19,attribution:'&copy; OpenStreetMap contributors'}}).addTo(map);
{chr(10).join(markers)}
var b=[];map.eachLayer(function(l){{if(l.getLatLng)b.push(l.getLatLng());}});if(b.length)map.fitBounds(b,{{padding:[40,40]}});
L.control({{position:'bottomleft'}}).onAdd=function(){{var d=L.DomUtil.create('div','legend');d.innerHTML='<b>Iceland Trip Sep 2026</b>{legend_items}';return d;}}.addTo(map);
</script></body></html>"""

def build_sheet(days, order):
    lines = []
    total_km = total_min = 0.0
    for day in order:
        stops = days[day]
        lines.append(f"\n## {day}\n\n| Leg | Distance | Drive time |\n|---|---|---|")
        for i in range(len(stops) - 1):
            a, b = stops[i], stops[i + 1]
            km, mn = osrm_leg(a, b)
            if km is not None:
                total_km += km; total_min += mn
                lines.append(f"| {a['name']} → {b['name']} | {km:.1f} km | {mn:.0f} min |")
            else:
                lines.append(f"| {a['name']} → {b['name']} | n/a | n/a |")
            time.sleep(0.9)
    lines.insert(0, f"# Iceland Road Trip - Driving Sheet\n**Total (approx): {total_km:.0f} km / {total_min/60:.1f} hrs**\n")
    return "\n".join(lines)

def publish(repo, workdir, no_push=False):
    if no_push:
        return
    subprocess.run(["git", "init", "-q"], cwd=workdir, check=False)
    subprocess.run(["git", "config", "user.email", "pkooney@users.noreply.github.com"], cwd=workdir, check=False)
    subprocess.run(["git", "config", "user.name", "pkooney"], cwd=workdir, check=False)
    subprocess.run(["git", "remote", "remove", "origin"], cwd=workdir, check=False)
    subprocess.run(["git", "remote", "add", "origin", f"https://github.com/{repo}.git"], cwd=workdir, check=False)
    subprocess.run(["git", "add", "-A"], cwd=workdir, check=False)
    changed = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=workdir, check=False).returncode != 0
    if changed:
        subprocess.run(["git", "commit", "-q", "-m", f"Trip map update {time.strftime('%Y-%m-%d %H:%M')}"],
                       cwd=workdir, check=False)
        p = subprocess.run(["git", "push", "-q", "-f", "origin", "HEAD:main"], cwd=workdir,
                           capture_output=True, text=True)
        if p.returncode == 0:
            print("Pushed to GitHub Pages.")
        else:
            print("Push failed:", p.stderr[-500:])
    else:
        print("No changes since last sync.")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--share", default=SHARE_URL)
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--no-push", action="store_true")
    args = ap.parse_args()

    print("Fetching TripIt share link...")
    text = fetch_tripit(args.share)
    if not text or "TripIt" not in text[:2000]:
        print("ERROR: could not fetch TripIt page"); sys.exit(1)

    print("Parsing itinerary...")
    items = parse_tripit(text)
    print(f"  {len(items)} items found")

    cache = load_cache()
    stops = []
    for it in items:
        stops.extend(resolve_stop(it, cache))
    save_cache(cache)
    print(f"  {len(stops)} mapped stops")

    days, order = group_by_day(stops)
    os.makedirs(WORKDIR, exist_ok=True)

    kml = build_kml(days, order)
    with open(os.path.join(WORKDIR, "Iceland_Trip_Sep2026_Map.kml"), "w", encoding="utf-8") as f:
        f.write(kml)
    html = build_html(days, order)
    with open(os.path.join(WORKDIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    sheet = build_sheet(days, order)
    with open(os.path.join(WORKDIR, "Iceland_Driving_Sheet.md"), "w", encoding="utf-8") as f:
        f.write(sheet)

    print(f"Files: KML {len(kml)//1024}KB, HTML {len(html)//1024}KB, sheet {len(sheet)//1024}KB")
    publish(args.repo, WORKDIR, args.no_push)

if __name__ == "__main__":
    main()
