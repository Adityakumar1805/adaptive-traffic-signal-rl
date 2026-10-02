# Benchmark Summary

Grid: 2x2 | seeds: [0, 1, 2] | episode: 1800s | means over the seeds of outputs/benchmark_results.csv

## Average waiting time (s) by scenario and controller

| scenario | fixed_time | max_pressure |   rl |
|:---------|-----------:|-------------:|-----:|
| low      |       26.3 |         19.2 | 22.3 |
| medium   |       34.3 |         22.5 | 25.2 |
| high     |       68.7 |         35.6 | 35.2 |
| rush     |      113.0 |         50.5 | 43.9 |

## Average queue (veh/intersection)

| scenario | fixed_time | max_pressure |   rl |
|:---------|-----------:|-------------:|-----:|
| low      |        3.0 |          2.2 |  2.5 |
| medium   |        6.8 |          4.4 |  5.0 |
| high     |       20.1 |         10.4 | 10.2 |
| rush     |       36.5 |         16.3 | 14.1 |

## Throughput (vehicles completed)

| scenario | fixed_time | max_pressure |   rl |
|:---------|-----------:|-------------:|-----:|
| low      |        808 |          809 |  807 |
| medium   |       1399 |         1400 | 1400 |
| high     |       2002 |         2051 | 2058 |
| rush     |       2093 |         2246 | 2253 |

## Emergency clearance time (s)

| scenario | fixed_time | max_pressure |   rl |
|:---------|-----------:|-------------:|-----:|
| low      |       26.0 |         26.3 | 20.0 |
| medium   |       28.7 |         36.7 | 18.3 |
| high     |       80.0 |         42.0 | 41.7 |
| rush     |      113.7 |         39.3 | 32.7 |

## RL vs fixed-time: change (%)

Negative = RL is lower. For waiting time and queue length lower is better; for throughput higher is better. Same sign convention as README.md.

| scenario | waiting time |   queue | throughput |
|:---------|-------------:|--------:|-----------:|
| low      |      −15.1 % | −15.3 % |     −0.1 % |
| medium   |      −26.5 % | −26.6 % |      0.0 % |
| high     |      −48.8 % | −48.9 % |     +2.8 % |
| rush     |      −61.2 % | −61.3 % |     +7.6 % |
| **mean** |      −37.9 % | −38.0 % |     +2.6 % |
