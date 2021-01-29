import json
import os

import jwt
import redis
import requests


POLICY_TYPES_SHORT_NAMES = {
    "PROCESSING_UNITS": "PU",
    "REQUESTS": "RQ",
}
REDIS_POLICIES_KEY = b"rl"


REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("REDIS_PORT", 6379))
r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT)


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
    contracts = r.json()["data"]

    r = requests.get(
        f"https://services.sentinel-hub.com/aux/ratelimit/statistics/tokenCounts/{user_id}",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    r.raise_for_status()
    stats = r.json()["data"]

    rate_limits = []
    for contract in contracts:
        for policy in contract["policies"]:
            policy_type_long = contract["type"]["name"]
            policy_type = POLICY_TYPES_SHORT_NAMES[policy_type_long]
            policy_id = f'{policy_type}_{policy["capacity"]}_{policy["samplingPeriod"]}'
            remaining = stats[policy_type_long][policy["samplingPeriod"]]
            rate_limits.append(
                {
                    "id": policy_id,
                    "type": policy_type,
                    "capacity": policy["capacity"],
                    "initial": remaining,
                    "nanosBetweenRefills": policy["nanosBetweenRefills"],
                }
            )
    return rate_limits


def redis_init_rate_limits(rate_limits):
    with r.pipeline() as pipe:
        for policy in rate_limits:
            pipe.hset(REDIS_POLICIES_KEY, policy["id"], policy["initial"])
        pipe.execute()


if __name__ == "__main__":
    CLIENT_ID = os.environ.get("CLIENT_ID")
    CLIENT_SECRET = os.environ.get("CLIENT_SECRET")
    if not CLIENT_ID or not CLIENT_SECRET:
        raise Exception("Please supply CLIENT_ID and CLIENT_SECRET env vars!")

    auth_token = request_auth_token(CLIENT_ID, CLIENT_SECRET)
    user_id = extract_user_id(auth_token)
    rate_limits = fetch_rate_limits(user_id, auth_token)
    print(json.dumps(rate_limits))

    redis_init_rate_limits(rate_limits)
