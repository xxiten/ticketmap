import os
import json
import html
import base64
import shutil
import folium
import logging
import requests
import argparse
from folium.plugins import Fullscreen
from geopy.geocoders import Photon
from geopy.distance import geodesic
from geopy.extra.rate_limiter import RateLimiter
from datetime import datetime, timedelta

# === KONSTANTEN ===
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(_SCRIPT_DIR, 'config.json')
OUTPUT_MAP_FILE = '/var/www/html/index.html'
TICKET_BASE_URL = 'https://tickets.netixx.it:10443'
VERSION = '1.0.2'
LOGO_URL = 'https://www.netixx.it/wp-content/themes/netixx/img/logo.svg'

STATUS_COLOR = {
    "offen": "blue",
    "in bearbeitung": "orange",
    "erledigt": "green"
}
DEFAULT_MARKER_COLOR = "blue"
APPROXIMATE_MARKER_COLOR = "black"
OVERDUE_MARKER_COLOR = "darkred"
OVERDUE_DAYS = 30

# === MAP CONFIGURATION ===
MAP_DEFAULT_ZOOM = 11
MAP_TILES = 'OpenStreetMap'
MAP_POPUP_MAX_WIDTH = 350
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
)

# === SPRACH-TEXTE ===
LANG_TEXT = {
    'de': {
        'warning_head': "Achtung: Folgende Tickets konnten nicht exakt lokalisiert werden:",
        'approx_marker': "(nur Gemeinde lokalisiert)",
        'not_found_marker': "(keine Lokalisierung möglich)",
        'fullscreen': "Vollbildmodus",
        'fullscreen_exit': "Vollbild verlassen",
        'generated_at': "Generiert am",
        'ticket': "Ticket",
        'customer': "Kunde",
        'address': "Adresse",
        'type': "ToDo",
        'created': "Erstellt",
        'overdue': "älter als 30 Tage"
    },
    'it': {
        'warning_head': "Attenzione: I seguenti Ticket non sono stati localizzati esattamente:",
        'approx_marker': "(solo il comune localizzato)",
        'not_found_marker': "(localizzazione non riuscita)",
        'fullscreen': "Schermo intero",
        'fullscreen_exit': "Esci da schermo intero",
        'generated_at': "Generato alle",
        'ticket': "Ticket",
        'customer': "Cliente",
        'address': "Indirizzo",
        'type': "ToDo",
        'created': "Creato",
        'overdue': "più di 30 giorni"
    },
    'en': {
        'warning_head': "Warning: The following Tickets could not be located exactly:",
        'approx_marker': "(only municipality found)",
        'not_found_marker': "(location failed)",
        'fullscreen': "Fullscreen",
        'fullscreen_exit': "Exit Fullscreen",
        'generated_at': "Generated at",
        'ticket': "Ticket",
        'customer': "Customer",
        'address': "Address",
        'type': "ToDo",
        'created': "Created",
        'overdue': "older than 30 days"
    }
}


def fetch_logo_as_base64(url):
    """Fetch logo image and return as base64 data URL for embedding in HTML."""
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        mime = response.headers.get('Content-Type', 'image/png').split(';')[0]
        data = base64.b64encode(response.content).decode('utf-8')
        return f"data:{mime};base64,{data}"
    except Exception as e:
        logging.warning(f"Logo konnte nicht geladen werden: {e}")
        return url  # fall back to remote URL


def load_config():
    """Load configuration from JSON file."""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    else:
        logging.error(f"Konfigurationsdatei '{CONFIG_FILE}' fehlt.")
        raise FileNotFoundError(f"Konfigurationsdatei '{CONFIG_FILE}' fehlt.")


def fetch_data_from_api(token, ticket_base_url=None):
    """Fetch ticket data from API."""
    token = os.environ.get('TICKETMAP_API_TOKEN', token)
    base_url = ticket_base_url or TICKET_BASE_URL
    url = (
        f'{base_url}/api2/Ticket/search/'
        f'?token={token}&params%5BtypeId%5D=6&params%5Bstatus%5D=1'
    )
    try:
        response = requests.get(url, timeout=60)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.Timeout:
        logging.error("Timeout beim API-Request.")
    except requests.exceptions.HTTPError as e:
        logging.error(f"HTTP-Fehler beim API-Request ({response.status_code}): {e}")
    except Exception as e:
        logging.error(f"Unbekannter Fehler beim API-Request: {type(e).__name__}: {e}")
    return []



