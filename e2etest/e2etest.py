import os
import subprocess
import sys
import time

import pytest
import requests


currentdir = os.path.dirname(os.path.realpath(__file__))
parentdir = os.path.dirname(currentdir)
sys.path.append(parentdir)
from lib.rlguard import apply_for_request


MOCKSH_ROOT_URL = "http://127.0.0.1:8000"
SYNCER_CONTAINER_NAME = "e2etest_syncer"


@pytest.fixture
def set_policies():
    def wrapped(policies):
        # tell mocksh service to use these policies from now on:
        data = {"PU": [], "RQ": []}
        for policy_type, capacity, refill_time in policies:
            nanos = (float(refill_time) / capacity) * 10 ** 9
            data[policy_type].append(
                {
                    "capacity": capacity,
                    "samplingPeriod": f"mock{policy_type}_{capacity}_{refill_time}",
                    "nanosBetweenRefills": nanos,
                }
            )

        for policy_type in data:
            r = requests.put(f"{MOCKSH_ROOT_URL}/policies/{policy_type}", json=data[policy_type])
            r.raise_for_status()

        # syncer service should re-read its policies, the easiest way to force it to do so is to simply restart it:
        subprocess.call(["docker", "restart", "-t", "1", SYNCER_CONTAINER_NAME])
        time.sleep(2)

    return wrapped


def calculate_ideal_time(policies, total_requests, pu_per_request):
    ideal_time = 0
    for policy_type, capacity, refill_time in policies:
        if capacity >= total_requests:
            continue
        refill_rate = capacity / refill_time
        ideal_time = max(ideal_time, (total_requests - capacity) / refill_rate)
    return ideal_time


@pytest.mark.parametrize(
    "policies,total_requests,use_rlguard",
    [
        (
            [("RQ", 1000, 100), ("PU", 1000, 100)],
            100,
            False,
        ),
        (
            [("RQ", 1000, 100), ("PU", 1000, 100)],
            100,
            True,
        ),
        (
            [("RQ", 50, 10), ("PU", 200, 10)],
            100,
            False,
        ),
        (
            [("RQ", 50, 10), ("PU", 200, 10)],
            100,
            True,
        ),
    ],
)
def test_ratelimiting(set_policies, capsys, policies, total_requests, use_rlguard):
    req = requests.Session()

    set_policies(policies)
    url = f"{MOCKSH_ROOT_URL}/data"
    pu_per_request = 2
    max_retries = 5

    start_time = time.monotonic()
    count_success = 0
    count_429 = 0
    with capsys.disabled():  # print out output
        print(f"\n{'=' * 35}\nExpected test time: > {calculate_ideal_time(policies, total_requests, pu_per_request)}s")
        for i in range(total_requests):
            for t in range(max_retries):
                if use_rlguard:
                    delay = apply_for_request(pu_per_request)
                    if delay > 0:
                        # print(f"Sleeping for {delay}s (rlguard)")
                        time.sleep(delay)

                # trigger mock service to update its buckets:
                r2 = req.post(f"{MOCKSH_ROOT_URL}/refill_buckets")
                r2.raise_for_status()

                r = req.get(url, params={"processing_units": pu_per_request})
                if r.status_code == 200:
                    count_success += 1
                    break
                if r.status_code == 429:
                    count_429 += 1
                    if use_rlguard:
                        delay = 0.5
                        # print(f"Sleeping for {delay}s (got 429 with rlguard)")
                    else:
                        delay = 2 ** t  # exponentional backoff
                        # print(f"Sleeping for {delay}s (exp. backoff)")
                    time.sleep(delay)
                    continue
                r.raise_for_status()
            else:
                raise Exception("Request has failed! This should never happen!!!")

        total_time = time.monotonic() - start_time

        ideal_time = calculate_ideal_time(policies, total_requests, pu_per_request)

        print(f"Total_time: {total_time}s")
        print(f"Number of 429 responses: {count_429}")
        print(f"Successfully completed: {count_success} / {total_requests}")
        print(f"{'=' * 35}\n")
    assert True
