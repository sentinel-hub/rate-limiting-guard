import logging
import os
import random
import time

import requests
import requests.exceptions

from rlguard import calculate_processing_units, OutputFormat, apply_for_request, SyncerDownException


logging.basicConfig(level=os.environ.get("LOGLEVEL", "INFO").upper())


def request_auth_token(client_id, client_secret):
    """
    Given the CLIENT_ID and CLIENT_SECRET, request a temporary authentication token which can later be used to
    request satellite imagery.

    See https://docs.sentinel-hub.com/api/latest/api/overview/authentication/ for details.
    """
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


def get_map(auth_token, output_filename=None):
    """
    This example shows how to download satellite imagery from Sentinel Hub using the Rate Limiting Guard
    for protection against rate limiting responses (429).
    """
    # calculate PU based on request parameters:
    pu = calculate_processing_units(False, 1024, 1024, 3, OutputFormat.OTHER, 1, False)

    # now apply for permission to make a request:
    try:
        delay = apply_for_request(pu)
    except SyncerDownException:
        delay = 0
        # If this happens, retries should be handled manually, with exponential backoff (but limited to the
        # time it takes to fill the offending bucket from empty to full).

    # if `apply_for_request` tells us to wait for some time before issuing the request, we should sleep a bit:
    if delay > 0.0:
        logging.info(f"Rate limited, sleeping for {delay}s...")
        time.sleep(delay)

    # note that sentinelhub-py could also be used here instead of performing the request directly:
    logging.info("Performing request...")
    r = requests.post(
        "https://services.sentinel-hub.com/api/v1/process",
        headers={
            "Authorization": f"Bearer {auth_token}",
        },
        json={
            "input": {
                "bounds": {
                    "properties": {"crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"},
                    "bbox": [13.822174072265625, 45.85080395917834, 14.55963134765625, 46.29191774991382],
                },
                "data": [
                    {
                        "type": "S2L1C",
                        "dataFilter": {"timeRange": {"from": "2018-10-01T00:00:00Z", "to": "2018-12-31T00:00:00Z"}},
                    }
                ],
            },
            "output": {
                "width": 1024,
                "height": 1024,
            },
            "evalscript": """
                //VERSION=3
                function setup() {
                  return {
                    input: ["B02", "B03", "B04"],
                    output: {
                      bands: 3,
                      sampleType: "AUTO" // default value - scales the output values from [0,1] to [0,255].
                     }
                  }
                }
                function evaluatePixel(sample) {
                  return [2.5 * sample.B04, 2.5 * sample.B03, 2.5 * sample.B02]
                }
            """,
        },
    )
    r.raise_for_status()

    if output_filename:
        with open(output_filename, "wb") as fh:
            fh.write(r.content)
    logging.info("...request completed.")


def main():
    CLIENT_ID = os.environ.get("CLIENT_ID")
    CLIENT_SECRET = os.environ.get("CLIENT_SECRET")
    if not CLIENT_ID or not CLIENT_SECRET:
        raise Exception("Please supply CLIENT_ID and CLIENT_SECRET env vars!")
    auth_token = request_auth_token(CLIENT_ID, CLIENT_SECRET)

    # if the request fails for any reason, we should retry it a few times:
    N_TRIES = 5
    for iteration in range(N_TRIES):
        try:
            get_map(auth_token, "output.png")
            break  # request succeeded, no need to retry it
        except requests.exceptions.HTTPError:
            # there was an error - perform exponential backoff
            # we apply some jitter to avoid all workers hitting the same time again:
            jitter = 0.75 + (0.5 * random.random())  # 25% jitter - 0.75 - 1.25
            retry_timeout = (2 ** iteration) * jitter
            if iteration < N_TRIES - 1:
                logging.warning(
                    f"There was an error fetching data (iteration {iteration}), retrying in {retry_timeout} seconds..."
                )
                time.sleep(retry_timeout)
            else:
                logging.error(f"Request failed {N_TRIES} times, failing... sorry.")
                raise


if __name__ == "__main__":
    main()