def normalize_status(status):
    """Normalize status string to standard format."""
    s = status.lower().replace(" ", "")
    if "erledigt" in s:
        return "erledigt"
    elif "bearbeitung" in s:
        return "in bearbeitung"
    elif "offen" in s:
        return "offen"
    return None


def is_ticket_overdue(created_str):
    """Return True if ticket was created more than OVERDUE_DAYS days ago."""
    if not created_str:
        return False
    try:
        created = datetime.strptime(created_str, "%Y-%m-%d %H:%M:%S")
        return (datetime.now() - created) > timedelta(days=OVERDUE_DAYS)
    except ValueError:
        return False


def get_marker_color(ticket, is_approximate=False):
    """Determine marker color based on ticket age and location accuracy."""
    if is_approximate:
        return APPROXIMATE_MARKER_COLOR
    if is_ticket_overdue(ticket.get("Created", "")):
        return OVERDUE_MARKER_COLOR
    status = ticket.get("Status", "").strip()
    key = normalize_status(status)
    return STATUS_COLOR.get(key, DEFAULT_MARKER_COLOR)


def extract_city(address):
    """Extract city name from comma-separated address."""
    if ',' in address:
        return address.split(',')[-1].strip()
    else:
        return None


def get_coordinates_extended(address, geocode_fn=None):
    """
    Get coordinates for address with fallback logic.

    Args:
        address: Street address to geocode
        geocode_fn: Rate-limited geocode callable (Nominatim RateLimiter)

    Returns:
        tuple: (coords, is_approximate, municipality)
            coords: (lat, lon) tuple or None
            is_approximate: True if only municipality was found
            municipality: Name of municipality if approximate
    """
    # Try different address variants
    variants = [
        address + ", South Tyrol, Italy",
        address + ", Südtirol, Italien",
        address
    ]

    for variant in variants:
        try:
            location = geocode_fn(variant)
        except Exception as e:
            logging.warning(f"Geocoding-Fehler für '{variant}': {type(e).__name__}: {e}")
            location = None
        if location:
            coords = (location.latitude, location.longitude)
            return coords, False, None

    # Fallback: Try just the municipality
    ortsteil = extract_city(address)
    if ortsteil:
        try:
            location = geocode_fn(f"{ortsteil}, South Tyrol, Italy")
        except Exception as e:
            logging.warning(f"Geocoding-Fehler für Gemeinde '{ortsteil}': {type(e).__name__}: {e}")
            location = None
        if location:
            coords = (location.latitude, location.longitude)
            return coords, True, ortsteil

    # Complete failure
    logging.warning(f"Adresse konnte nicht geocodiert werden: {address}")
    return None, False, None


