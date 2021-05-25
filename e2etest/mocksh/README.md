# About mocksh

This service is intended for internal use within the e2e tests. It provides a customizable replacement service for Sentinel Hub.

It provides:
- endpoints which are needed by `syncer` service (auth, policies and stats)
- endpoints for changing the policies in effect
- endpoint for filling the buckets (should be called periodically while the test is running)
- endpoint for "fetching data" (which fails with response 429 if one of the buckets is empty)

Note that when the test changes the policies, it should also send restart `syncer` service to trigger re-fetching of the policies from this mock service.

Syncer service should be setup to use this service during tests (by `SENTINELHUB_ROOT_URL` env var) instead of Sentinel Hub.

