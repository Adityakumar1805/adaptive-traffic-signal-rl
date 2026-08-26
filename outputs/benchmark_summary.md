# Benchmark Summary

Grid: 2x2 | seeds: [0, 1, 2] | episode: 1800s

## Average waiting time (s) by scenario and controller

| scenario   |   fixed_time |   max_pressure |   rl |
|:-----------|-------------:|---------------:|-----:|
| high       |         68.7 |           35.6 | 35.2 |
| low        |         26.3 |           19.2 | 22.3 |
| medium     |         34.3 |           22.5 | 25.2 |
| rush       |        113   |           50.5 | 43.9 |

## Average queue (veh/intersection)

| scenario   |   fixed_time |   max_pressure |   rl |
|:-----------|-------------:|---------------:|-----:|
| high       |         20.1 |           10.4 | 10.2 |
| low        |          3   |            2.2 |  2.5 |
| medium     |          6.8 |            4.4 |  5   |
| rush       |         36.5 |           16.3 | 14.1 |

## RL improvement vs fixed-time (%)

|        |   wait_reduction_% |   queue_reduction_% |   throughput_gain_% |
|:-------|-------------------:|--------------------:|--------------------:|
| low    |               15.1 |                15.3 |                -0.1 |
| medium |               26.5 |                26.6 |                 0   |
| high   |               48.8 |                48.9 |                 2.8 |
| rush   |               61.2 |                61.3 |                 7.6 |