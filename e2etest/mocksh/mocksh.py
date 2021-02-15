from fastapi import FastAPI

app = FastAPI()


FAKE_USER_ID = "1234567890"
# payload: { "sub": "1234567890" }
FAKE_USER_JWT_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"


@app.post("/oauth/token")
def post_oauth_token():
    return {"access_token": FAKE_USER_JWT_TOKEN}


@app.get("/aux/ratelimit/contract")
def get_contract():
    return {
        "data": [
            {
                "policies": [
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
                ],
                "type": {
                    "name": "PROCESSING_UNITS",
                },
            },
            {
                "policies": [
                    {
                        "capacity": 1000,
                        "samplingPeriod": "PT1M",
                        "nanosBetweenRefills": 60000000,
                        "niceSamplindPeriod": "1 minute",
                    }
                ],
                "type": {
                    "name": "REQUESTS",
                },
            },
        ],
    }


@app.get(f"/aux/ratelimit/statistics/tokenCounts/{FAKE_USER_ID}")
def get_stats():
    return {
        "data": {
            "REQUESTS": {"PT1M": 1000.0},
            "PROCESSING_UNITS": {"PT1M": 1000.0, "PT744H": 400000.0},
        }
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0")
