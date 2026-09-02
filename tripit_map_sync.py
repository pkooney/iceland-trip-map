#!/usr/bin/env python3
"""
TripIt -> Google Maps overlay sync script (v2 - title-first geocoding).
Fetches a TripIt public share link, parses itinerary items, geocodes new
locations (cached), rebuilds KML + Leaflet HTML map + driving sheet, and
publishes to a GitHub Pages repo.
"""
import argparse, datetime as dt, json, os, re, subprocess, sys, time, unicodedata, urllib.request

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

SHARE_URL = "https://www.tripit.com/p/ACAF6D20819AD7BFA3F72D57C62D4435"
REPO = "pkooney/iceland-trip-map"
WORKDIR = os.path.expanduser("~/tripit_map")
CACHE_FILE = os.path.join(WORKDIR, "geocode_cache.json")
DAY_COLORS = ["#ff3b30", "#ff9500", "#34c759", "#007aff", "#af52de"]
KML_COLORS = {"#ff3b30": "ff0000ff", "#ff9500": "ff0088ff", "#34c759": "ff59c734",
              "#007aff": "ffff7a00", "#af52de": "ffde52af"}
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

MANUAL_COORDS = [
    ("hotel ranga", 63.7809, -20.2923, "Hotel Rangá, Hella, Iceland"),
    ("ranga restaurant", 63.7809, -20.2923, "Hotel Rangá, Hella, Iceland"),
    ("retreat at blue lagoon", 63.8796, -22.4495, "The Retreat at Blue Lagoon, Iceland"),
    ("blue lagoon", 63.8804, -22.4495, "Blue Lagoon, Iceland"),
    ("lava restaurant", 63.8804, -22.4495, "Lava Restaurant, Blue Lagoon, Iceland"),
    ("moss restaurant", 63.8796, -22.4495, "Moss Restaurant, The Retreat, Iceland"),
    ("dill restaurant", 64.1448, -21.9200, "Dill Restaurant, Reykjavík, Iceland"),
    ("edition reykjavik", 64.1502, -21.9326, "The EDITION Reykjavik, Iceland"),
    ("perlan", 64.1292, -21.9195, "Perlan, Reykjavík, Iceland"),
    ("viking world", 63.9791, -22.5399, "Viking World Museum, Iceland"),
    ("hvalsneskirkja", 63.9934, -22.7395, "Hvalsneskirkja, Iceland"),
    ("massage", 63.8796, -22.4495, "The Retreat Spa, Blue Lagoon, Iceland"),
    ("eyjafjallajokull", 63.5436, -19.6677, "Eyjafjallajökull viewpoint, Iceland"),
]

def manual_coord(query):
    q = norm(query)
    for frag, lat, lon, display in MANUAL_COORDS:
        if norm(frag) in q:
            return {"lat": lat, "lon": lon, "display": display}
    return None

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
    if items:
        return items
    return parse_tripit_record_format(text)

def month_date_to_iso(s):
    m = re.search(r"(\d{1,2})\.\s*September\s+(2026)", s, re.I)
    if not m:
        return None
    return f"{m.group(2)}-09-{int(m.group(1)):02d}"

def slash_date_to_iso(s):
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", s)
    if not m:
        return None
    mm, dd, yyyy = map(int, m.groups())
    return f"{yyyy:04d}-{mm:02d}-{dd:02d}"

def day_label(iso_date):
    try:
        d = dt.date.fromisoformat(iso_date)
        return d.strftime("%a, Sep %-d")
    except ValueError:
        d = dt.date.fromisoformat(iso_date)
        return d.strftime("%a, Sep %#d")

def first_time(block):
    m = re.search(r"(?:###\s*)?(\d{1,2}:\d{2})\s*(AM|PM)\s*(?:GMT|EDT)?", block, re.I)
    return f"{m.group(1)} {m.group(2).upper()} GMT" if m else None

