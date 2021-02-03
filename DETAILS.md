# Additional information

## Sentinel Hub Rate Limiting sources

- https://docs.sentinel-hub.com/api/latest/api/overview/rate-limiting/
- definition of Processing Unit (PU) - https://docs.sentinel-hub.com/api/latest/api/overview/processing-unit/

## Fetching limits

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

### Concerns

- it could happen that real limits (capacity) and our buckets' values are not completely in sync. However, this leads in worst case to some 429 responses which the workers should handle gracefully (and this improves sync because value in buckets is lowered while capacity is not used). If somehow bucket value happens to be lower than capacity allowed (which should not happen - this would mean that the workers have requested capacity they haven't used), at worst the requests will be delayed somewhat, and values will sync as soon as system is idle(r).

- in theory it could happen that a worker would get a delayed permission based on one bucket. Because of delay, another bucket (which was topped-out at the time) could reach zero at the time of the actual request, leading to 429 on service. This seems like a very low probability event though, with limited impact (because of previous point).
