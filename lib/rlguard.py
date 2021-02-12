from enum import Enum
import logging
import os
from typing import Optional

import redis


REDIS_REMAINING_KEY = b"remaining"
REDIS_REFILLS_KEY = b"refill_ns"
REDIS_TYPES_KEY = b"types"


REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("REDIS_PORT", 6379))
rds = redis.Redis(host=REDIS_HOST, port=REDIS_PORT)


class PolicyType(Enum):
    PROCESSING_UNITS = "PU"
    REQUESTS = "RQ"


class OutputFormat(Enum):
    IMAGE_TIFF_DEPTH_32 = "tiff32"
    APPLICATION_OCTET_STREAM = "octet"
    OTHER = None


def calculate_processing_units(
    batch_processing: bool,
    width: int,
    height: int,
    n_input_bands_without_datamask: int,
    output_format: OutputFormat,
    n_data_samples: int = 1,
    s1_orthorectification: bool = False,
) -> float:
    # https://docs.sentinel-hub.com/api/latest/api/overview/processing-unit/
    pu = 1.0

    # Processing with batch processing API will result in a multiplication factor of 1/3.
    # Thus, three times more data can be processed comparing to process API for the same amount of PUs.
    if batch_processing:
        pu = pu / 3.0

    # The multiplication factor is calculated by dividing requested output (image) size (width x height) by 512 x 512.
    # The minimum value of this multiplication factor is 0.01. This corresponds to an area of 0.25 km2 for
    # Sentinel-2 data at 10 m spatial resolution.
    pu *= max((width * height) / (512.0 * 512.0), 0.01)

    # The multiplication factor is calculated by dividing the requested number of input bands by 3.
    # An exception is requesting dataMask which is not counted.
    pu *= n_input_bands_without_datamask / 3.0

    # Requesting 32 bit float TIFF will result in a multiplication factor of 2 due to larger memory consumption and data traffic.
    # Requesting application/octet-stream will result in a multiplication factor of 1.4 due to additional integration costs
    # (This is used for integration with external tools such as xcube.).
    if output_format == OutputFormat.IMAGE_TIFF_DEPTH_32:
        pu *= 2.0
    elif output_format == OutputFormat.APPLICATION_OCTET_STREAM:
        pu *= 1.4

    # The multiplication factor equals the number of data samples per pixel.
    pu *= n_data_samples

    # Requesting orthorectification (for S1 GRD data) will result in a multiplication factor of 2 due to additional
    # processing requirements (This rule is not applied at the moment.).
    # if s1_orthorectification:
    #     pu *= 2.

    # The minimal weight for a request is 0.001 PU.
    pu = max(pu, 0.001)
    return pu


def apply_for_request(processing_units: float) -> float:
    """
    Decrements & fetches the Redis counters and calculates the delay.
    """
    # figure out the types of the buckets so we know how much to decrement them:
    with rds.pipeline() as pipe:
        pipe.hgetall(REDIS_TYPES_KEY)
        pipe.hgetall(REDIS_REFILLS_KEY)
        policy_types, policy_refills = pipe.execute()

    logging.debug(f"Policy types: {policy_types}")
    logging.debug(f"Policy bucket refills: {policy_refills}ns")

    # decrement buckets according to their type:
    with rds.pipeline() as pipe:
        buckets_types_items = policy_types.items()
        for policy_id, policy_type in buckets_types_items:
            pipe.hincrbyfloat(
                REDIS_REMAINING_KEY,
                policy_id,
                -processing_units if policy_type.decode() == PolicyType.PROCESSING_UNITS.value else -1,
            )
        new_remaining = pipe.execute()
        new_remaining = dict(zip([policy_id for policy_id, _ in buckets_types_items], new_remaining))

    logging.debug(f"Bucket values after decrementing them: {new_remaining}")
    wait_times_ns = [-new_remaining[policy_id] * float(policy_refills[policy_id]) for policy_id in new_remaining.keys()]
    logging.debug(f"Wait times in s for each policy: {[0 if ns < 0 else ns / 1000000000. for ns in wait_times_ns]}")
    delay_ns = max(wait_times_ns)
    if delay_ns < 0:
        return 0
    return delay_ns / 1000000000.0
