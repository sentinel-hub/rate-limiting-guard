from typing import List

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
    return Response(status_code=202)


@app.put("/policies/RQ")
def put_policies_rq(policies: List[Policy], request: Request):
    request.app.state.POLICIES_RQ = policies
    return Response(status_code=202)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0")
