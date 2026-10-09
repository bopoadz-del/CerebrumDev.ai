### Ledger of `sess_f47605b0e22b4828` (roster account 0)

- platform `plt_4b053125e56d4580` product `product`
- state **failed** honesty `None` phase 2/5 current `{'id': 'WRITER', 'label': 'Platform manufacturer'}`
- failure: phase=WRITER; reason=writer_authored_factory_file; detail=writer_authored_factory_file: the writer touched .github/workflows/ci.yml, app/health.py, app/observe.py, app/revision.py, conftest.py, constraints.txt, docs/provenance/provenance.json, tests/test_data_lifecycle.py, tests/test_deploy.py, tests/test_domain_acceptance.py, tests/test_placeholder_contract.py, tests/test_routes.py, tests/test_smoke.py -- Factory-owned, restored to the Factory's version
- stopped: {"build_ceiling": 6, "check": "writer_contract", "class": "STOP", "finding": "block analytics loaded from a path outside the lock, not its locked path vendor/blocks/analytics: a block listed in blocks.lock.json must load from its locked path or fail loudly at start-up, never fall back silently", "gate": "WRITER", "gate_budget": 2, "gate_name": "writer_contract", "reason": "SAME_FAILURE_TWICE: writ
- last_error: 
- full ledger served: no (http 404)

Phase trail:

- {"phase": "COLLECTOR", "outcome": "passed", "reason": "", "location": "", "timestamp": "2026-10-08T19:51:05+00:00", "detail": "0 gap(s) declared for the writer"}
- {"phase": "CLONER", "outcome": "passed", "reason": "", "location": "", "timestamp": "2026-10-08T19:51:17+00:00", "detail": "18 block(s) import with no store configured; 35 file(s) across 18 block(s) verified against published digests; 18 signature(s) verified against the Store registry key"}
- {"phase": "WRITER", "outcome": "failed", "reason": "locked_block_loaded_elsewhere", "location": "WRITER", "timestamp": "2026-10-08T20:23:53+00:00", "detail": "locked_block_loaded_elsewhere: block analytics loaded from a path outside the lock, not its locked path vendor/blocks/analytics; block audit 
- {"phase": "TESTER", "outcome": "not_reached", "reason": "", "location": "", "timestamp": null, "detail": ""}
- {"phase": "STORE_MANAGER", "outcome": "not_reached", "reason": "", "location": "", "timestamp": null, "detail": ""}

Runner decisions:

- {"build_ceiling": 6, "check": "writer_contract", "class": "REWORK", "finding": ".github/workflows/ci.yml: created by the writer -- this file is Factory-owned (the Factory renders it); leave it to the Factory and do not write it", "gate": "WRITER", "gate_budget": 2, "gate_name": "writer_contract", "r
- {"build_ceiling": 6, "check": "writer_contract", "class": "STOP", "finding": "block analytics loaded from a path outside the lock, not its locked path vendor/blocks/analytics: a block listed in blocks.lock.json must load from its locked path or fail loudly at start-up, never fall back silently", "ga

Activity log (last 40):

```
2026-10-08T20:18:05+00:00 WRITER: writer working — 5m48s in, 95 file(s) on disk, 3 step(s) reported so far.
2026-10-08T20:19:05+00:00 WRITER: writer working — 6m48s in, 95 file(s) on disk, 3 step(s) reported so far.
2026-10-08T20:20:05+00:00 WRITER: writer working — 7m48s in, 95 file(s) on disk, 3 step(s) reported so far.
2026-10-08T20:21:05+00:00 WRITER: writer working — 8m48s in, 95 file(s) on disk, 3 step(s) reported so far.
2026-10-08T20:21:20+00:00 WRITER: writer: STEP 4 [BACKEND]: domain review pass -- barrel census now really spans BOTH cellars (census_rows reads the tenant's whole barrel shelf via the database block's list_all path; verified: posting
2026-10-08T20:21:20+00:00 WRITER: writer: STEP 5 [BACKEND]: moved the winery's own numbers out of handler source into named settings -- [redacted] (wine_club_shipments), [redacted] (cellar_ops_worklist); tightened the worklist auth ch
2026-10-08T20:21:20+00:00 WRITER: writer: STEP 6 [DEVOPS]: Dockerfile now copies requirements.txt + constraints.txt together and installs `-c constraints.txt -r requirements.txt` (the floor's pinned-install contract; constraints.txt i
2026-10-08T20:21:21+00:00 WRITER: writer: STEP 7: re-ran the gates on the changed tree -- python -m pytest 35 passed, factory_checks probe 0 halt/finding/miss, acceptance --self-check 18/22 (the 2 FAILs are the Factory-owned .github/w
2026-10-08T20:22:21+00:00 WRITER: writer working — 10m04s in, 95 file(s) on disk, 7 step(s) reported so far.
2026-10-08T20:22:52+00:00 WRITER: writer: STEP 8 [SECURITY]: adversarial pass on the frozen tree -- 26 /v1 routes all 401 unauthenticated (well-formed ids; malformed path params are 422 at the edge, never a record), cross-tenant read 
2026-10-08T20:22:52+00:00 WRITER: writer: STEP 9: final gates on the frozen tree -- pytest 35 passed, WRITER behaviour probe 0 halt/finding/miss, acceptance --self-check 18/22 (2 FAILs are the Factory-owned .github/workflows/ci.yml, r
2026-10-08T20:23:39+00:00 WRITER: writer: STEP 10 [SECURITY]: two findings from the adversarial pass applied and pinned -- the served console and frontend source no longer ship a prerfilled bearer token (app/static/index.html token in
2026-10-08T20:23:45+00:00 WRITER: FACTORY_CODE_CLI session finished — codewhale writer CLI exited
2026-10-08T20:23:45+00:00 WRITER: founding classes filled by the factory: docs/provenance/provenance.json
2026-10-08T20:23:53+00:00 WRITER: failure narrative (ledger)
```

Service log (800 lines naming the session; last 40):

```
1791490024551 INFO:     172.31.53.165:2472 - "GET /v1/sessions/sess_f47605b0e22b4828/product/package HTTP/1.1" 409 Conflict
1791490024772 INFO:     172.31.12.29:42152 - "GET /v1/sessions/sess_f47605b0e22b4828/product/build-status HTTP/1.1" 200 OK
1791490029986 INFO:     172.31.12.29:25260 - "GET /v1/sessions/sess_f47605b0e22b4828/product/package HTTP/1.1" 409 Conflict
1791490030196 INFO:     172.31.19.186:34902 - "GET /v1/sessions/sess_f47605b0e22b4828/product/build-status HTTP/1.1" 200 OK
1791490035419 INFO:     172.31.53.165:37698 - "GET /v1/sessions/sess_f47605b0e22b4828/product/package HTTP/1.1" 409 Conflict
1791490035642 INFO:     172.31.19.186:34906 - "GET /v1/sessions/sess_f47605b0e22b4828/product/build-status HTTP/1.1" 200 OK
1791490041020 INFO:     172.31.53.165:63484 - "GET /v1/sessions/sess_f47605b0e22b4828/product/package HTTP/1.1" 409 Conflict
1791490041250 INFO:     172.31.19.186:22698 - "GET /v1/sessions/sess_f47605b0e22b4828/product/build-status HTTP/1.1" 200 OK
1791490046478 INFO:     172.31.12.29:47880 - "GET /v1/sessions/sess_f47605b0e22b4828/product/package HTTP/1.1" 409 Conflict
1791490046688 INFO:     172.31.19.186:22700 - "GET /v1/sessions/sess_f47605b0e22b4828/product/build-status HTTP/1.1" 200 OK
1791490051907 INFO:     172.31.41.174:1556 - "GET /v1/sessions/sess_f47605b0e22b4828/product/package HTTP/1.1" 409 Conflict
1791490052116 INFO:     172.31.53.165:27504 - "GET /v1/sessions/sess_f47605b0e22b4828/product/build-status HTTP/1.1" 200 OK
1791490057355 INFO:     172.31.19.186:28542 - "GET /v1/sessions/sess_f47605b0e22b4828/product/package HTTP/1.1" 409 Conflict
1791490057579 INFO:     172.31.53.165:36984 - "GET /v1/sessions/sess_f47605b0e22b4828/product/build-status HTTP/1.1" 200 OK
1791490062811 INFO:     172.31.12.29:12232 - "GET /v1/sessions/sess_f47605b0e22b4828/product/package HTTP/1.1" 409 Conflict
1791490063039 INFO:     172.31.19.186:33176 - "GET /v1/sessions/sess_f47605b0e22b4828/product/build-status HTTP/1.1" 200 OK
1791490068249 INFO:     172.31.41.174:50434 - "GET /v1/sessions/sess_f47605b0e22b4828/product/package HTTP/1.1" 409 Conflict
1791490068458 INFO:     172.31.53.165:36820 - "GET /v1/sessions/sess_f47605b0e22b4828/product/build-status HTTP/1.1" 200 OK
1791490073670 INFO:     172.31.19.186:11506 - "GET /v1/sessions/sess_f47605b0e22b4828/product/package HTTP/1.1" 409 Conflict
1791490073884 INFO:     172.31.53.165:36832 - "GET /v1/sessions/sess_f47605b0e22b4828/product/build-status HTTP/1.1" 200 OK
1791490079162 INFO:     172.31.12.29:6628 - "GET /v1/sessions/sess_f47605b0e22b4828/product/package HTTP/1.1" 409 Conflict
1791490079499 INFO:     172.31.53.165:31528 - "GET /v1/sessions/sess_f47605b0e22b4828/product/build-status HTTP/1.1" 200 OK
1791490084728 INFO:     172.31.12.29:6636 - "GET /v1/sessions/sess_f47605b0e22b4828/product/package HTTP/1.1" 409 Conflict
1791490084947 INFO:     172.31.19.186:42292 - "GET /v1/sessions/sess_f47605b0e22b4828/product/build-status HTTP/1.1" 200 OK
1791490090159 INFO:     172.31.19.186:26398 - "GET /v1/sessions/sess_f47605b0e22b4828/product/package HTTP/1.1" 409 Conflict
1791490090374 INFO:     172.31.12.29:18760 - "GET /v1/sessions/sess_f47605b0e22b4828/product/build-status HTTP/1.1" 200 OK
1791490095584 INFO:     172.31.19.186:26414 - "GET /v1/sessions/sess_f47605b0e22b4828/product/package HTTP/1.1" 409 Conflict
1791490095797 INFO:     172.31.53.165:55250 - "GET /v1/sessions/sess_f47605b0e22b4828/product/build-status HTTP/1.1" 200 OK
1791490101165 INFO:     172.31.12.29:41378 - "GET /v1/sessions/sess_f47605b0e22b4828/product/package HTTP/1.1" 409 Conflict
1791490101392 INFO:     172.31.12.29:41378 - "GET /v1/sessions/sess_f47605b0e22b4828/product/build-status HTTP/1.1" 200 OK
1791490106614 INFO:     172.31.41.174:43316 - "GET /v1/sessions/sess_f47605b0e22b4828/product/package HTTP/1.1" 409 Conflict
1791490106828 INFO:     172.31.53.165:53162 - "GET /v1/sessions/sess_f47605b0e22b4828/product/build-status HTTP/1.1" 200 OK
1791490112056 INFO:     172.31.41.174:8818 - "GET /v1/sessions/sess_f47605b0e22b4828/product/package HTTP/1.1" 409 Conflict
1791490112265 INFO:     172.31.12.29:24096 - "GET /v1/sessions/sess_f47605b0e22b4828/product/build-status HTTP/1.1" 200 OK
1791490117485 INFO:     172.31.53.165:7432 - "GET /v1/sessions/sess_f47605b0e22b4828/product/package HTTP/1.1" 409 Conflict
1791490117695 INFO:     172.31.53.165:7442 - "GET /v1/sessions/sess_f47605b0e22b4828/product/build-status HTTP/1.1" 200 OK
1791490122909 INFO:     172.31.19.186:4922 - "GET /v1/sessions/sess_f47605b0e22b4828/product/package HTTP/1.1" 409 Conflict
1791490123147 INFO:     172.31.19.186:4926 - "GET /v1/sessions/sess_f47605b0e22b4828/product/build-status HTTP/1.1" 200 OK
1791490128375 INFO:     172.31.53.165:57860 - "GET /v1/sessions/sess_f47605b0e22b4828/product/package HTTP/1.1" 409 Conflict
1791490128584 INFO:     172.31.12.29:62308 - "GET /v1/sessions/sess_f47605b0e22b4828/product/build-status HTTP/1.1" 200 OK
```

