import logging
import json
import math
import os
import sched
import time

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


logging.basicConfig(level=os.environ.get("LOGLEVEL", "INFO").upper())


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
            fill_interval_s, fill_quantity = adjust_filling(int(policy["nanosBetweenRefills"]))
            logging.info(
                f"Found rate limiting policy: {policy_type_long}, remaining {remaining}, capacity {policy['capacity']}, nanosBetweenRefills {policy['nanosBetweenRefills']}"
            )
            rate_limits.append(
                {
                    "id": policy_id,
                    "type": policy_type,
                    "capacity": policy["capacity"],
                    "initial": remaining,
                    "fill_interval_s": fill_interval_s,
                    "fill_quantity": fill_quantity,
                }
            )
    return rate_limits


def adjust_filling(nanos_between_refills):
    """
    We know that we don't have a chance to run tasks with ns precision, so we adjust the
    filling interval to 100ms or more (and increment value accordingly).
    """
    MIN_INTERVAL_NS = 100 * 1000 * 1000  # 100 ms sounds manageable
    if nanos_between_refills >= MIN_INTERVAL_NS:
        fill_interval_s, fill_quantity = nanos_between_refills / 1000000000.0, 1
        return fill_interval_s, fill_quantity
    # we need to fix fill_quantity so that we can return big enough fill time:
    n_at_once = math.ceil(MIN_INTERVAL_NS / nanos_between_refills)
    fill_interval_s = (nanos_between_refills * n_at_once) / 1000000000.0
    return fill_interval_s, n_at_once


def redis_init_rate_limits(rate_limits):
    with r.pipeline() as pipe:
        for policy in rate_limits:
            pipe.hset(REDIS_POLICIES_KEY, policy["id"], policy["initial"])
        pipe.execute()


def redis_fill_bucket(field, incr_by, limit):
    """
    Since we can't atomically check and increment conditionally, we increment, then
    check the new value, and decrement back if over the limit.
    """
    new_value = r.hincrbyfloat(REDIS_POLICIES_KEY, field, incr_by)
    if int(new_value) > limit:
        decr_by = int(new_value) - limit
        final_value = r.hincrbyfloat(REDIS_POLICIES_KEY, field, -decr_by)
        logging.debug(f"Filled {field} to {final_value} (limit {limit} reached)")
    else:
        logging.debug(f"Filled {field} to {new_value} (limit {limit})")


def run_syncing(rate_limits):
    """
    Runs a scheduler which fills the rate limiting buckets in Redis.

    We are using the stock Python `sched` package for running the filling tasks.

    We are well aware that in theory the way we are dealing with time is not the most precise
    way. However the difference should be negligable and should not matter, because the process
    fixes itself in time if we have either too big or too small value in a bucket.
    """
    s = sched.scheduler(time.time, time.sleep)
    PRIORITY = 1

    def fill_bucket(policy_id, fill_interval_s, fill_quantity, capacity, scheduled_at):
        now = time.time()
        logging.debug(
            f"Filling: {policy_id} every {fill_interval_s}s with {fill_quantity}. Was scheduled at {scheduled_at:.3f}, {now - scheduled_at:.3f}s late."
        )
        redis_fill_bucket(policy_id, fill_quantity, capacity)

        # schedule next run, adjusting the time so that delay in running doesn't affect the sequence (much)
        adjusted_interval_s = max(scheduled_at + fill_interval_s - now, 0.001)
        arguments = (
            policy_id,
            fill_interval_s,
            fill_quantity,
            capacity,
            scheduled_at + fill_interval_s,
        )
        s.enter(adjusted_interval_s, PRIORITY, fill_bucket, argument=arguments)

    # initialize the scheduler:
    now = time.time()
    for policy in rate_limits:
        policy_id = policy["id"]
        fill_interval_s = policy["fill_interval_s"]
        fill_quantity = policy["fill_quantity"]
        capacity = policy["capacity"]
        logging.info(f"Rate limiting policy {policy_id}: {fill_quantity} every {fill_interval_s}s, up until {capacity}")
        scheduled_at = now + fill_interval_s
        arguments = (
            policy_id,
            fill_interval_s,
            fill_quantity,
            capacity,
            scheduled_at,
        )
        s.enter(fill_interval_s, PRIORITY, fill_bucket, argument=arguments)
    s.run()


def main():
    CLIENT_ID = os.environ.get("CLIENT_ID")
    CLIENT_SECRET = os.environ.get("CLIENT_SECRET")
    if not CLIENT_ID or not CLIENT_SECRET:
        raise Exception("Please supply CLIENT_ID and CLIENT_SECRET env vars!")

    auth_token = request_auth_token(CLIENT_ID, CLIENT_SECRET)
    user_id = extract_user_id(auth_token)
    rate_limits = fetch_rate_limits(user_id, auth_token)

    redis_init_rate_limits(rate_limits)
    run_syncing(rate_limits)


if __name__ == "__main__":
    main()
