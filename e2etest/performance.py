import concurrent.futures
import os
import random
import subprocess
import sys
import time

import pytest
import requests


currentdir = os.path.dirname(os.path.realpath(__file__))
parentdir = os.path.dirname(currentdir)
sys.path.append(parentdir)
from lib.rlguard import apply_for_request, SyncerDownException


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

    return wrapped


def restart_syncer():
    # syncer service should re-read its policies, the easiest way to force it to do so is to simply restart it:
    subprocess.call(["docker", "restart", "-t", "1", SYNCER_CONTAINER_NAME])
    time.sleep(2)


def stop_syncer():
    subprocess.call(["docker", "stop", SYNCER_CONTAINER_NAME])


def calculate_ideal_time(policies, total_requests, pu_per_request):
    ideal_time = 0
    for policy_type, capacity, refill_time in policies:
        if capacity >= total_requests:
            continue
        refill_rate = capacity / refill_time
        ideal_time = max(ideal_time, (total_requests - capacity) / refill_rate)
    return ideal_time


def worker_func(
    n_requests, use_rlguard, pu_per_request, max_delay, use_jitter=False, use_startup_delay=False, print_debug=False
):
    req = requests.Session()
    url = f"{MOCKSH_ROOT_URL}/data"
    max_retries = 100  # this number must be big, otherwise there will be failures with some (non-optimal) settings

    # add (per-worker) random startup delay:
    if use_startup_delay:
        time.sleep(random.random())  # up to 1s

    count_success = 0
    count_429 = 0
    syncer_down = False
    for i in range(n_requests):
        for t in range(max_retries):
            if use_rlguard:
                try:
                    delay = apply_for_request(pu_per_request)
                except SyncerDownException:
                    if not syncer_down:
                        print("--- syncer is down! ---")
                    syncer_down = True
                    delay = 0
                if delay > 0:
                    time.sleep(delay)

            # trigger mock service to update its buckets:
            try:
                r2 = req.post(f"{MOCKSH_ROOT_URL}/refill_buckets")
                r2.raise_for_status()
            except Exception as ex:
                print(f"--- exception while doing a refill request: {str(ex)} - ignoring ---")
                pass

            try:
                r = req.get(url, params={"processing_units": pu_per_request})
            except Exception as ex:
                print(f"--- exception while doing a request: {str(ex)} - retrying ---")
                continue
            if r.status_code == 200:
                count_success += 1
                break
            if r.status_code == 429:
                count_429 += 1
                if use_rlguard and not syncer_down:
                    # we got 429 even though syncer told us it would be ok - it happens, but let's
                    # wait just a bit before asking it again:
                    delay = 0.5
                else:
                    delay = 2 ** t  # exponentional backoff
                    if use_jitter:
                        jitter = random.random() - 0.5  # -0.5 - 0.5
                        delay += jitter
                delay = min(delay, max_delay)
                if print_debug:
                    print(f"Sleeping for {delay}s (try: {t + 1})")
                time.sleep(delay)
                continue
            r.raise_for_status()
        else:
            raise Exception("Request has failed! This should never happen!!!")
    return count_success, count_429


@pytest.mark.parametrize(
    "policies,requests_per_worker,n_workers,use_rlguard,simulate_dead_syncer",
    [
        (
            [("RQ", 1, 1), ("PU", 2, 1)],
            1,
            1,
            True,
            False,
        ),
        (
            [("RQ", 1000, 100), ("PU", 1000, 100)],
            100,
            1,
            False,
            False,
        ),
        (
            [("RQ", 1000, 100), ("PU", 1000, 100)],
            100,
            1,
            True,
            False,
        ),
        (
            [("RQ", 50, 10), ("PU", 200, 10)],
            100,
            1,
            False,
            False,
        ),
        (
            [("RQ", 10, 5), ("PU", 200, 10)],
            20,
            1,
            True,
            False,
        ),
    ],
)
def test_ratelimiting(
    set_policies, capsys, policies, requests_per_worker, n_workers, use_rlguard, simulate_dead_syncer
):
    set_policies(policies)
    if simulate_dead_syncer:
        stop_syncer()
    else:
        restart_syncer()

    pu_per_request = 2
    total_requests = requests_per_worker * n_workers
    max_delay = min([p[2] for p in policies])

    # additional test settings:
    use_jitter = False
    use_startup_delay = False
    print_debug = False

    start_time = time.monotonic()
    with capsys.disabled():  # print out output
        print(f"\n{'=' * 35}")
        policies_nice = ", ".join([f"{x[1]} {x[0]} / {x[2]} s" for x in policies])
        print(
            f"Test: use rlguard [{use_rlguard}], total requests [{total_requests}], workers [{n_workers}], policies [{policies_nice}]"
        )
        print(f"Expected test time: > {calculate_ideal_time(policies, total_requests, pu_per_request):.1f}s")

        with concurrent.futures.ThreadPoolExecutor(max_workers=n_workers) as executor:
            future_stats = [
                executor.submit(
                    worker_func,
                    requests_per_worker,
                    use_rlguard,
                    pu_per_request,
                    max_delay,
                    use_jitter,
                    use_startup_delay,
                    print_debug,
                )
                for _ in range(n_workers)
            ]
            concurrent.futures.wait(future_stats)

        total_time = time.monotonic() - start_time
        stats = [f.result() for f in future_stats]
        count_success = sum([s[0] for s in stats])
        count_429 = sum([s[1] for s in stats])
        print(f"Total_time: {total_time:.1f}s")
        print(f"Number of 429 responses: {count_429}")
        print(f"Successfully completed: {count_success} / {total_requests}")
        print(f"{'=' * 35}\n")
    assert True
