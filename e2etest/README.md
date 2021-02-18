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

### 1 worker, 1000 requests per worker (total 1000 requests)

When fetching resources with a single worker, rlguard doesn't change the total time it takes to complete (it is close to ideal even with the exponential backoff anyway), but it does lower the amount of 429 responses. With rlguard enabled there are still some 429 responses happening at the start, while the system is stabilizing, but not many.

Example for 1000 requests and policies `50 req / 10 s` and `200 PU / 10 s`, single consumer:
```
===================================
Test: use rlguard [False], total requests [1000], workers [1], policies [50 RQ / 10 s, 200 PU / 10 s]
Expected test time: > 190.0s
Total_time: 190.6s
Number of 429 responses: 181
Successfully completed: 1000 / 1000
===================================

===================================
Test: use rlguard [True], total requests [1000], workers [1], policies [50 RQ / 10 s, 200 PU / 10 s]
Expected test time: > 190.0s
Total_time: 190.9s
Number of 429 responses: 5
Successfully completed: 1000 / 1000
===================================
```

### 50 workers, 10 requests per worker (total 500 requests)

When number of workers rises, so does the probability that they interfere with each other. What starts happening is also that some workers are always overtaken by their peers who consume all available bandwidth, leading to exponentially longer wait times (even though the bandwidth is soon again available). Rlguard in this case shows much better performance:
```
===================================
Test: use rlguard [False], total requests [500], workers [50], policies [50 RQ / 10 s, 200 PU / 10 s]
Expected test time: > 90.0s
Total_time: 515.2s
Number of 429 responses: 372
Successfully completed: 500 / 500
===================================

===================================
Test: use rlguard [True], total requests [500], workers [50], policies [50 RQ / 10 s, 200 PU / 10 s]
Expected test time: > 90.0s
Total_time: 91.0s
Number of 429 responses: 5
Successfully completed: 500 / 500
===================================
```

Some of the delays with exponential backoff with jitter reached as much as 4 minutes. It can be argued that jitter added to this, as it put some of the workers behind others. Without jitter (meaning: multiple workers perform requests at approximately the same time, even after delay) the time is better, but at the expense of total time.
```
===================================
Test: use rlguard [False], total requests [500], workers [50], policies [50 RQ / 10 s, 200 PU / 10 s]
Expected test time: > 90.0s
Total_time: 96.2s
Number of 429 responses: 517
Successfully completed: 500 / 500
===================================
```

Also, in real-world setting it is doubtful that all the workers would start at exactly the same time. Adding a startup delay of up to 1 second (random) made the time worse, though not as bad as with exp. backoff with jitter:
```
===================================
Test: use rlguard [False], total requests [500], workers [50], policies [50 RQ / 10 s, 200 PU / 10 s]
Expected test time: > 90.0s
Total_time: 128.8s
Number of 429 responses: 511
Successfully completed: 500 / 500
===================================
```

### 200 workers, 5 requests per worker (total 1000 requests)

Trying the best un-coordinated strategy from previous tests (random startup delay, exp. backoff, no jitter) against rlguard on a bigger number of workers (200) shows how the number workers really affects the outcome:
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
Total_time: 191.1s
Number of 429 responses: 4
Successfully completed: 1000 / 1000
===================================
```


### 200 workers, 3 requests per worker (total 600 requests)

Lowering the total number of requests allows us to get some results without rllimit, when we limit the max. delay to 10s. The reasoning for this threshold is that this is the time it takes the bucket to refill completely if there were no requests. This limit thus prevents the worker to wait for more than the refill time, which means that the achieved times should be comparable to optimum (on expense of more 429 responses).

```
===================================
Test: use rlguard [False], total requests [600], workers [200], policies [50 RQ / 10 s, 200 PU / 10 s]
Expected test time: > 110.0s
Total_time: 113.7s
Number of 429 responses: 1670
Successfully completed: 600 / 600
===================================

===================================
Test: use rlguard [True], total requests [600], workers [200], policies [50 RQ / 10 s, 200 PU / 10 s]
Expected test time: > 110.0s
Total_time: 110.9s
Number of 429 responses: 5
Successfully completed: 600 / 600
===================================
```

Without rlguard, workers achieved execution time comparable to rlguard (and optimum, for that matter). However the number of 429 responses is very big, because workers are effectively using remote service as their coordinator.

To be fair, it is almost certainly possible to fine-tune the parameters of uncoordinated workers to achieve better results. However parameters depend on number of workers and the schedule in which they make their requests, making it difficult to guess correct parameters. It might also be impossible to do so in real-world setting where workers are created on-the-fly, as needed, and the number of requests varies through time.
