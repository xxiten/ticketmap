import os
import json
import html
import folium
import logging
import requests
import argparse
from folium.plugins import Fullscreen
from geopy.geocoders import Nominatim
from geopy.distance import geodesic
from datetime import datetime

# === KONSTANTEN ===
CONFIG_FILE = 'config.json'
CACHE_FILE = 'api_cache.json'
GEO_CACHE_FILE = 'geo_cache.json'
OUTPUT_MAP_FILE = '/var/www/html/index.html'
LOGO_URL = 'https://www.netixx.it/fileadmin/user_upload/Netixx_Logo_rgb_digital.png'

STATUS_COLOR = {
    "offen": "red",
    "in bearbeitung": "orange",
    "erledigt": "green"
}
DEFAULT_MARKER_COLOR = "blue"
APPROXIMATE_MARKER_COLOR = "black"

# === LOGGING ===
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

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    else:
        logging.error(f"Konfigurationsdatei '{CONFIG_FILE}' fehlt.")
        raise FileNotFoundError(f"Konfigurationsdatei '{CONFIG_FILE}' fehlt.")

def load_json_cache(file_path):
    if os.path.exists(file_path):
        with open(file_path, 'r') as f:
            return json.load(f)
    return {}

def save_json_cache(file_path, data):
    with open(file_path, 'w') as f:
        json.dump(data, f, indent=4)