def clean_record_title(line):
    line = re.sub(r"^#+\s*", "", line.strip())
    bad = {"depart", "arrive", "end", "source:", "note url", "location name", "address",
           "pickup phone #", "drop-off phone #", "phone #", "calendar", "move", "cancel"}
    if re.search(r"\b\d{1,2}:\d{2}\s*(?:am|pm)\b|\b\d{1,2}/\d{1,2}/\d{4}\b", line, re.I):
        return ""
    if re.fullmatch(r"\d+H(?:,\s*\d+M)?|\d+M", line, re.I):
        return ""
    return line if line and line.lower() not in bad and not line.startswith("[[TripItALT") else ""

def infer_record_date(block, current_date):
    # Prefer explicit event dates: "on  9/3/2026", "9/3/2026 at 3:00pm", or Noona's date wording.
    m = re.search(r"\bon\s+(\d{1,2}/\d{1,2}/\d{4})", block, re.I)
    if m:
        return slash_date_to_iso(m.group(1))
    m = re.search(r"\b(\d{1,2}/\d{1,2}/\d{4})\s+at\s+\d", block, re.I)
    if m:
        return slash_date_to_iso(m.group(1))
    md = month_date_to_iso(block)
    if md:
        return md
    # For car/hotel blocks that only show checkout/dropoff date plus duration, back into start date.
    m = re.search(r"\b(\d{1,2}/\d{1,2}/\d{4})\s*\((\d+)\s+(?:Night|Nights|Day|Days)\)", block, re.I)
    if m:
        end = dt.date.fromisoformat(slash_date_to_iso(m.group(1)))
        return (end - dt.timedelta(days=int(m.group(2)))).isoformat()
    # Restaurant records sometimes omit the date; TripIt lists records chronologically.
    return current_date

def title_from_record(block):
    if "DILL Restaurant" in block:
        return "Dill Restaurant"
    if "Moss Restaurant" in block:
        return "Moss Restaurant"
    if "Lava Restaurant" in block:
        return "Lava Restaurant"
    if "Ranga Restaurant" in block:
        return "Ranga Restaurant"
    if "Vínstofa Friðheima" in block:
        return "Vínstofa Friðheima"
    if "Skálakot Manor Restaurant" in block:
        return "Skálakot Manor Restaurant"
    if "Bridge Between Continents" in block and "Gunnuhver" in block:
        return "Bridge Between Continents - Reykjanesviti Lighthouse & Valahnúkamöl Cliffs - Gunnuhver Hot Springs - Brimketill Pool"
    if "Blue Lagoon Ritual" in block or "ritual chamber" in block:
        return "Blue Lagoon Ritual"
    if "massage" in norm(block):
        return "Massage"
    if "Viking World" in block:
        return "Viking World Museum"
    if "Perlan" in block:
        return "Perlan Nature Museum"
    supplier = re.search(r"^Supplier Name:(.+)$", block, re.M)
    supplier_name = supplier.group(1).strip() if supplier else ""
    if supplier_name:
        if "### Pick Up" in block:
            return f"Pick Up {supplier_name} Car Rental"
        if "### Check In" in block:
            return f"Arrive {supplier_name}"
        return supplier_name
    lines = [l.strip() for l in block.splitlines() if l.strip()]
    for i, line in enumerate(lines):
        if re.match(r"^(?:###\s*)?\d{1,2}:\d{2}\s*(?:AM|PM)\s*(?:GMT|EDT)?$", line, re.I):
            for cand in lines[i+1:i+6]:
                title = clean_record_title(cand)
                if title:
                    return title
    return ""

