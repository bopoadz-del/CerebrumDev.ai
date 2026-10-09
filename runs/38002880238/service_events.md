### Service events (last 21600 s)

- task definition `arn:aws:ecs:us-west-2:059020085816:task-definition/cerebrumdev-backend:5`
- MemoryUtilization max **33.16650390625** at 2026-10-09T18:44:00+00:00 (358 points)
- CPUUtilization max **97.7373673915863** at 2026-10-09T18:44:00+00:00 (358 points)

Stopped tasks:


ECS events:

- 2026-10-09T22:05:34.386000+00:00 (service cerebrumdev-backend) has reached a steady state.
- 2026-10-09T22:05:34.385000+00:00 (service cerebrumdev-backend) (deployment ecs-svc/4898377691688528528) deployment completed.
- 2026-10-09T22:04:42.522000+00:00 (service cerebrumdev-backend, taskSet ecs-svc/2724164339164815955) has begun draining connections on 1 tasks.
- 2026-10-09T22:04:42.516000+00:00 (service cerebrumdev-backend) deregistered 1 targets in (target-group arn:aws:elasticloadbalancing:us-west-2:059020085816:targetgroup/cerebrumdev-backend/c60a6bb13d7053cd)
- 2026-10-09T22:04:32.396000+00:00 (service cerebrumdev-backend) has stopped 1 running tasks: (task 46c2d7bdc6924c61bb111555ee6afaa0).
- 2026-10-09T22:03:42.269000+00:00 (service cerebrumdev-backend) registered 1 targets in (target-group arn:aws:elasticloadbalancing:us-west-2:059020085816:targetgroup/cerebrumdev-backend/c60a6bb13d7053cd)
- 2026-10-09T22:03:01.844000+00:00 (service cerebrumdev-backend) has started 1 tasks: (task 7338f557f60a45b7b6dad8760d53671b).
- 2026-10-09T18:59:37.081000+00:00 (service cerebrumdev-backend) has reached a steady state.
- 2026-10-09T18:59:28.698000+00:00 (service cerebrumdev-backend) registered 1 targets in (target-group arn:aws:elasticloadbalancing:us-west-2:059020085816:targetgroup/cerebrumdev-backend/c60a6bb13d7053cd)
- 2026-10-09T18:58:23.735000+00:00 (service cerebrumdev-backend) has started 1 tasks: (task 46c2d7bdc6924c61bb111555ee6afaa0).
- 2026-10-09T18:58:22.991000+00:00 (service cerebrumdev-backend, taskSet ecs-svc/2724164339164815955) has begun draining connections on 1 tasks.
- 2026-10-09T18:58:22.985000+00:00 (service cerebrumdev-backend) deregistered 1 targets in (target-group arn:aws:elasticloadbalancing:us-west-2:059020085816:targetgroup/cerebrumdev-backend/c60a6bb13d7053cd)
- 2026-10-09T18:16:40.950000+00:00 (service cerebrumdev-backend) has reached a steady state.
- 2026-10-09T18:16:40.949000+00:00 (service cerebrumdev-backend) (deployment ecs-svc/2724164339164815955) deployment completed.
- 2026-10-09T18:15:48.772000+00:00 (service cerebrumdev-backend, taskSet ecs-svc/2523861903614899289) has begun draining connections on 1 tasks.
- 2026-10-09T18:15:48.767000+00:00 (service cerebrumdev-backend) deregistered 1 targets in (target-group arn:aws:elasticloadbalancing:us-west-2:059020085816:targetgroup/cerebrumdev-backend/c60a6bb13d7053cd)
- 2026-10-09T18:15:39.048000+00:00 (service cerebrumdev-backend) has stopped 1 running tasks: (task 135f1493db13453cb59b6fbfd74faf83).
- 2026-10-09T18:14:47.630000+00:00 (service cerebrumdev-backend) registered 1 targets in (target-group arn:aws:elasticloadbalancing:us-west-2:059020085816:targetgroup/cerebrumdev-backend/c60a6bb13d7053cd)
- 2026-10-09T18:13:47.684000+00:00 (service cerebrumdev-backend) has started 1 tasks: (task 5c1471c297764fd4b3c95518127b605b).

Lifecycle log:

```
1791569680564 INFO:     Started server process [1]
1791569788131 INFO:     Shutting down
1791569788232 INFO:     Finished server process [1]
1791572272127 INFO:     Shutting down
1791572272228 INFO:     Finished server process [1]
1791572355750 INFO:     Started server process [1]
1791572358331 INFO cerebrumdev.factory.orphan_recovery: orphan model_call recovery: [('skipped', '/app/storage/factory_outputs/sessions/sess_3c68fbd7a8204551/product'), ('skipped', '/app/storage/factory_outputs/sessions/sess_a2b535a306114685/product'), ('skipped', '/app/storage/factory_outputs/sessi
1791572358331 INFO cerebrumdev.factory.orphan_recovery: resumed orphaned WRITER at /app/storage/factory_outputs/sessions/sess_7bd4f34bfa834f5f/product already_running=False
1791583418775 INFO:     Started server process [1]
1791583522141 INFO:     Shutting down
1791583522242 INFO:     Finished server process [1]
```