def fetch_data_from_api(token):
    url = (
        f'https://tickets.netixx.it:10443/api2/Ticket/search/'
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
    s = status.lower().replace(" ", "")
    if "erledigt" in s:
        return "erledigt"
    elif "bearbeitung" in s:
        return "in bearbeitung"
    elif "offen" in s:
        return "offen"
    return None

def get_marker_color(ticket, is_approximate=False):
    if is_approximate:
        return APPROXIMATE_MARKER_COLOR
    status = ticket.get("Status", "").strip()
    key = normalize_status(status)
    return STATUS_COLOR.get(key, DEFAULT_MARKER_COLOR)

def extract_city(address):
    if ',' in address:
        return address.split(',')[-1].strip()
    else:
        return None

def get_coordinates_extended(address, geo_cache, geolocator=None):
    if address in geo_cache:
        entry = geo_cache[address]
        if isinstance(entry, (list, tuple)) and len(entry) == 2 and all(isinstance(x, (int, float)) for x in entry):
            coords = tuple(entry)
            is_approx = False
            ortsteil = None
            geo_cache[address] = (coords, is_approx, ortsteil)
            return coords, is_approx, ortsteil
        elif isinstance(entry, (list, tuple)) and len(entry) == 3:
            coords, is_approx, ortsteil = entry
            return coords, is_approx, ortsteil

    if geolocator is None:
        geolocator = Nominatim(user_agent="street_mapper_idm")

    variants = [
        address + ", South Tyrol, Italy",
        address + ", Südtirol, Italien",
        address
    ]
    for variant in variants:
        try:
            location = geolocator.geocode(variant, timeout=5)
            if location:
                coords = (location.latitude, location.longitude)
                geo_cache[address] = (coords, False, None)
                return coords, False, None
        except Exception as e:
            logging.warning(f"Geocoding-Fehler für '{variant}': {type(e).__name__}: {e}")

    ortsteil = extract_city(address)
    if ortsteil:
        try:
            location = geolocator.geocode(f"{ortsteil}, South Tyrol, Italy", timeout=5)
            if location:
                coords = (location.latitude, location.longitude)
                geo_cache[address] = (coords, True, ortsteil)
                return coords, True, ortsteil
        except Exception as e:
            logging.warning(f"Geocoding-Fehler für Gemeinde '{ortsteil}': {type(e).__name__}: {e}")

    logging.warning(f"Adresse konnte nicht geocodiert werden: {address}")
    return None, False, None

def addresses_within_radius(data, center_point, radius_km, geo_cache, language='de'):
    all_coordinates = []
    warning_list = []
    markers = []
    geolocator = Nominatim(user_agent="street_mapper_idm")
    lang = LANG_TEXT.get(language, LANG_TEXT['de'])

    for ticket in data:
        address = ticket.get('Address', '')
        ticket_id = ticket.get('Id', '')
        customer_name = ticket.get('CustomerName', '')
        title = ticket.get('Title', '')
        status = ticket.get('Status', 'offen')

        coords, is_approx, ortsteil = get_coordinates_extended(address, geo_cache, geolocator=geolocator)
        if coords:
            distance = geodesic(center_point, coords).km
            if distance <= radius_km:
                ticket_url = f"https://tickets.netixx.it:10443/Ticket/view/id/{ticket_id}"
                popup_html = f"""
                <div style='font-family:Arial; font-size:14px; min-width:210px;'>
                  <b>Ticket:</b> <a href="{ticket_url}" target="_blank">{ticket_id}</a><br>
                  <b>Kunde:</b> {html.escape(customer_name)}<br>
                  <span style='color:#6a994e; font-weight:bold;'>{html.escape(title)}</span><br>
                  <span style='padding:3px 8px; background:#eee; border-radius:7px; color:#333; font-size:12px;'>{html.escape(status.title())}</span><br>
                """
                if is_approx and ortsteil:
                    popup_html += f"<i style='color:#000;font-size:12px;'>{lang['approx_marker']}: {html.escape(ortsteil)}</i><br>"
                    warning_list.append({
                        'ticket_id': ticket_id,
                        'customer_name': customer_name,
                        'address': address,
                        'type': 'approx'
                    })
                popup_html += "</div>"

                markers.append({
                    'coords': coords,
                    'popup': popup_html,
                    'tooltip': html.escape(customer_name),
                    'color': get_marker_color(ticket, is_approximate=is_approx),
                })
                all_coordinates.append(coords)
            else:
                logging.info(f"Adresse {address} liegt außerhalb des Radius.")
        else:
            warning_list.append({
                'ticket_id': ticket_id,
                'customer_name': customer_name,
                'address': address,
                'type': 'not_found'
            })
    return markers, all_coordinates, warning_list

def save_map_safely(mymap, output_path):
    mymap.save(output_path)
    os.chmod(output_path, 0o644)
    logging.info(f"Karte wurde gespeichert unter: {output_path}")

def generate_map(center_point, markers, all_coordinates, warning_list, language='de'):
    lang = LANG_TEXT.get(language, LANG_TEXT['de'])
    mymap = folium.Map(location=center_point, zoom_start=9, control_scale=True)

    for marker in markers:
        folium.Marker(
            location=marker['coords'],
            popup=folium.Popup(marker['popup'], max_width=320),
            tooltip=marker['tooltip'],
            icon=folium.Icon(color=marker['color'], icon='info-sign')
        ).add_to(mymap)

    logo_html = f"""
    <div style='position: absolute; right:50%; left:50%; top:55px; transform:translate(-50%, -50%); width:5%; z-index: 1000'>
        <img src='{LOGO_URL}' height='150'>
    </div>
    """
    mymap.get_root().html.add_child(folium.Element(logo_html))

    timestamp = datetime.now().strftime('%H:%M:%S %d.%m.%Y')
    timestamp_html = (
        f"<div style='position: absolute; right:50%; left:50%; bottom:0px; transform:translate(-50%, -50%); width:10%; z-index: 1000; "
        f"background:rgba(255,255,255,0.9); padding:5px 12px; border:1px solid #ddd; "
        f"border-radius:7px; font-size:13px;'>"
        f"<b>{lang['generated_at']}: {timestamp}</b></div>"
    )
    mymap.get_root().html.add_child(folium.Element(timestamp_html))

    if warning_list:
        warning_html = (
            f"<div style='position: absolute; top: 20px; right: 20px; z-index: 2000; "
            f"background:rgba(255,255,255,0.97); padding: 12px 18px; border:1px solid #f00; "
            f"border-radius:12px; font-size:13px; min-width:340px;'>"
            f"<div style='margin-bottom: 7px; color:#d9534f;'><b>{lang['warning_head']}</b></div>"
            f"<table style='font-size:12px;'><tr>"
            f"<th>{lang['ticket']}</th><th>{lang['customer']}</th><th>{lang['address']}</th><th>{lang['type']}</th></tr>"
        )
        for w in warning_list:
            type_display = (
                lang['approx_marker'] if w['type'] == 'approx'
                else lang['not_found_marker']
            )
            warning_html += (
                f"<tr><td>{w['ticket_id']}</td>"
                f"<td>{html.escape(w['customer_name'])}</td>"
                f"<td>{html.escape(w['address'])}</td>"
                f"<td>{type_display}</td></tr>"
            )
        warning_html += "</table></div>"
        mymap.get_root().html.add_child(folium.Element(warning_html))

    Fullscreen(
        position="topleft",
        title=lang['fullscreen'],
        title_cancel=lang['fullscreen_exit']
    ).add_to(mymap)

    if all_coordinates:
        if len(all_coordinates) == 1:
            mymap.location = all_coordinates[0]
            mymap.zoom_start = 16
        else:
            mymap.fit_bounds(all_coordinates, padding=(45, 45))

    style = """
    <style>
    @media (max-width: 650px) {
        .leaflet-control { font-size: 13px !important; }
        div[style*='position: absolute;'] { font-size: 12px !important; }
    }
    </style>
    """
    mymap.get_root().header.add_child(folium.Element(style))

    save_map_safely(mymap, OUTPUT_MAP_FILE)

def main():
    parser = argparse.ArgumentParser(description='Ticket Map Generator')
    parser.add_argument('--force-update', action='store_true', help='Generiert die Karte unabhängig von API-Änderungen')
    args = parser.parse_args()

    config = load_config()
    api_token = config.get('api_token')
    center_address = config.get('center_address', 'Bolzano')
    radius_km = config.get('radius_km', 120)
    language = config.get('language', 'de')

    geo_cache = load_json_cache(GEO_CACHE_FILE)
    data = fetch_data_from_api(api_token)
    prev_data = load_json_cache(CACHE_FILE)

    if (data == prev_data) and not args.force_update:
        logging.info("API-Antwort entspricht der vorherigen. Kartengenerierung übersprungen.")
        return

    save_json_cache(CACHE_FILE, data)
    center_point, _, _ = get_coordinates_extended(center_address, geo_cache)
    if not center_point:
        logging.error("Zentrum konnte nicht geocodiert werden. Abbruch.")
        return

    markers, all_coords, warnings = addresses_within_radius(data, center_point, radius_km, geo_cache, language=language)
    save_json_cache(GEO_CACHE_FILE, geo_cache)

    generate_map(center_point, markers, all_coords, warnings, language)

if __name__ == "__main__":
    main()