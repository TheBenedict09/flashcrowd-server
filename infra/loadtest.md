# Load test — capacity `C`

**Result: C = 254 req/s per server (p95 < 200 ms)** — measured Sat 24 Oct ~17:45 BST.

| Setting | Value |
|-|-|
| Target | EC2 `t3.micro`, Ubuntu 24.04, nginx serving static article HTML (same image as the boot bench) |
| Page | `/wiki/Mount_Fuji.html`, 557,602 bytes (uncompressed Parsoid HTML) |
| Load generator | separate `c6i.large` in the same region (London), `hey -z 30s`, private IP |

| Concurrency | req/s | p95 (ms) | non-200 |
|-|-|-|-|
| 10 | 252.3 | 40.2 | 0 |
| 25 | 251.6 | 111.9 | 0 |
| 50 | 253.8 | 199.7 | 0 |
| 100 | 254.9 | 632.5 | 0 |

**Reading it:** throughput is flat at ~254 req/s ≈ 1.1 Gbit/s — the server is **network-bound** (large uncompressed page), not CPU-bound; adding concurrency only adds queueing. A t3.micro's sustained network baseline is lower than its burst, so 254 is an optimistic short-run figure. Say this on the assumptions slide.

Raw numbers: `infra/capacity_results.json`; also `INFRA_MEASUREMENTS (kind='capacity')`.
