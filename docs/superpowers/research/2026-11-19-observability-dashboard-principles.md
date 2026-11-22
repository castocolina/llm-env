# Grafana dashboard and observability principles

The llm-env dashboards follow the operator's questions from inventory and saturation to traffic and then to an individual Laya request. Dashboard links preserve the time range. Time-series panels place a table legend to the right so values can be scanned beside their series. Dashboard tests should check a populated panel, a missing source, and readable legends at desktop and narrow widths. [Grafana dashboard best practices](https://grafana.com/docs/grafana/latest/visualizations/dashboards/build-dashboards/best-practices/) · [Grafana legend options](https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/configure-legend/)

| Operator question | Signal and interpretation |
| --- | --- |
| What is running? | Compare intended Compose membership, qualified runtime identity, and source freshness independently. Show stale and unknown explicitly. |
| How much traffic is reaching the service? | Use per-second arrivals for activity and cumulative counters for exact reconciliation. Keep independent service boundaries, listener types, and private control traffic separate. |
| What happened to the requests? | Show response status classes, proxy errors, and aborts. HTTP completion reports the observed exchange; client receipt and asynchronous outcomes require independent evidence. |
| How slow is it and what is saturated? | Show terminal duration p95, in-flight exchanges, cgroup memory and pressure, and exporter queue/drop signals where available. The current histogram includes all terminal exchanges. Success and failure latency distributions still need separate qualified series. |
| What happened to this Laya request? | Open the exact server-generated ID and trace it to the authenticated caller event. Show an actual fallback only when the caller reports it. |

Use source-gated queries so a missing sample produces a gap. Keep bounded service, listener, status and outcome labels. Use histograms for aggregate percentiles and independent client traffic for reconciliation. Define alert thresholds separately when reliable baselines and actionable symptoms exist. [Google SRE four golden signals](https://sre.google/sre-book/monitoring-distributed-systems/) · [Prometheus instrumentation](https://prometheus.io/docs/practices/instrumentation/) · [Prometheus histograms](https://prometheus.io/docs/practices/histograms/)

Apply these checks with the inventory, traffic, detail and operational acceptance items in the November 19 observability parity plan.