def process_tickets_to_markers(data, center_point, radius_km, language='de', ticket_base_url=None):
    """
    Process ticket data into map markers.

    Args:
        data: List of ticket dictionaries
        center_point: (lat, lon) tuple for radius center
        radius_km: Radius in kilometers
        language: Language code ('de', 'it', 'en')

    Returns:
        tuple: (markers, warning_list)
            markers: List of marker dictionaries
            warning_list: List of tickets with geocoding issues
    """
    markers = []
    warning_list = []
    geolocator = Photon(user_agent="street_mapper_idm", timeout=10)
    geocode = RateLimiter(
        geolocator.geocode,
        min_delay_seconds=1,
        max_retries=3,
        error_wait_seconds=10,
        swallow_exceptions=True
    )
    lang = LANG_TEXT.get(language, LANG_TEXT['de'])
    if ticket_base_url is None:
        ticket_base_url = TICKET_BASE_URL

    logging.info(f"Processing {len(data)} tickets...")

    for ticket in data:
        address = ticket.get('Address', '')
        ticket_id = ticket.get('Id', '')
        customer_name = ticket.get('CustomerName', '')
        title = ticket.get('Title', '')
        created_str = ticket.get('Created', '')

        if not address:
            logging.warning(f"Ticket {ticket_id} has no address, skipping")
            continue

        # Get coordinates
        coords, is_approx, ortsteil = get_coordinates_extended(address, geocode_fn=geocode)

        if coords:
            distance = geodesic(center_point, coords).km
            if distance <= radius_km:
                # Build ticket URL
                ticket_url = html.escape(f"{ticket_base_url}/Ticket/view/id/{ticket_id}")

                # Build popup HTML
                overdue = is_ticket_overdue(created_str)
                created_display = html.escape(created_str[:10]) if created_str else ''
                created_cell_style = "padding:5px 8px; border:1px solid #ccc; color:#c0392b; font-weight:bold;" if overdue else "padding:5px 8px; border:1px solid #ccc;"
                overdue_label = f" ⚠ {lang['overdue']}" if overdue else ''
                popup_html = f"""
                <div style="width:320px; font-size:13px;">
                    <table style="width:100%; border-collapse:collapse;">
                        <tr>
                            <td style="padding:5px 8px; background:#f0f0f0; font-weight:bold; border:1px solid #ccc; white-space:nowrap; width:1%;">{lang['ticket']}</td>
                            <td style="padding:5px 8px; border:1px solid #ccc;"><a href="{ticket_url}" target="_blank">{html.escape(str(ticket_id))}</a></td>
                        </tr>
                        <tr>
                            <td style="padding:5px 8px; background:#f0f0f0; font-weight:bold; border:1px solid #ccc; white-space:nowrap;">{lang['customer']}</td>
                            <td style="padding:5px 8px; border:1px solid #ccc;">{html.escape(customer_name)}</td>
                        </tr>
                        <tr>
                            <td style="padding:5px 8px; background:#f0f0f0; font-weight:bold; border:1px solid #ccc; white-space:nowrap;">{lang['address']}</td>
                            <td style="padding:5px 8px; border:1px solid #ccc;">{html.escape(address)}</td>
                        </tr>
                        <tr>
                            <td style="padding:5px 8px; background:#f0f0f0; font-weight:bold; border:1px solid #ccc; white-space:nowrap;">{lang['type']}</td>
                            <td style="padding:5px 8px; border:1px solid #ccc;">{html.escape(title)}</td>
                        </tr>
                        <tr>
                            <td style="padding:5px 8px; background:#f0f0f0; font-weight:bold; border:1px solid #ccc; white-space:nowrap;">{lang['created']}</td>
                            <td style="{created_cell_style}">{created_display}{overdue_label}</td>
                        </tr>
                    </table>
                </div>
                """

                # Determine marker color
                marker_color = get_marker_color(ticket, is_approximate=is_approx)

                # Create marker dictionary
                marker = {
                    'coords': coords,
                    'popup': popup_html,
                    'tooltip': f"{ticket_id} - {customer_name}",
                    'color': marker_color,
                    'ticket_id': ticket_id
                }
                markers.append(marker)

                # Add to warning list if approximate or not found
                if is_approx:
                    warning_list.append({
                        'ticket_id': ticket_id,
                        'customer_name': customer_name,
                        'address': address,
                        'reason': 'approximate',
                        'ortsteil': ortsteil
                    })
        else:
            # Geocoding completely failed
            logging.warning(f"Skipping ticket {ticket_id} - geocoding failed for '{address}'")
            warning_list.append({
                'ticket_id': ticket_id,
                'customer_name': customer_name,
                'address': address,
                'reason': 'not_found',
                'ortsteil': None
            })

    logging.info(f"Created {len(markers)} markers within {radius_km}km radius")
    logging.info(f"Warning list contains {len(warning_list)} entries")
    return markers, warning_list


