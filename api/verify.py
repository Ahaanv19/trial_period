from flask import Blueprint, request, jsonify
from flask_restful import Api, Resource

verify_api = Blueprint('verify', __name__, url_prefix='/api')
api = Api(verify_api)

entries = []

class EntryResource(Resource):
    def post(self):
        data = request.get_json()
        name = data.get("name")
        email = data.get("email")
        address = data.get("address")

        if not all([name, email, address]):
            return {"error": "Missing fields"}, 400

        entries.append({"name": name, "email": email, "address": address})
        return {"message": "Entry added"}, 200

    def get(self):
        return jsonify(entries)  # Public access

api.add_resource(EntryResource, '/entries')



























