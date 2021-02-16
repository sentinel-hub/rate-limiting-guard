import asyncio
import random
import time
from typing import List, Optional

from fastapi import FastAPI, Request, Response
from pydantic import BaseModel

app = FastAPI(
    # workaround so that /docs endpoint works:
    #   https://github.com/iwpnd/fastapi-aws-lambda-example/issues/2
    openapi_prefix="/",
    title="Mock API for e2e tests on SH rate limiting guard project",
    description="Implements endpoints which allow e2e tests to run without access to Sentinel Hub. Also mimics SH rate limiting.",
)


FAKE_USER_ID = "1234567890"
# payload: { "sub": "1234567890" }
FAKE_USER_JWT_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
)


class Policy(BaseModel):
    capacity: int
    samplingPeriod: str
    nanosBetweenRefills: int


app.state.POLICIES_PU: List[Policy] = [
    {
        "capacity": 1000,
        "samplingPeriod": "PT1M",
        "nanosBetweenRefills": 60000000,
    },
    {
        "capacity": 400000,
        "samplingPeriod": "PT744H",
        "nanosBetweenRefills": 6696000000,
    },
]
app.state.POLICIES_RQ: List[Policy] = [
    {
        "capacity": 1000,
        "samplingPeriod": "PT1M",
        "nanosBetweenRefills": 60000000,
    }
]
app.state.buckets_pu = [p["capacity"] for p in app.state.POLICIES_PU]
app.state.buckets_rq = [p["capacity"] for p in app.state.POLICIES_RQ]
app.state.last_increment_time = time.monotonic()


@app.post("/oauth/token")
def post_oauth_token():
    return {"access_token": FAKE_USER_JWT_TOKEN}


@app.get("/aux/ratelimit/contract")
def get_contract(request: Request):
    return {
        "data": [
            {
                "policies": request.app.state.POLICIES_RQ,
                "type": {"name": "REQUESTS"},
            },
            {
                "policies": request.app.state.POLICIES_PU,
                "type": {"name": "PROCESSING_UNITS"},
            },
        ],
    }


@app.get(f"/aux/ratelimit/statistics/tokenCounts/{FAKE_USER_ID}")
def get_stats(request: Request):
    return {
        "data": {
            "REQUESTS": {p["samplingPeriod"]: p["capacity"] for p in request.app.state.POLICIES_RQ},
            "PROCESSING_UNITS": {p["samplingPeriod"]: p["capacity"] for p in request.app.state.POLICIES_PU},
        },
    }


@app.put("/policies/PU")
def put_policies_pu(policies: List[Policy], request: Request):
    request.app.state.POLICIES_PU = policies
    # reset the rate limiting buckets values and the filling time:
    request.app.state.buckets_pu = [p["capacity"] for p in policies]
    request.app.state.last_increment_time = time.monotonic()
    return Response(status_code=202)


@app.put("/policies/RQ")
def put_policies_rq(policies: List[Policy], request: Request):
    request.app.state.POLICIES_RQ = policies
    request.app.state.buckets_rq = [p["capacity"] for p in policies]
    request.app.state.last_increment_time = time.monotonic()
    return Response(status_code=202)


@app.post("/refill_buckets")
async def post_refill_buckets(request: Request):
    """
    Maintenance endpoint - updates rate limiting buckets if needed. Should be called around every second or so.
    """
    now = time.monotonic()
    time_passed = now - request.app.state.last_increment_time
    if time_passed < 0.8:  # no need to refill too often
        return
    request.app.state.last_increment_time = now

    # enough time has passed, we need to increment all buckets:
    for i, policy in enumerate(request.app.state.POLICIES_RQ):
        fill_per_s = 10 ** 9 / policy["nanosBetweenRefills"]
        request.app.state.buckets_rq[i] = min(
            policy["capacity"],
            request.app.state.buckets_rq[i] + time_passed * fill_per_s,
        )
    for i, policy in enumerate(request.app.state.POLICIES_PU):
        fill_per_s = 10 ** 9 / policy["nanosBetweenRefills"]
        request.app.state.buckets_pu[i] = min(
            policy["capacity"],
            request.app.state.buckets_pu[i] + time_passed * fill_per_s,
        )


@app.get("/data")
async def get_data(processing_units: int, request: Request):
    """
    This endpoint mocks a request for data on Sentinel Hub:
    - checks buckets to determine if 429 should be returned
    - decrements buckets
    - sleeps for some time, then returns
    """
    # check if any policy is depleted - if so, return 429:
    for bucket_value in request.app.state.buckets_rq:
        if bucket_value < 1.0:
            return Response(content="Rate limit reached", status_code=429)
    for bucket_value in request.app.state.buckets_pu:
        if bucket_value < processing_units:
            return Response(content="Rate limit reached", status_code=429)

    # otherwise decrement all of the buckets appropriately:
    for i in range(len(request.app.state.buckets_rq)):
        request.app.state.buckets_rq[i] -= 1.0
    for i in range(len(request.app.state.buckets_pu)):
        request.app.state.buckets_pu[i] -= processing_units

    # sleep for some time and return response 200:
    await asyncio.sleep(0.5 + random.random())  # 0.5 - 1.5 s delay
    return Response(content="", status_code=200)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0")
