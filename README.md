# Sentinel Hub Rate Limiting Guard

## Problem statement

We have N (hundreds and even thousands) of parallel workers competing for the same rate-limited resource. We would like to coordinate them so that:
- 429 responses from SH don't happen (much)
- latencies are low when the system is empty
- the bandwidth is used as much as possible

The workers are expected to make a single request, process it (taking some time to do it), then make another. The responses themselves could take several seconds to complete.

### About SH Rate Limiting

- https://docs.sentinel-hub.com/api/latest/api/overview/rate-limiting/
- definition of Processing Unit (PU) - https://docs.sentinel-hub.com/api/latest/api/overview/processing-unit/

### Fetching limits

The request:
https://services.sentinel-hub.com/aux/ratelimit/statistics/tokenCounts/30641222-602c-40b5-8455-db3997d5cd24

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

All of the limits are checked; request is rejected with 429 if any of them are breached.


## Proposed solution

When worker wants to issue a request to Sentinel hub, it must first obtain a permission from this service.

The parameters for getting a permission are:
- number of Processing Units

The response will contain:
- `delay`: the number of milliseconds worker should wait before issuing its request


## Implementation details

The design of the system is similar to rate limiting solution using the buckets. The main difference is that the values in the bucket can be negative (which helps us determine the time worker should wait for). There are multiple buckets - one for each limit. The limit can be number of requests or number of PUs.

Workers:
- ask for permission
- wait if needed
- perform request
  - failsafe: if it still fails with 429 (which shouldn't happen), repeat the process (+ log for monitoring purposes)

When worker asks for permission to perform 1 request and use N Processing Units, it gets:

- all bucket values are decremented as needed (by 1 for request limits, by N for PU limits)
- if all bucket values are above or equal to 0, delay = 0 is returned
- otherwise:
  - delays for all buckets are calculated: delay equals time when the bucket will again be non-negative (depends on filling rate)
  - `max(delays)` is returned

An independent process is filling the buckets according to limits. These limits can be fetched at start time from service, or (at least initially) specified as params at boot time.


Possible improvements:
- prioritization of workers: some workers could be delayed on some limit which is above 0, meaning that some of the bandwidth is reserved for other, more latency-important workers.
  - we could do that for every worker, to make sure we never reach 429 response
- number of PUs could be calculated from supplied parameters (but probably not from the evalscript itself - that would be difficult to achieve)

Concerns:
- it could happen that real limits (capacity) and our buckets' values are not completely in sync. However, this leads in worst case to some 429 responses which the workers should handle gracefully (and this improves sync because value in buckets is lowered while capacity is not used). If somehow bucket value happens to be lower than capacity allowed (which should not happen - workers have requested capacity they haven't used), at worst the requests will be delayed somewhat, and values will sync as soon as system is idle(r).
- theoretically it could happen that a worker would get a delayed permission based on one bucket. Because of delay, another bucket (which was topped-out at the time) could reach zero at the time of the actual request, leading to 429 on service. This seems like a very low probability event though, with limited impact (because of previous point).
