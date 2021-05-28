## Tests

To run end-to-end performance tests, first install the python packages needed:
```
$ cd e2etest/
$ pipenv install
```

Then, in the same directory:
```
$ docker-compose up -d
$ pipenv shell
<pipenv> $ pytest performance.py
```

There are some tests already defined (see the parameters to `test_ratelimiting` test), but to perform performance testing they should be adapted as required.


### Performance results

We are comparing 2 ways in which the workers can fetch data from the service:
- workers are not coordinated, each worker performs the requests it needs to, and if they get a 429 response, they retry (as many times as needed) with some delay.
- workers are coordinated through `rlguard`

Tests were performed using a mocked service (`mocksh`), which allowed us to set policies as needed.

### 1 worker, 1000 requests per worker (total 1000 requests)

Example for 1000 requests and policies `50 req / 10 s` and `200 PU / 10 s`, when there is a single consumer:
```
===================================
Test: use rlguard [False], total requests [1000], workers [1], policies [50 RQ / 10 s, 200 PU / 10 s]
Expected test time: > 190.0s
Total time: 190.6s
Number of 429 responses: 181
Successfully completed: 1000 / 1000
===================================

===================================
Test: use rlguard [True], total requests [1000], workers [1], policies [50 RQ / 10 s, 200 PU / 10 s]
Expected test time: > 190.0s
Total time: 190.9s
Number of 429 responses: 5
Successfully completed: 1000 / 1000
===================================
```

When fetching resources with a single worker, `rlguard` doesn't change the total time it takes to complete (total time is close to ideal even with the exponential backoff anyway), but it does lower the amount of 429 responses. With `rlguard` enabled there are still some 429 responses happening at the start, while the system is stabilizing, but not that many.

In effect, when there is a single worker, the requests are coordinated - 429 response means that all the requests should wait. We could improve performance by waiting just the amount supplied in return headers, but not by much (there would just be more 429 responses, when we hit the limit again and again).

### 50 workers, 10 requests per worker (total 500 requests)

When number of workers rises, so does the probability that they will interfere with each other. What starts happening with exponential backoff is also that some workers are *always* overtaken by their peers who consume all available bandwidth, leading to exponentially longer wait times (even though the bandwidth is soon again available). Using `rlguard` in this case shows better performance, both in terms of time and in number of 429 responses:
```
===================================
Test: use rlguard [False], total requests [500], workers [50], policies [50 RQ / 10 s, 200 PU / 10 s]
Expected test time: > 90.0s
Total time: 515.2s
Number of 429 responses: 372
Successfully completed: 500 / 500
===================================

===================================
Test: use rlguard [True], total requests [500], workers [50], policies [50 RQ / 10 s, 200 PU / 10 s]
Expected test time: > 90.0s
Total time: 91.0s
Number of 429 responses: 5
Successfully completed: 500 / 500
===================================
```

Some of the delays with exponential backoff with jitter reached as much as 4 minutes. It can be argued that jitter added to this, as it put some of the workers behind others. Without jitter (meaning: multiple workers perform requests at approximately the same time, even after delay) the time is better, but at the expense of number of 429 responses:
```
===================================
Test: use rlguard [False], total requests [500], workers [50], policies [50 RQ / 10 s, 200 PU / 10 s]
Expected test time: > 90.0s
Total time: 96.2s
Number of 429 responses: 517
Successfully completed: 500 / 500
===================================
```

Also, in real-world setting it is doubtful that all the workers would start at exactly the same time. Adding a startup delay of up to 1 second (random) made the time again worse, though not as bad as with exp. backoff with jitter:
```
===================================
Test: use rlguard [False], total requests [500], workers [50], policies [50 RQ / 10 s, 200 PU / 10 s]
Expected test time: > 90.0s
Total time: 128.8s
Number of 429 responses: 511
Successfully completed: 500 / 500
===================================
```

### 200 workers, 5 requests per worker (total 1000 requests)

Trying the best un-coordinated strategy from previous tests (random startup delay, exp. backoff, no jitter) against `rlguard` on a bigger number of workers (200) shows that the number workers really affects the outcome:
```
===================================
Test: use rlguard [False], total requests [1000], workers [200], policies [50 RQ / 10 s, 200 PU / 10 s]
Expected test time: > 190.0s
^C
```
After around 5 minutes, there were 87 workers waiting for 512 seconds, at which point it doesn't make sense to continue experiment.

Adding an upper limit to delay (max. 64s, 10s or 5s) helps because the workers don't wait as much, but the workers hit retry limit of 30.

With rlguard there are no problems:
```
===================================
Test: use rlguard [True], total requests [1000], policies [50 RQ / 10 s, 200 PU / 10 s]
Expected test time: > 190.0s
Total time: 191.1s
Number of 429 responses: 4
Successfully completed: 1000 / 1000
===================================
```


### 200 workers, 3 requests per worker (total 600 requests)

Lowering the total number of requests allows us to get some results without rllimit, when we limit the max. delay to 10s. The reasoning for this threshold is that this is the time it takes for the bucket to refill completely if there were no requests. This limit thus prevents the worker to wait for more than the refill time, which means that the achieved times should be comparable to optimum (at expense of more 429 responses).

```
===================================
Test: use rlguard [False], total requests [600], workers [200], policies [50 RQ / 10 s, 200 PU / 10 s]
Expected test time: > 110.0s
Total time: 113.7s
Number of 429 responses: 1670
Successfully completed: 600 / 600
===================================

===================================
Test: use rlguard [True], total requests [600], workers [200], policies [50 RQ / 10 s, 200 PU / 10 s]
Expected test time: > 110.0s
Total time: 110.9s
Number of 429 responses: 5
Successfully completed: 600 / 600
===================================
```

With lower amount of requests and limited retry time, workers achieved execution time comparable to `rlguard` (and optimum for that matter). However the number of 429 responses is very big, because workers are effectively using remote service as their coordinator.

To be fair, it is almost certainly possible to fine-tune the parameters of uncoordinated workers to achieve better results - when we know the number of workers and the amount of requests they will make. However parameters depend on number of workers and the schedule in which they make their requests, making it difficult to guess correct values. It might also be impossible to do so in real-world setting where workers are created on-the-fly, as needed, and the number of requests varies through time.
