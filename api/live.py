from flask import Blueprint, request, jsonify
from datetime import datetime

incident_api = Blueprint('incident_api', __name__, url_prefix='/api')

# In-memory storage for incidents
incidents = []
incident_id_counter = 1

# Try to import geopy for geocoding, gracefully handle if not installed
try:
    from geopy.geocoders import Nominatim
    from geopy.exc import GeocoderTimedOut, GeocoderServiceError
    GEOCODER_AVAILABLE = True
    geolocator = Nominatim(user_agent="sd_auto_backend")
except ImportError:
    GEOCODER_AVAILABLE = False
    geolocator = None


def geocode_location(location_text):
    """
    Geocode a location string to latitude/longitude coordinates.
    
    Args:
        location_text: String description of the location
        
    Returns:
        tuple: (latitude, longitude) or (None, None) if geocoding fails
    """
    if not GEOCODER_AVAILABLE or not geolocator:
        return None, None
    
    try:
        location = geolocator.geocode(location_text, timeout=5)
        if location:
            return location.latitude, location.longitude
    except (GeocoderTimedOut, GeocoderServiceError) as e:
        print(f"Geocoding error for '{location_text}': {e}")
    except Exception as e:
        print(f"Unexpected geocoding error: {e}")
    
    return None, None


@incident_api.route('/incidents', methods=['POST'])
def report_incident():
    """
    Report a new incident.
    
    Request body:
        {
            "type": "accident|construction|hazard|closure|other",
            "location": "Location description",
            "details": "Optional additional details"
        }
    
    Response:
        {
            "message": "Incident reported successfully",
            "incident": { ...incident data with coordinates... }
        }
    """
    global incident_id_counter
    
    data = request.get_json()
    if not data.get('location') or not data.get('type'):
        return jsonify({'error': 'Location and type are required'}), 400

    location_text = data['location']
    incident_type = data['type']
    details = data.get('details', '')
    
    # Geocode the location server-side
    latitude, longitude = geocode_location(location_text)
    
    # Create the incident
    incident = {
        'id': incident_id_counter,
        'type': incident_type,
        'location': location_text,
        'details': details,
        'latitude': latitude,
        'longitude': longitude,
        'created_at': datetime.utcnow().isoformat()
    }
    
    incidents.append(incident)
    incident_id_counter += 1
    
    return jsonify({
        'message': 'Incident reported successfully',
        'incident': incident
    }), 201


@incident_api.route('/incidents', methods=['GET'])
def get_incidents():
    """
    Get all reported incidents.
    
    Response:
        [
            {
                "id": 1,
                "type": "accident",
                "location": "123 Main St",
                "details": "Minor fender bender",
                "latitude": 32.7157,
                "longitude": -117.1611,
                "created_at": "2025-12-30T10:00:00"
            },
            ...
        ]
    """
    return jsonify(incidents)


@incident_api.route('/incidents/<int:incident_id>', methods=['DELETE'])
def delete_incident(incident_id):
    """
    Delete an incident by ID.
    
    Response:
        { "message": "Incident deleted successfully" }
    """
    global incidents
    
    for i, incident in enumerate(incidents):
        if incident['id'] == incident_id:
            incidents.pop(i)
            return jsonify({'message': 'Incident deleted successfully'})
    
    return jsonify({'error': 'Incident not found'}), 404


