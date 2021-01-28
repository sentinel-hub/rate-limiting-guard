import json
import os

import jwt
import requests


def request_auth_token(client_id, client_secret):
    r = requests.post(
        "https://services.sentinel-hub.com/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )
    r.raise_for_status()
    j = r.json()
    return j["access_token"]


def extract_user_id(auth_token):
    data = jwt.decode(auth_token, options={"verify_signature": False})
    return data["sub"]


def fetch_rate_limits(user_id, auth_token):
    r = requests.get(
        "https://services.sentinel-hub.com/aux/ratelimit/contract",
        params={
            "userId": f"eq:{user_id}",
        },
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    r.raise_for_status()
    j = r.json()
    rate_limits = []
    for part in j["data"]:
        for policy in part["policies"]:
            rate_limits.append(
                {
                    "type": part["type"]["name"],
                    "capacity": policy["capacity"],
                    "samplingPeriod": policy["samplingPeriod"],
                    "nanosBetweenRefills": policy["nanosBetweenRefills"],
                }
            )
    print(json.dumps(rate_limits))
    return rate_limits


if __name__ == "__main__":
    CLIENT_ID = os.environ.get("CLIENT_ID")
    CLIENT_SECRET = os.environ.get("CLIENT_SECRET")
    if not CLIENT_ID or not CLIENT_SECRET:
        raise Exception("Please supply CLIENT_ID and CLIENT_SECRET env vars!")

    auth_token = request_auth_token(CLIENT_ID, CLIENT_SECRET)
    user_id = extract_user_id(auth_token)
    rate_limits = fetch_rate_limits(user_id, auth_token)