def parse_tripit_record_format(text):
    items, current_date = [], None
    chunks = re.split(r"!\[Image \d+: seat alerts icon\]\(https://www\.tripit\.com/images/clear\.gif\)", text)
    for block in chunks[1:]:
        title = title_from_record(block)
        iso = infer_record_date(block, current_date)
        tm = first_time(block)
        if iso:
            current_date = iso
        # Some public TripIt records redact the title as generic "Location Name"; use known itinerary slots.
        slot_titles = {
            ("2026-09-03", "2:00 PM GMT"): "Perlan Nature Museum",
            ("2026-09-05", "9:55 AM GMT"): "Roadside Eyjafjallajökull Viewpoint",
            ("2026-09-05", "3:30 PM GMT"): "Arrive The Retreat at Blue Lagoon Iceland",
            ("2026-09-06", "1:30 PM GMT"): "Hvalsneskirkja",
            ("2026-09-06", "3:00 PM GMT"): "Massage",
            ("2026-09-07", "11:00 AM GMT"): "Depart The Retreat at Blue Lagoon Iceland",
            ("2026-09-07", "12:00 PM GMT"): "Viking World Museum",
        }
        if (not title or title.lower() == "location name") and iso and tm:
            title = slot_titles.get((iso, tm), title)
        if not title or re.search(r"\bJFK\b|New York|Icelandair|Dial 7", title, re.I):
            continue
        if not iso:
            continue
        items.append({"day": day_label(iso), "date": iso, "title": title,
                      "time": tm, "address": None, "raw": block})
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
    fixed = manual_coord(query)
    if fixed:
        return fixed
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
        f"<div class=\"legend-row\"><i style=\"background:{DAY_COLORS[i % len(DAY_COLORS)]}\"></i>"
        f"<span class=\"legend-day\">{day}, 2026</span>"
        f"<code>{DAY_COLORS[i % len(DAY_COLORS)]}</code>"
        f"<span class=\"legend-count\">{len(days[day])} stops</span></div>"
        for i, day in enumerate(order))
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Iceland Trip Sep 2026 - Map</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>html,body{{margin:0;height:100%}}#map{{height:100vh}}
.legend{{background:#fff;padding:12px 14px;border-radius:10px;box-shadow:0 2px 8px rgba(0,0,0,.32);font:13px/1.45 system-ui,-apple-system,Segoe UI,sans-serif;color:#1f2937;min-width:245px}}
.legend b{{display:block;margin-bottom:2px;font-size:14px}}.legend .legend-sub{{display:block;margin-bottom:8px;color:#6b7280;font-size:12px}}
.legend-row{{display:grid;grid-template-columns:16px 1fr auto;gap:7px;align-items:center;margin:5px 0}}
.legend i{{display:inline-block;width:13px;height:13px;border-radius:50%;border:1px solid rgba(0,0,0,.28)}}
.legend code{{font-size:11px;background:#f3f4f6;padding:1px 4px;border-radius:4px;color:#374151}}
.legend-count{{grid-column:2 / 4;color:#6b7280;font-size:11px;margin-top:-3px}}</style>
</head><body><div id="map"></div><script>
function coloredIcon(c){{return L.divIcon({{className:'',html:`<div style="width:14px;height:14px;background:${{c}};border:2px solid #fff;border-radius:50%;box-shadow:0 0 4px rgba(0,0,0,.5)"></div>`,iconSize:[18,18],iconAnchor:[9,9]}});}}
var map=L.map('map');
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',{{maxZoom:19,attribution:'&copy; OpenStreetMap contributors'}}).addTo(map);
{chr(10).join(markers)}
var b=[];map.eachLayer(function(l){{if(l.getLatLng)b.push(l.getLatLng());}});if(b.length)map.fitBounds(b,{{padding:[40,40]}});
L.control({{position:'bottomleft'}}).onAdd=function(){{var d=L.DomUtil.create('div','legend');d.innerHTML='<b>Map legend</b><span class="legend-sub">Day/date → pin & route color</span>{legend_items}';return d;}}.addTo(map);
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
    # Jina's rendering of TripIt public pages is valid even when the first
    # couple KB contain only the page title and lowercase source URL, so accept
    # either the TripIt marker, source URL, or itinerary title text.
    head = (text or "")[:4000].lower()
    if not text or not any(marker in head for marker in ("tripit", "tripit.com/p/", "check out my itinerary")):
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