def create_folium_map(markers, warning_list, center_point, language='de', ticket_base_url=None):
    """
    Create Folium map with markers and warning table.

    Args:
        markers: List of marker dictionaries
        warning_list: List of tickets with geocoding issues
        center_point: (lat, lon) tuple for map center
        language: Language code ('de', 'it', 'en')

    Returns:
        folium.Map object
    """
    lang = LANG_TEXT.get(language, LANG_TEXT['de'])
    if ticket_base_url is None:
        ticket_base_url = TICKET_BASE_URL

    # Create base map
    m = folium.Map(
        location=center_point,
        zoom_start=MAP_DEFAULT_ZOOM,
        tiles=MAP_TILES
    )

    # Add fullscreen button
    Fullscreen(
        position='topleft',
        title=lang['fullscreen'],
        title_cancel=lang['fullscreen_exit'],
        force_separate_button=True
    ).add_to(m)

    # Add markers to map
    for marker in markers:
        folium.Marker(
            location=marker['coords'],
            popup=folium.Popup(marker['popup'], max_width=MAP_POPUP_MAX_WIDTH),
            tooltip=marker['tooltip'],
            icon=folium.Icon(color=marker['color'], icon='info-sign')
        ).add_to(m)

    # Auto-fit map to show all markers
    if markers:
        all_coords = [mk['coords'] for mk in markers]
        m.fit_bounds(all_coords, padding=(30, 30))

    # Add warning table if there are warnings
    if warning_list:
        warning_html = f"""
        <div style="position: fixed; 
                    top: 20px; 
                    right: 20px; 
                    width: 500px; 
                    max-height: 300px;
                    background: white; 
                    border: 2px solid #ccc; 
                    border-radius: 5px;
                    padding: 10px;
                    overflow-y: auto;
                    z-index: 9999;
                    box-shadow: 0 0 10px rgba(0,0,0,0.3);">
            <h4 style="margin-top:0; color:#d9534f;">{lang['warning_head']}</h4>
            <table style="width:100%; border-collapse:collapse; font-size:12px;">
                <tr style="background:#f0f0f0; font-weight:bold;">
                    <td style="padding:5px; border:1px solid #ccc;">{lang['ticket']}</td>
                    <td style="padding:5px; border:1px solid #ccc;">{lang['customer']}</td>
                    <td style="padding:5px; border:1px solid #ccc;">{lang['address']}</td>
                    <td style="padding:5px; border:1px solid #ccc;">Status</td>
                </tr>
        """

        for w in warning_list:
            if w['reason'] == 'approximate':
                type_display = f"{lang['approx_marker']}: {w.get('ortsteil', '')}"
            else:
                type_display = lang['not_found_marker']

            w_ticket_url = html.escape(f"{ticket_base_url}/Ticket/view/id/{w['ticket_id']}")
            warning_html += f"""
                <tr>
                    <td style="padding:5px; border:1px solid #ccc;">
                        <a href="{w_ticket_url}" target="_blank">{html.escape(str(w['ticket_id']))}</a>
                    </td>
                    <td style="padding:5px; border:1px solid #ccc;">{html.escape(w['customer_name'])}</td>
                    <td style="padding:5px; border:1px solid #ccc;">{html.escape(w['address'])}</td>
                    <td style="padding:5px; border:1px solid #ccc;">{html.escape(type_display)}</td>
                </tr>
            """

        warning_html += """
            </table>
        </div>
        """

        m.get_root().html.add_child(folium.Element(warning_html))

    # Add logo and timestamp
    timestamp = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    logo_src = fetch_logo_as_base64(LOGO_URL)
    logo_html = f"""
    <div style="position: fixed; 
                top: 10px; 
                left: 50%; 
                transform: translateX(-50%);
                z-index: 9999;">
        <img src="{logo_src}" style="height:90px; display:block; mix-blend-mode:multiply;">
    </div>
    <div style="position: fixed; 
                bottom: 10px; 
                left: 50%; 
                transform: translateX(-50%);
                background: white; 
                padding: 6px 12px; 
                border: 2px solid #ccc; 
                border-radius: 5px;
                z-index: 9999;
                font-size:11px;
                box-shadow: 0 0 10px rgba(0,0,0,0.3);">
        {lang['generated_at']}: {timestamp}
    </div>
    """
    m.get_root().html.add_child(folium.Element(logo_html))

    info_html = f"""
    <div style="position: fixed;
                bottom: 10px;
                left: 10px;
                background: white;
                padding: 6px 12px;
                border: 2px solid #ccc;
                border-radius: 5px;
                z-index: 9999;
                font-size: 11px;
                box-shadow: 0 0 10px rgba(0,0,0,0.3);">
        v{VERSION} &mdash; Created for the people by <a href="https://www.netixx.it" target="_blank">www.netixx.it</a>
    </div>
    """
    m.get_root().html.add_child(folium.Element(info_html))

    overdue_count = sum(1 for mk in markers if mk['color'] == OVERDUE_MARKER_COLOR)
    counter_html = f"""
    <div style="position: fixed;
                bottom: 10px;
                right: 10px;
                background: white;
                padding: 6px 12px;
                border: 2px solid #ccc;
                border-radius: 5px;
                z-index: 9999;
                font-size: 11px;
                box-shadow: 0 0 10px rgba(0,0,0,0.3);">
        Offene Tickets: <strong>{len(markers)}</strong><br>
        Stinkende Tickets: <strong style="color:#c0392b;">{overdue_count}</strong>
    </div>
    """
    m.get_root().html.add_child(folium.Element(counter_html))

    godzilla_html = f"""
    <script>
    (function() {{
        var CENTER    = [{center_point[0]}, {center_point[1]}];
        var SPREAD    = 0.25;
        var SPEED_KMH = 250;
        var M_PER_LAT = 111000;
        var M_PER_LON = 111000 * Math.cos(46.9 * Math.PI / 180);
        var FPS       = 60;
        var M_PER_FRAME = SPEED_KMH * 1000 / 3600 / FPS;

        var BRUNECK         = [46.7963, 11.9358];
        var START_RADIUS_KM = 50;

        function getMap() {{
            var keys = Object.keys(window);
            for (var i = 0; i < keys.length; i++) {{
                try {{
                    var o = window[keys[i]];
                    if (o && o._leaflet_id && typeof o.latLngToContainerPoint === 'function') return o;
                }} catch(e) {{}}
            }}
            return null;
        }}

        function randomPoint() {{
            return [
                CENTER[0] + (Math.random() - 0.5) * SPREAD * 2,
                CENTER[1] + (Math.random() - 0.5) * SPREAD * 2
            ];
        }}

        function randomStartPoint(cb) {{
            var r     = Math.sqrt(Math.random()) * START_RADIUS_KM * 1000;
            var theta = Math.random() * 2 * Math.PI;
            var lat   = BRUNECK[0] + (r * Math.cos(theta)) / 111000;
            var lon   = BRUNECK[1] + (r * Math.sin(theta)) / (111000 * Math.cos(BRUNECK[0] * Math.PI / 180));
            var url   = 'https://router.project-osrm.org/nearest/v1/driving/' + lon + ',' + lat;
            fetch(url)
                .then(function(res) {{ return res.json(); }})
                .then(function(d) {{
                    if (d.waypoints && d.waypoints[0]) {{
                        var loc = d.waypoints[0].location;
                        cb([loc[1], loc[0]]);
                    }} else {{
                        cb([lat, lon]);
                    }}
                }})
                .catch(function() {{ cb([lat, lon]); }});
        }}

        function segDist(a, b) {{
            var dlat = (b[1] - a[1]) * M_PER_LAT;
            var dlon = (b[0] - a[0]) * M_PER_LON;
            return Math.sqrt(dlat*dlat + dlon*dlon);
        }}

        function fetchRoute(from, to, cb) {{
            var url = 'https://router.project-osrm.org/route/v1/driving/' +
                from[1] + ',' + from[0] + ';' + to[1] + ',' + to[0] +
                '?overview=full&geometries=geojson';
            fetch(url)
                .then(function(r) {{ return r.json(); }})
                .then(function(d) {{
                    if (d.routes && d.routes[0]) cb(d.routes[0].geometry.coordinates);
                    else cb(null);
                }})
                .catch(function() {{ cb(null); }});
        }}

        var el = document.createElement('div');
        el.style.cssText = 'position:fixed;font-size:36px;z-index:9998;pointer-events:none;transform:translate(-50%,-100%);line-height:1;';
        el.textContent = '🦖';
        document.body.appendChild(el);

        var pos = null, route = [], seg = 0, t = 0;

        function place() {{
            var map = getMap();
            if (!map || !pos) return;
            var p = map.latLngToContainerPoint(pos);
            var r = map.getContainer().getBoundingClientRect();
            el.style.left = (r.left + p.x) + 'px';
            el.style.top  = (r.top  + p.y) + 'px';
        }}

        function step() {{
            if (!route.length || seg >= route.length - 1) {{ next(); return; }}
            var a = route[seg], b = route[seg + 1];
            el.style.transform = 'translate(-50%,-100%) scaleX(' + (b[0] >= a[0] ? 1 : -1) + ')';
            var dist = segDist(a, b);
            t += dist > 0 ? M_PER_FRAME / dist : 1;
            while (t >= 1 && seg < route.length - 1) {{ t -= 1; seg++; }}
            if (seg >= route.length - 1) {{ next(); return; }}
            var aa = route[seg], bb = route[seg + 1];
            pos = [aa[1] + (bb[1] - aa[1]) * t, aa[0] + (bb[0] - aa[0]) * t];
            place();
            requestAnimationFrame(step);
        }}

        function next() {{
            var from = pos;
            var to = randomPoint();
            fetchRoute(from, to, function(coords) {{
                if (coords && coords.length > 1) {{
                    route = coords; seg = 0; t = 0;
                    requestAnimationFrame(step);
                }} else {{
                    setTimeout(next, 3000);
                }}
            }});
        }}

        function init() {{
            var map = getMap();
            if (!map) {{ setTimeout(init, 500); return; }}
            map.on('move zoom resize', place);
            randomStartPoint(function(startPos) {{
                pos = startPos;
                next();
            }});
        }}

        setTimeout(init, 1200);
    }})();
    </script>
    """
    m.get_root().html.add_child(folium.Element(godzilla_html))

    return m


