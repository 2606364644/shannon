from flask import Flask, request, jsonify

app = Flask(__name__)


@app.route("/api/users", methods=["GET"])
def list_users():
    users = get_users()
    return jsonify(users)


@app.route("/api/users/<int:user_id>", methods=["POST"])
def update_user(user_id):
    data = request.get_json()
    result = save_user(user_id, data)
    return jsonify(result)


@shared_task
def process_queue():
    items = fetch_items()
    for item in items:
        process_item(item)


def get_users():
    return db.query("SELECT * FROM users")


def save_user(user_id, data):
    return db.update("users", user_id, data)


def fetch_items():
    return []


def process_item(item):
    pass
