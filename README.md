# Sentinel Hub Rate Limiting Guard

SH Rate Limiting Guard is a centralized rate limiting service + library whish allows parallel workers to efficiently adapt to rate limits when requesting data from Sentinel Hub.

Instead of enforcing the limit, it instructs workers on how long they should wait before making a request to Sentinel Hub.

## Problem statement

We have N (where N is in thousands) of parallel workers which would like to perform requests to Sentinel Hub. The account we use is [rate limited](https://docs.sentinel-hub.com/api/latest/api/overview/rate-limiting/).

We wish to coordinate the workers so that:
- 429 responses from SH don't happen too often
- latencies are as low as possible
- the bandwidth is used as much as possible
- no worker is starved (first come first served principle)

The workers are expected to make a single request, process it (taking some time to do it), then make another. The responses themselves could take several seconds to complete.

## Solution

When worker wants to issue a request to Sentinel hub, it must first obtain a permission to do so. Permission comes in a form of delay - how long the worker should wait for before issuing the request.

The process for workers is:
1) ask for permission
2) wait if needed (as indicated by `delay`)
3) perform request
4) if request fails for any reason and we wish to retry, start again with step 1

The only parameter when asking for permission is number of [Processing Units (PU)](https://docs.sentinel-hub.com/api/latest/api/overview/processing-unit/) the request will consume. To help with calculation, a utility function `calculate_processing_units()` is provided.

The worker must observe the returned delay time as closely as possible. It should not wait for more than the time specified, otherwise it might interfere with other workers' downloads.

Even with this synchronization mechanism, it is possible that worker gets response 429, and request could also fail for other reasons. The general rule is that if request fails for any reason, worker should again obtain a permission before making a new request.

Note that we rely on workers to cooperate with each other. In other words, this solution doesn't protect against adversarial workers or workers which do not obey the rules stated above.

### Architecture

This repository provides:
- Syncer service
- RLGuard library

```
     +-----------------------------------------------------------------------------------------------------+
     |                                                                                                     |
     |                                                                         Sentinel Hub                |
     |                                                                                                     |
     +-----^-------------------------------------------------------------------------+----------------^----+
           |                                                                         |                |
           |                                                                         | (0) init       |
           |                                                                         |                |
           |                                           +-------------+     +---------v--------+       |
           |                                           |             |     |                  |       |
           |                                           |    Redis    <-----+      Syncer      |       |
           |                                           |             |     |                  |       |
(2) request|                                           +--^-------^--+     +------------------+       |request (2)
       data|                                              |       |                                   |data
           |                                              |       |                                   |
           |                                              |       |                                   |
           |                                              |       |                                   |
           |                                              |       |                                   |
           |                                              |       |                                   |
           |                   +--------------------------+       +---------------+                   |
           |                   |                                                  |                   |
           |                   |       WORKER 1                     WORKER N      |                   |
       +--------------------------------------+                     +--------------------------------------+
       |   |                   |              |                     |             |                   |    |
       |   |       +-----------v------------+ |                     | +-----------v------------+      |    |
       |   |       |                        | |                     | |                        |      |    |
       |   |       |    RLGuard library     | |                     | |    RLGuard library     |      |    |
       |   |       |                        | |                     | |                        |      |    |
       |   |       +-----------^------------+ |                     | +-----------^------------+      |    |
       |   |                   |              |                     |             |                   |    |
       |   |    (1) synchronize|              |     .  .  .  .      |             |synchronize (1)    |    |
       |   |            request|              |                     |             |request            |    |
       |   |                   |              |                     |             |                   |    |
       | +-v-------------------v------------+ |                     | +-----------v-------------------v--+ |
       | |                                  | |                     | |                                  | |
       | |          Worker process          | |                     | |    Worker process                | |
       | |                                  | |                     | |                                  | |
       | +----------------------------------+ |                     | +----------------------------------+ |
       |                                      |                     |                                      |
       +--------------------------------------+                     +--------------------------------------+
```


## Installation

Both service and library are provided as examples. We encourage you to investigate them and to adapt them to your needs.

### Syncer service

The purpose of `syncer` service is to synchronize the internal state (counters which are kept in Redis) with Sentinel Hub. It does so by periodically refilling the buckets according to user's rate limiting policies.

Before running the `syncer` service, first edit `.env` file and set user credentials:
```
CLIENT_ID=...
CLIENT_SECRET="..."
```

Note: since `CLIENT_SECRET` contains special characters by design, you should enclose it in double quotes.

Syncer service depends on Redis. You can run both of them using Docker and Docker-compose:
```
$ docker-compose build
$ docker-compose up -d
```

### RLGuard library

The purpose of `RLGuard` library is to make applying for a permission to make a request to Sentinel Hub a bit easier. It provides two functions:
- `apply_for_request`: updates the counters in the central storage (Redis) and calculates the delay worker should wait for before making a request to Sentinel Hub, and
- `calculate_processing_units`: helper function to calculate the number of [Processing Units](https://docs.sentinel-hub.com/api/latest/api/overview/processing-unit/) the request will use

For the time being, the library is only available as part of this repository (i.e., it can't be installed via `pip` and similar mechanisms).

To use it:
- copy `lib/rlguard.py` file to your project,
- import it, and
- use `calculate_processing_units` and `apply_for_request` in your code.

For an example see `lib/example.py`.

## Additional information

See [DETAILS.md](./DETAILS.md) for additional implementation information.
