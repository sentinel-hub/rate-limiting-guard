import redis
import logging
import math
import os
import sched
import sys
import time

import jwt
import requests

import kazoo.client
from kazoo.client import KazooClient
from rate_limiting_guard.lib import PolicyType, Repository, RedisRepository, ZooKeeperRepository


POLICY_TYPES_SHORT_NAMES = {
    "PROCESSING_UNITS": PolicyType.PROCESSING_UNITS,
    "REQUESTS": PolicyType.REQUESTS,
}
POLICY_TYPES_FULL_NAMES = {
    PolicyType.PROCESSING_UNITS.value: "PROCESSING_UNITS",
    PolicyType.REQUESTS.value: "REQUESTS",
}

min_revisit_time_ms = None

SENTINELHUB_ROOT_URL = os.environ.get("SENTINELHUB_ROOT_URL", "https://services.sentinel-hub.com")

CLIENT_ID = os.environ.get("CLIENT_ID")
CLIENT_SECRET = os.environ.get("CLIENT_SECRET")
# Docker-compose doesn't strip double quotes when reading from .env; however running this file from
# command line decodes the secret incorrectly if the quotes are absent. To avoid having two different
# ways of writing .env files, we remove the quotes here if present:
if CLIENT_SECRET.startswith('"') and CLIENT_SECRET.endswith('"'):
    CLIENT_SECRET = CLIENT_SECRET[1:-1]
if not CLIENT_ID or not CLIENT_SECRET:
    raise Exception("Please supply CLIENT_ID and CLIENT_SECRET env vars!")


logging.basicConfig(level=os.environ.get("LOGLEVEL", "INFO").upper())
kazoo.client.log.setLevel(logging.WARNING)


