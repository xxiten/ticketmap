import os
import json
import html
import base64
import shutil
import folium
import logging
import requests
import argparse
from folium.plugins import Fullscreen, MarkerCluster
from geopy.geocoders import Photon
from geopy.distance import geodesic
from geopy.extra.rate_limiter import RateLimiter
from datetime import datetime

# === KONSTANTEN ===
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(_SCRIPT_DIR, 'config.json')
GEO_CACHE_FILE = os.path.join(_SCRIPT_DIR, 'geo_cache.json')
OUTPUT_MAP_FILE = '/var/www/html/index.html'
TICKET_BASE_URL = 'https://tickets.netixx.it:10443'
VERSION = '1.0.1'
LOGO_URL = 'https://www.netixx.it/wp-content/themes/netixx/img/logo.svg'

STATUS_COLOR = {
    "offen": "red",
    "in bearbeitung": "orange",
    "erledigt": "green"
}
DEFAULT_MARKER_COLOR = "blue"
APPROXIMATE_MARKER_COLOR = "black"

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
        'layer_tickets': "Tickets",
        'fullscreen': "Vollbildmodus",
        'fullscreen_exit': "Vollbild verlassen",
        'generated_at': "Generiert um",
        'ticket': "Ticket",
        'customer': "Kunde",
        'address': "Adresse",
        'type': "Art"
    },
    'it': {
        'warning_head': "Attenzione: I seguenti Ticket non sono stati localizzati esattamente:",
        'approx_marker': "(solo il comune localizzato)",
        'not_found_marker': "(localizzazione non riuscita)",
        'layer_tickets': "Ticket",
        'fullscreen': "Schermo intero",
        'fullscreen_exit': "Esci da schermo intero",
        'generated_at': "Generato alle",
        'ticket': "Ticket",
        'customer': "Cliente",
        'address': "Indirizzo",
        'type': "Tipo"
    },
    'en': {
        'warning_head': "Warning: The following Tickets could not be located exactly:",
        'approx_marker': "(only municipality found)",
        'not_found_marker': "(location failed)",
        'layer_tickets': "Tickets",
        'fullscreen': "Fullscreen",
        'fullscreen_exit': "Exit Fullscreen",
        'generated_at': "Generated at",
        'ticket': "Ticket",
        'customer': "Customer",
        'address': "Address",
        'type': "Type"
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


def load_json_cache(file_path):
    """Load JSON cache file if it exists."""
    if os.path.exists(file_path):
        with open(file_path, 'r') as f:
            return json.load(f)
    return {}


def save_json_cache(file_path, data):
    """Save data to JSON cache file."""
    with open(file_path, 'w') as f:
        json.dump(data, f, indent=4)


def fetch_data_from_api(token):
    """Fetch ticket data from API."""
    token = os.environ.get('TICKETMAP_API_TOKEN', token)
    url = (
        f'{TICKET_BASE_URL}/api2/Ticket/search/'
        f'?token={token}&params%5BtypeId%5D=6&params%5Bstatus%5D=1'
    )
    try:
        response = requests.get(url, timeout=10)
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


def get_marker_color(ticket, is_approximate=False):
    """Determine marker color based on ticket status and location accuracy."""
    if is_approximate:
        return APPROXIMATE_MARKER_COLOR
    status = ticket.get("Status", "").strip()
    key = normalize_status(status)
    return STATUS_COLOR.get(key, DEFAULT_MARKER_COLOR)


def extract_city(address):
    """Extract city name from comma-separated address."""
    if ',' in address:
        return address.split(',')[-1].strip()
    else:
        return None


def get_coordinates_extended(address, geo_cache, geocode_fn=None):
    """
    Get coordinates for address with caching and fallback logic.

    Args:
        address: Street address to geocode
        geo_cache: Dictionary cache for geocoded addresses
        geocode_fn: Rate-limited geocode callable (Nominatim RateLimiter)

    Returns:
        tuple: (coords, is_approximate, municipality)
            coords: (lat, lon) tuple or None
            is_approximate: True if only municipality was found
            municipality: Name of municipality if approximate
    """
    # Check cache first
    if address in geo_cache:
        entry = geo_cache[address]
        # Handle old 2-tuple format (backwards compatibility)
        if isinstance(entry, (list, tuple)) and len(entry) == 2 and all(isinstance(x, (int, float)) for x in entry):
            coords = tuple(entry)
            is_approx = False
            ortsteil = None
            # Upgrade cache entry to new format
            geo_cache[address] = (coords, is_approx, ortsteil)
            return coords, is_approx, ortsteil
        # Handle new 3-tuple format
        elif isinstance(entry, (list, tuple)) and len(entry) == 3:
            coords, is_approx, ortsteil = entry
            return coords, is_approx, ortsteil

    # Initialize a default geocode function if not provided
    if geocode_fn is None:
        geolocator = Photon(user_agent="street_mapper_idm", timeout=10)
        geocode_fn = RateLimiter(geolocator.geocode, min_delay_seconds=1,
                                 max_retries=3, error_wait_seconds=10,
                                 swallow_exceptions=True)

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
            geo_cache[address] = (coords, False, None)
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
            geo_cache[address] = (coords, True, ortsteil)
            return coords, True, ortsteil

    # Complete failure
    logging.warning(f"Adresse konnte nicht geocodiert werden: {address}")
    return None, False, None


def process_tickets_to_markers(data, center_point, radius_km, geo_cache, language='de', geo_cache_file=None, ticket_base_url=None):
    """
    Process ticket data into map markers.
    This ALWAYS runs regardless of cache status.

    Args:
        data: List of ticket dictionaries
        center_point: (lat, lon) tuple for radius center
        radius_km: Radius in kilometers
        geo_cache: Dictionary cache for geocoded addresses
        language: Language code ('de', 'it', 'en')
        geo_cache_file: Path to geo cache file for incremental saving

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
        status = ticket.get('Status', 'offen')

        if not address:
            logging.warning(f"Ticket {ticket_id} has no address, skipping")
            continue

        # Track whether this address is already cached
        already_cached = address in geo_cache

        # Get coordinates
        coords, is_approx, ortsteil = get_coordinates_extended(address, geo_cache, geocode_fn=geocode)

        # Save geo cache immediately after each new geocoding result
        if not already_cached and geo_cache_file:
            save_json_cache(geo_cache_file, geo_cache)

        if coords:
            distance = geodesic(center_point, coords).km
            if distance <= radius_km:
                # Build ticket URL
                ticket_url = html.escape(f"{ticket_base_url}/Ticket/view/id/{ticket_id}")

                # Build popup HTML
                popup_html = f"""
                <div style="width:300px;">
                    <table style="width:100%; border-collapse:collapse;">
                        <tr style="background:#f0f0f0; font-weight:bold;">
                            <td style="padding:5px; border:1px solid #ccc;">{lang['ticket']}</td>
                            <td style="padding:5px; border:1px solid #ccc;">{lang['customer']}</td>
                            <td style="padding:5px; border:1px solid #ccc;">{lang['address']}</td>
                            <td style="padding:5px; border:1px solid #ccc;">{lang['type']}</td>
                        </tr>
                        <tr>
                            <td style="padding:5px; border:1px solid #ccc;">
                                <a href="{ticket_url}" target="_blank">{html.escape(str(ticket_id))}</a>
                            </td>
                            <td style="padding:5px; border:1px solid #ccc;">{html.escape(customer_name)}</td>
                            <td style="padding:5px; border:1px solid #ccc;">{html.escape(address)}</td>
                            <td style="padding:5px; border:1px solid #ccc;">{html.escape(title)}</td>
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

    # Create feature group with clustering for markers
    ticket_layer = folium.FeatureGroup(name=lang['layer_tickets'])
    cluster = MarkerCluster().add_to(ticket_layer)

    # Add markers to map
    for marker in markers:
        folium.Marker(
            location=marker['coords'],
            popup=folium.Popup(marker['popup'], max_width=MAP_POPUP_MAX_WIDTH),
            tooltip=marker['tooltip'],
            icon=folium.Icon(color=marker['color'], icon='info-sign')
        ).add_to(cluster)

    ticket_layer.add_to(m)

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
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
    data = fetch_data_from_api(token=config['api_token'])

    if not data:
        logging.warning("No ticket data returned from API - map will have no markers")

    # Step 2: Load geo cache (separate from API cache)
    geo_cache = load_json_cache(GEO_CACHE_FILE)
    logging.info(f"Loaded geo cache with {len(geo_cache)} entries")

    # Step 3: ALWAYS process tickets into markers (never skip this step)
    ticket_base_url = config.get('ticket_base_url', TICKET_BASE_URL)
    output_map_file = config.get('output_map_file', OUTPUT_MAP_FILE)
    markers, warnings = process_tickets_to_markers(
        data=data,
        center_point=tuple(config['center_point']),
        radius_km=config['radius_km'],
        geo_cache=geo_cache,
        language=language,
        geo_cache_file=GEO_CACHE_FILE,
        ticket_base_url=ticket_base_url
    )

    # Step 4: Save updated geo cache
    save_json_cache(GEO_CACHE_FILE, geo_cache)
    logging.info(f"Saved geo cache with {len(geo_cache)} entries")
    logging.info(f"Summary: {len(markers)} markers placed, {len(warnings)} geocoding warnings")

    # Step 5: Generate and save map
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