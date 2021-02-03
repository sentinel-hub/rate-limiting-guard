# Sentinel Hub Rate Limiting Guard

SH Rate Limiting Guard is a centralized rate limiting service + library for workers on the client side. Instead of enforcing the limit, it instructs workers on how long they should wait before making a request to Sentinel Hub.

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
       |                                                                         |                |
       |                                                                         |                |
       |                                           +-------------+     +---------v--------+       |
       |                                           |             |     |                  |       |
       |                                           |    Redis    <-----+      Syncer      |       |
       |                                           |             |     |                  |       |
request|                                           +--^-------^--+     +------------------+       |request
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
   |   |        synchronize|              |     .  .  .  .      |             |synchronize        |    |
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
### Sentinel Hub Rate Limiting sources

- https://docs.sentinel-hub.com/api/latest/api/overview/rate-limiting/
- definition of Processing Unit (PU) - https://docs.sentinel-hub.com/api/latest/api/overview/processing-unit/

### Fetching limits

The request:
https://services.sentinel-hub.com/aux/ratelimit/statistics/tokenCounts/:userId

Returns:
```json
{
  "data" : {
    "REQUESTS" : {
      "PT1M" : 1000.0
    },
    "PROCESSING_UNITS" : {
      "PT1M" : 1000.0,
      "PT744H" : 400000.0
    }
  }
}
```

This request:
https://services.sentinel-hub.com/aux/ratelimit/contract?userId=eq%3A:userId

returns:
```json
{
  "data" : [ {
    "@id" : "https://services.sentinel-hub.com/aux/ratelimit//contract/1547",
    "id" : 1547,
    "policies" : [ {
      "capacity" : 1000,
      "samplingPeriod" : "PT1M",
      "nanosBetweenRefills" : 60000000,
      "niceSamplindPeriod" : "1 minute"
    }, {
      "capacity" : 400000,
      "samplingPeriod" : "PT744H",
      "nanosBetweenRefills" : 6696000000,
      "niceSamplindPeriod" : "31 days"
    } ],
    "usageNotificationExtra" : { },
    "userId" : "...",
    "type" : {
      "name" : "PROCESSING_UNITS",
      "suffix" : "PU",
      "defaultPolicies" : [ {
        "capacity" : 30000,
        "samplingPeriod" : "PT744H",
        "nanosBetweenRefills" : 89280000000,
        "niceSamplindPeriod" : "31 days"
      }, {
        "capacity" : 300,
        "samplingPeriod" : "PT1M",
        "nanosBetweenRefills" : 200000000,
        "niceSamplindPeriod" : "1 minute"
      } ]
    }
  }, {
    "@id" : "https://services.sentinel-hub.com/aux/ratelimit//contract/364",
    "id" : 364,
    "policies" : [ {
      "capacity" : 1000,
      "samplingPeriod" : "PT1M",
      "nanosBetweenRefills" : 60000000,
      "niceSamplindPeriod" : "1 minute"
    } ],
    "usageNotificationExtra" : { },
    "userId" : "...",
    "type" : {
      "name" : "REQUESTS",
      "suffix" : "",
      "defaultPolicies" : [ {
        "capacity" : 30000,
        "samplingPeriod" : "PT744H",
        "nanosBetweenRefills" : 89280000000,
        "niceSamplindPeriod" : "31 days"
      }, {
        "capacity" : 300,
        "samplingPeriod" : "PT1M",
        "nanosBetweenRefills" : 200000000,
        "niceSamplindPeriod" : "1 minute"
      } ]
    }
  } ],
  "links" : {
    "currentToken" : "0"
  }
}
```

All of the limits are checked; request is rejected with 429 if any of them are breached.


## Implementation details

The design of the system is similar to rate limiting solution using the buckets. The main difference is that the values in the bucket can be negative (which helps us determine the time worker should wait for). There are multiple buckets - one for each policy. The policy can be tied to either number of requests or number of PUs.

When worker asks for permission to perform 1 request and use N Processing Units:

- all bucket values are decremented as needed (by 1 for request limits, by N for PU limits)
- if all bucket values are above or equal to 0, delay = 0 is returned
- otherwise:
  - delays for all buckets are calculated: delay equals time when the bucket will again be non-negative (depends on filling rate)
  - `max(delays)` is returned

An independent process (`syncer`) is filling the buckets according to limits. These limits are fetched at start time from Sentinel Hub service.

Concerns:
- it could happen that real limits (capacity) and our buckets' values are not completely in sync. However, this leads in worst case to some 429 responses which the workers should handle gracefully (and this improves sync because value in buckets is lowered while capacity is not used). If somehow bucket value happens to be lower than capacity allowed (which should not happen - this would mean that the workers have requested capacity they haven't used), at worst the requests will be delayed somewhat, and values will sync as soon as system is idle(r).
- in theory it could happen that a worker would get a delayed permission based on one bucket. Because of delay, another bucket (which was topped-out at the time) could reach zero at the time of the actual request, leading to 429 on service. This seems like a very low probability event though, with limited impact (because of previous point).
