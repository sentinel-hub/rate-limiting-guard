from fastapi import FastAPI

app = FastAPI()


FAKE_USER_ID = "1234567890"
# payload: { "sub": "1234567890" }
FAKE_USER_JWT_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"

POLICIES_PU = [
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
POLICIES_RQ = [
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
def get_contract():
    return {
        "data": [
            {
                "policies": POLICIES_RQ,
                "type": {
                    "name": "REQUESTS",
                },
            },
            {
                "policies": POLICIES_PU,
                "type": {
                    "name": "PROCESSING_UNITS",
                },
            },
        ],
    }


@app.get(f"/aux/ratelimit/statistics/tokenCounts/{FAKE_USER_ID}")
def get_stats():
    return {
        "data": {
            "REQUESTS": {p["samplingPeriod"]: p["capacity"] for p in POLICIES_RQ},
            "PROCESSING_UNITS": {
                p["samplingPeriod"]: p["capacity"] for p in POLICIES_PU
            },
        }
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0")