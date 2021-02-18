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

        # syncer service should re-read its policies:
        subprocess.call(["docker", "kill", "--signal=SIGTERM", SYNCER_CONTAINER_NAME])

    return wrapped


# @pytest.mark.parametrize("use_syncer", [
#     (True,),
#     (False,),
# ])
def test_ratelimiting(set_policies, capsys):
    req = requests.Session()

    set_policies(
        [
            ("RQ", 20, 10),
            ("PU", 40, 10),
        ]
    )
    url = f"{MOCKSH_ROOT_URL}/data"
    processing_units = 2
    max_retries = 5
    total_requests = 100

    use_syncer = True

    start_time = time.monotonic()
    count_success = 0
    count_429 = 0
    with capsys.disabled():  # print out output
        for i in range(total_requests):
            for t in range(max_retries):
                if use_syncer:
                    delay = apply_for_request(processing_units)
                    if delay > 0:
                        print(f"Sleeping for {delay}s (rlguard)")
                        time.sleep(delay)

                # trigger mock service to update its buckets:
                r2 = req.post(f"{MOCKSH_ROOT_URL}/refill_buckets")
                r2.raise_for_status()

                r = req.get(url, params={"processing_units": processing_units})
                if r.status_code == 200:
                    count_success += 1
                    break
                if r.status_code == 429:
                    count_429 += 1
                    if use_syncer:
                        delay = 0.5
                        print(f"Sleeping for {delay}s (got 429 with rlguard)")
                    else:
                        delay = 2 ** t  # exponentional backoff
                        print(f"Sleeping for {delay}s (exp. backoff)")
                    time.sleep(delay)
                    continue
                r.raise_for_status()
            else:
                raise Exception("A request has failed! This should never happen!!!")

        total_time = time.monotonic() - start_time

        print(f"{'*' * 30}\nTotal_time: {total_time}")
        print(f"Number of 429 responses: {count_429}")
        print(f"Successfully completed: {count_success} / {total_requests}")
    assert True