def generate_map(config, language='de'):
    """
    Main function - orchestrates the complete workflow.

    Args:
        config: Configuration dictionary
        language: Language code ('de', 'it', 'en')

    Returns:
        folium.Map object
    """
    logging.info("=== Starting map generation ===")

    # Step 1: Fetch ticket data from API
    ticket_base_url = config.get('ticket_base_url', TICKET_BASE_URL)
    output_map_file = config.get('output_map_file', OUTPUT_MAP_FILE)
    data = fetch_data_from_api(token=config['api_token'], ticket_base_url=ticket_base_url)

    if not data:
        logging.warning("No ticket data returned from API - map will have no markers")

    # Step 2: Process tickets into markers (no caching)
    markers, warnings = process_tickets_to_markers(
        data=data,
        center_point=tuple(config['center_point']),
        radius_km=config['radius_km'],
        language=language,
        ticket_base_url=ticket_base_url
    )

    logging.info(f"Summary: {len(markers)} markers placed, {len(warnings)} geocoding warnings")

    # Step 3: Generate and save map
    map_obj = create_folium_map(
        markers=markers,
        warning_list=warnings,
        center_point=tuple(config['center_point']),
        language=language,
        ticket_base_url=ticket_base_url
    )

    tmp_file = output_map_file + '.tmp'
    map_obj.save(tmp_file)
    shutil.move(tmp_file, output_map_file)
    logging.info(f"Map generated successfully: {output_map_file}")
    logging.info("=== Map generation complete ===")

    return map_obj


def main():
    """Main entry point with argument parsing."""
    parser = argparse.ArgumentParser(description='Generate ticket map')
    parser.add_argument('--language', '-l',
                        choices=['de', 'it', 'en'],
                        default=None,
                        help='Language for map labels')

    args = parser.parse_args()

    # Load configuration
    config = load_config()

    # Generate map
    language = args.language or config.get('language', 'de')
    generate_map(
        config=config,
        language=language
    )


if __name__ == "__main__":
    main()