def request_auth_token(client_id, client_secret):
    r = requests.post(
        f"{SENTINELHUB_ROOT_URL}/oauth/token",
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


def will_auth_token_soon_expire(auth_token, exp_margin_s=300):
    return extract_expiration_time(auth_token) <= time.time() + exp_margin_s


def extract_expiration_time(auth_token):
    data = jwt.decode(auth_token, options={"verify_signature": False})
    return data["exp"]


def fetch_current_stats(auth_token, user_id):
    r = requests.get(
        f"{SENTINELHUB_ROOT_URL}/aux/ratelimit/statistics/tokenCounts/{user_id}",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    r.raise_for_status()
    stats = r.json()["data"]
    return stats


def fetch_rate_limits(user_id, auth_token):
    r = requests.get(
        f"{SENTINELHUB_ROOT_URL}/aux/ratelimit/contract",
        params={
            "userId": f"eq:{user_id}",
        },
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    r.raise_for_status()
    contracts = r.json()["data"]

    stats = fetch_current_stats(auth_token, user_id)

    rate_limits = []
    for contract in contracts:
        for policy in contract["policies"]:
            policy_type_long = contract["type"]["name"]
            policy_type = POLICY_TYPES_SHORT_NAMES[policy_type_long].value
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
                    "nanos_between_refills": int(policy["nanosBetweenRefills"]),
                    "sampling_period": policy["samplingPeriod"],
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


def repository_fill_bucket(field, incr_by, limit, min_revisit_time_ms, repository: Repository):
    """
    Fills the rate-limiting bucket.
    """
    new_value = repository.increment_counter(field, float(incr_by))

    # Since we can't atomically check and increment conditionally, we increment, then
    # check the new value, and decrement back if over the limit.
    if int(new_value) > limit:
        decr_by = int(new_value) - limit
        final_value = repository.increment_counter(field, -float(decr_by))
        logging.debug(f"Filled {field} to {final_value} (limit {limit} reached)")
    else:
        logging.debug(f"Filled {field} to {new_value} (limit {limit})")

    repository.signal_syncer_alive(min_revisit_time_ms)


def run_syncing(rate_limits, min_revisit_time_ms, repository: Repository, refresh_buckets_sec=None, auth_token=None):
    """
    Runs a scheduler which fills the rate limiting buckets in Redis.

    We are using the stock Python `sched` package for running the filling tasks.

    We are well aware that in theory the way we are dealing with time is not the most precise
    way. However the difference should be negligable and should not matter, because the process
    fixes itself in time if we have either too big or too small value in a bucket.
    """
    scheduler = sched.scheduler(time.time, time.sleep)
    PRIORITY = 1
    PRIORITY_REFRESH_BUCKETS = 2

    def fill_bucket(policy_id, fill_interval_s, fill_quantity, capacity, scheduled_at):
        now = time.time()
        logging.debug(
            f"Filling: {policy_id} every {fill_interval_s}s with {fill_quantity}. Was scheduled at {scheduled_at:.3f}, {now - scheduled_at:.3f}s late."
        )
        repository_fill_bucket(policy_id, fill_quantity, capacity, min_revisit_time_ms, repository)

        # schedule next run, adjusting the time so that delay in running doesn't affect the sequence (much)
        adjusted_interval_s = max(scheduled_at + fill_interval_s - now, 0.001)
        arguments = (
            policy_id,
            fill_interval_s,
            fill_quantity,
            capacity,
            scheduled_at + fill_interval_s,
        )
        scheduler.enter(adjusted_interval_s, PRIORITY, fill_bucket, argument=arguments)

    def refresh_buckets(rate_limits, auth_token):
        try:
            if auth_token is None or will_auth_token_soon_expire(auth_token):
                auth_token = request_auth_token(CLIENT_ID, CLIENT_SECRET)
                exp_time_s = extract_expiration_time(auth_token)
                repository.save_access_token(auth_token, exp_time_s)

            user_id = extract_user_id(auth_token)
            stats = fetch_current_stats(auth_token, user_id)
        except Exception as ex:
            logging.warning(f"Refreshing buckets failed! {str(ex)}")

        bucket_values = repository.get_buckets_state()

        for policy in rate_limits:
            bucket_value = float(bucket_values[policy["id"]])
            actual_value = stats[POLICY_TYPES_FULL_NAMES[policy["type"]]][policy["sampling_period"]]
            incr_by = actual_value - bucket_value
            repository_fill_bucket(policy["id"], incr_by, policy["capacity"], min_revisit_time_ms, repository)
            logging.debug(
                f"Refreshed policy type {POLICY_TYPES_FULL_NAMES[policy['type']]} {policy['sampling_period']}. Bucket value: {bucket_value}. Actual value: {actual_value}"
            )
        scheduler.enter(
            refresh_buckets_sec, PRIORITY_REFRESH_BUCKETS, refresh_buckets, argument=(rate_limits, auth_token)
        )

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
        scheduler.enter(fill_interval_s, PRIORITY, fill_bucket, argument=arguments)

    if refresh_buckets_sec is not None:
        # Schedule refreshing buckets with values from sentinel hub
        arguments = (rate_limits, auth_token)
        scheduler.enter(refresh_buckets_sec, PRIORITY_REFRESH_BUCKETS, refresh_buckets, argument=arguments)
        logging.info(f"Refreshing buckets every {refresh_buckets_sec} seconds.")

    scheduler.run()


def start_syncer(argv):
    if len(argv) > 1 and argv[1] == "zookeeper":
        ZOOKEEPER_HOSTS = os.environ.get("ZOOKEEPER_HOSTS", "127.0.0.1:2181")
        zk = KazooClient(hosts=ZOOKEEPER_HOSTS)
        zk.start()

        repository = ZooKeeperRepository(zk, key_base="/openeo/rlguard")
    else:
        REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
        REDIS_PORT = int(os.environ.get("REDIS_PORT", 6379))
        rds = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

        repository = RedisRepository(rds)

    REFRESH_BUCKETS_SEC = os.environ.get("REFRESH_BUCKETS_SEC")
    if REFRESH_BUCKETS_SEC:
        REFRESH_BUCKETS_SEC = int(REFRESH_BUCKETS_SEC)
    else:
        REFRESH_BUCKETS_SEC = None

    REVISIT_TIME_MSEC = os.environ.get("REVISIT_TIME_MSEC")
    if REVISIT_TIME_MSEC:
        REVISIT_TIME_MSEC = int(REVISIT_TIME_MSEC)
    else:
        REVISIT_TIME_MSEC = None

    while True:
        try:
            auth_token = request_auth_token(CLIENT_ID, CLIENT_SECRET)
        except Exception as ex:
            logging.warning(f"Could not fetch auth token, will retry in 5s. Error: {str(ex)}")
            time.sleep(5)
            continue

        user_id = extract_user_id(auth_token)
        rate_limits = fetch_rate_limits(user_id, auth_token)
        exp_time_s = extract_expiration_time(auth_token)

        # we need a way for workers to know if we died - we do this by setting EXPIRE on `syncer_alive`
        # key to twice the time we should refill the buckets in:
        min_revisit_time_ms = REVISIT_TIME_MSEC or int(1000 * min([r["fill_interval_s"] for r in rate_limits])) * 2

        repository.init_rate_limits(rate_limits, min_revisit_time_ms)
        repository.save_access_token(auth_token, exp_time_s)

        run_syncing(
            rate_limits, min_revisit_time_ms, repository, refresh_buckets_sec=REFRESH_BUCKETS_SEC, auth_token=auth_token
        )

        logging.info("Restarting...")


if __name__ == "__main__":
    start_syncer(sys.argv)
