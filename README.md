# AlertMind

> 给 Prometheus Alertmanager 装上「大脑」的开源 AIOps 平台——通过 LLM 实现告警聚合、根因分析与中文处置建议，接入零侵入，Docker Compose 一键启动。

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](./LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/evan-feng/alertmind?style=social)](https://github.com/evan-feng/alertmind/stargazers)
[![Status](https://img.shields.io/badge/status-WIP-orange.svg)](#)

---

## 🚧 项目开发中，即将发布 MVP

当前处于早期开发阶段，按分阶段路线图推进，完整功能即将陆续上线。

## Known Limitations

### Concurrent Webhook Race
When multiple similar alerts arrive simultaneously, they may create separate incidents
instead of being aggregated. This is because concurrent BackgroundTasks cannot see each
other's just-created incidents. A periodic reconciler is planned for v0.2.

### Orphan Alerts
If aggregation fails (e.g., embedding errors, DB issues), the alert remains in the
database with `incident_id = NULL`. These "orphan alerts" don't appear in the incidents
list but are queryable via direct SQL:

```bash
docker compose exec postgres psql -U alertmind -d alertmind -c \
  "SELECT id, fingerprint, status, severity, alertname FROM alerts WHERE incident_id IS NULL;"
```

A reconciler API and automatic re-aggregation are planned for v0.2.

### Cross-Service Aggregation May Be Too Aggressive
Alerts from different services with identical alertname (e.g., `HighRequestLatency` on
service-A vs service-B) may be aggregated into a single incident due to text similarity.
If this is undesired for your environment:
- Increase `AGGREGATION_SIMILARITY_THRESHOLD` (default 0.85, try 0.95)
- Add `service` / `job` to alert annotations to make text more discriminative
- v0.2 will introduce hard-partition by labels (e.g., `AGGREGATION_GROUP_BY=service,namespace`)

## Aggregation Tuning

Three environment variables control aggregation behavior:

| Variable | Default | Description |
|---|---|---|
| `AGGREGATION_SIMILARITY_THRESHOLD` | 0.85 | Cosine similarity threshold for grouping alerts. Higher = stricter. See [ADR-009](./docs/adr/ADR-009.md) for the empirical basis. |
| `AGGREGATION_WINDOW_HOURS` | 24 | Only consider active incidents within this window as aggregation candidates. |
| `EMBEDDING_DROP_LABELS` | `pod,container_id,instance_id,replica_id,uid,request_id,trace_id` | Comma-separated list of high-entropy labels to exclude from embedding text. Customize based on your Prometheus label conventions. |

To tune:
1. Run calibration: `cd backend && uv run python ../scripts/calibrate_similarity.py`
2. Adjust threshold based on observed distribution
3. Update `.env` and restart backend

## Environment Notes

### First-Time Startup
The first startup downloads the embedding model (~100MB, `BAAI/bge-small-zh-v1.5`)
from HuggingFace, taking around **60 seconds**. Subsequent startups use the local
cache (~3 seconds).

Production deployments should bake the model into the Docker image (planned for v0.2):

```dockerfile
RUN python -c "from sentence_transformers import SentenceTransformer; \\
    SentenceTransformer('BAAI/bge-small-zh-v1.5')"
```

### SOCKS Proxy
If your dev machine uses a SOCKS proxy (e.g., `all_proxy=socks5://...`), `httpx`
inside `huggingface_hub` will fail with `ImportError: 'socksio' package is not installed`
unless you either:

- `unset ALL_PROXY` before starting the backend, or
- Pre-download the model with proxy disabled, then start with proxy on:

```bash
unset ALL_PROXY all_proxy
python -c "from sentence_transformers import SentenceTransformer; \
    SentenceTransformer('BAAI/bge-small-zh-v1.5')"
```

This is the documented approach (we deliberately do not bundle `httpx[socks]` to
avoid dependency bloat). See `docs/OPERATIONAL_NOTES.md` ON-002 for context.

## 📖 文档

- **完整项目规格（Single Source of Truth）**：[AlertMind-PRD.md](./AlertMind-PRD.md)

*更多文档（Quick Start / Architecture / Configuration）将在 MVP 发布时同步放出。*
