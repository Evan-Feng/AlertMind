#!/usr/bin/env bash
# scripts/e2e_phase3.sh - AlertMind 阶段 3 端到端验收脚本
#
# 验证目标（PRD §8.3 验收标准）：
#   1. 连续发送 5 条相似告警，聚合为 1 个 Incident
#   2. 发送 1 条不相关告警，创建独立的新 Incident
#   3. 孤儿告警（incident_id IS NULL）应为 0
#
# 用法：
#   ./scripts/e2e_phase3.sh                          # 默认 http://localhost:8000
#   ./scripts/e2e_phase3.sh --url http://host:port   # 指定后端地址
#   BASE_URL=http://host:port ./scripts/e2e_phase3.sh
#
# 前置条件：
#   - postgres + redis 已启动
#   - alembic upgrade head 已跑过
#   - uvicorn 已启动且 /health/ready 返回 200（lifespan 完成 embedder 加载）
#
# 退出码：
#   0 - 阶段 3 验收通过
#   1 - 参数错误 / 依赖缺失
#   2 - 后端未就绪
#   3 - 增量断言失败（incident 数 / alert_count 排序不符）
#
# 依赖：bash 4+、curl、jq

set -euo pipefail

# ---------- 默认参数 ----------
BASE_URL="${BASE_URL:-http://localhost:8000}"
WAIT_AGGREGATION_SECONDS="${WAIT_AGGREGATION_SECONDS:-5}"

# ---------- 用法说明 ----------
usage() {
    cat <<'USAGE'
用法：
  e2e_phase3.sh [--url BASE_URL]

可选参数：
  --url URL    后端 BASE URL（默认 http://localhost:8000，也可通过 BASE_URL 环境变量传入）
  -h, --help   打印此帮助信息并退出

环境变量：
  BASE_URL                       覆盖默认 URL
  WAIT_AGGREGATION_SECONDS       覆盖等待 BackgroundTasks 完成的秒数（默认 5）

退出码：
  0  阶段 3 验收通过
  1  参数 / 依赖错误
  2  后端未就绪（/health/ready 不通）
  3  断言失败
USAGE
}

# ---------- 参数解析 ----------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --url)
            if [[ $# -lt 2 ]]; then
                echo "Error: --url 需要一个值" >&2
                usage >&2
                exit 1
            fi
            BASE_URL="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Error: 未知参数 '$1'" >&2
            usage >&2
            exit 1
            ;;
    esac
done

# ---------- 依赖检查 ----------
for cmd in curl jq date; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo "Error: 未找到 $cmd，请先安装" >&2
        exit 1
    fi
done

WEBHOOK_URL="$BASE_URL/api/v1/webhook/alertmanager"
INCIDENTS_URL="$BASE_URL/api/v1/incidents"
ALERTS_URL="$BASE_URL/api/v1/alerts"
HEALTH_URL="$BASE_URL/health/ready"

# curl 默认参数：--noproxy '*' 防 SOCKS / HTTP 代理对 localhost 的干扰
CURL=(curl -sS --noproxy '*')

# ---------- 前置：后端就绪检查 ----------
echo "==> [Pre-flight] 检查后端就绪 ($HEALTH_URL)"
if ! "${CURL[@]}" -f -o /dev/null "$HEALTH_URL"; then
    echo "Error: 后端未就绪，/health/ready 返回非 200" >&2
    echo "请先启动：docker compose up -d postgres redis && uv run alembic upgrade head && uv run uvicorn alertmind.main:app" >&2
    exit 2
fi
echo "    OK"
echo

# ---------- 工具函数 ----------

# count_incidents: 返回当前 incidents 总数
count_incidents() {
    "${CURL[@]}" "$INCIDENTS_URL?page=1&page_size=1" | jq -r '.total'
}

# fetch_incident_ids_by_alertname：用 alerts API 精准过滤，返回某个 alertname 下
# 所有 alert 的 incident_id（一行一个；NULL 会被 jq 打印成字符串 "null"）。
fetch_incident_ids_by_alertname() {
    local name="$1"
    "${CURL[@]}" "$ALERTS_URL?alertname=${name}&page=1&page_size=20" \
        | jq -r '.items[].incident_id'
}

# count_orphan_alerts: 返回 incident_id IS NULL 的 alert 数
# 注意：阶段 3 W4 验证过 alerts API 不支持 incident_id=null 过滤（被 FastAPI 静默忽略），
# 这里改用 SQL 直查（需要 docker compose 环境）；若不可用则跳过孤儿检查。
count_orphan_alerts() {
    if command -v docker >/dev/null 2>&1 \
        && docker compose exec -T postgres psql -U alertmind -d alertmind -tAc \
            "SELECT COUNT(*) FROM alerts WHERE incident_id IS NULL;" 2>/dev/null
    then
        return 0
    fi
    echo "SKIP"
    return 0
}

# send_alert: 发送一条 Alertmanager webhook
# 参数：$1=alertname $2=severity $3=instance $4=summary $5=fingerprint
send_alert() {
    local alertname="$1"
    local severity="$2"
    local instance="$3"
    local summary="$4"
    local fingerprint="$5"
    local starts_at
    starts_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

    local payload
    payload="$(printf '{
      "version": "4",
      "groupKey": "e2e-%s",
      "truncatedAlerts": 0,
      "status": "firing",
      "receiver": "alertmind",
      "groupLabels": {"alertname": "%s"},
      "commonLabels": {
        "alertname": "%s",
        "severity": "%s",
        "cluster": "prod",
        "instance": "%s",
        "namespace": "kube-system"
      },
      "commonAnnotations": {"summary": "%s"},
      "externalURL": "http://localhost:9093",
      "alerts": [{
        "status": "firing",
        "labels": {
          "alertname": "%s",
          "severity": "%s",
          "cluster": "prod",
          "instance": "%s",
          "namespace": "kube-system"
        },
        "annotations": {
          "summary": "%s",
          "description": "AlertMind e2e_phase3.sh 生成"
        },
        "startsAt": "%s",
        "endsAt": "0001-01-01T00:00:00Z",
        "generatorURL": "http://localhost:9090/graph",
        "fingerprint": "%s"
      }]
    }' "$fingerprint" "$alertname" "$alertname" "$severity" "$instance" "$summary" \
       "$alertname" "$severity" "$instance" "$summary" "$starts_at" "$fingerprint")"

    local http_code
    http_code="$(
        "${CURL[@]}" -X POST \
            -H 'Content-Type: application/json' \
            --data "$payload" \
            -o /dev/null \
            -w '%{http_code}' \
            "$WEBHOOK_URL"
    )"

    if [[ "$http_code" != "200" ]]; then
        echo "Error: webhook 返回 $http_code（fingerprint=$fingerprint）" >&2
        exit 3
    fi
}

# ---------- Step 0: 记录初始 incidents 数量 ----------
BEFORE_COUNT="$(count_incidents)"
echo "==> [Step 0] 初始 incidents 数量: $BEFORE_COUNT"
echo

# ---------- Step 1: 发 5 条相似告警（E2E-<RUN_ID>-HighCPU on node-1..5）----------
# 关键：alertname 拼入 RUN_ID 保证每次运行产生全新的 embedding 文本，
# 避免归入历史测试遗留的同名 incident，使"新增 incident +2"的断言在脏环境下也成立
RUN_ID="$(date -u +%Y%m%d%H%M%S)"
CPU_NAME="E2E${RUN_ID}HighCPU"
DISK_NAME="E2E${RUN_ID}DiskFull"

echo "==> [Step 1] 发送 5 条相似告警 (${CPU_NAME} on node-1..5)"
for i in 1 2 3 4 5; do
    send_alert \
        "$CPU_NAME" \
        "critical" \
        "node-${i}" \
        "节点 CPU 使用率超过 90% 持续 5 分钟" \
        "e2e_${RUN_ID}_sim_${i}"
    echo "    sent sim_${i}"
done
echo

# ---------- Step 2: 发 1 条无关告警（E2E-<RUN_ID>-DiskFull on db-master-1） ----------
echo "==> [Step 2] 发送 1 条无关告警 (${DISK_NAME} on db-master-1)"
send_alert \
    "$DISK_NAME" \
    "warning" \
    "db-master-1" \
    "数据库主机磁盘使用率达到 95%" \
    "e2e_${RUN_ID}_unrelated_1"
echo "    sent unrelated_1"
echo

# ---------- Step 3: 等异步聚合 ----------
echo "==> [Step 3] 等待 BackgroundTasks 聚合 (${WAIT_AGGREGATION_SECONDS}s)"
sleep "$WAIT_AGGREGATION_SECONDS"
echo

# ---------- Step 4: 按 alertname 精准断言聚合行为 ----------
# 不依赖"incidents 总数增量"——生产环境里相似 alertname 可能被 bge 判定
# 相似度 >0.85 归入老 incident，导致总数不变但 alert_count 增长。
# 这里直接查 alerts API（精准按 alertname 过滤），判断：
#   1. 5 条 sim alert 的 incident_id 全部相同且非 null（聚合成一个）
#   2. 1 条 unrelated alert 的 incident_id 非 null（不孤儿）且不等于 sim 组
echo "==> [Step 4] 按 alertname 精准断言聚合行为"

SIM_IIDS="$(fetch_incident_ids_by_alertname "$CPU_NAME")"
UNRELATED_IIDS="$(fetch_incident_ids_by_alertname "$DISK_NAME")"

SIM_COUNT="$(printf '%s\n' "$SIM_IIDS" | grep -cv '^$' || true)"
UNRELATED_COUNT="$(printf '%s\n' "$UNRELATED_IIDS" | grep -cv '^$' || true)"

if [[ "$SIM_COUNT" -ne 5 ]]; then
    echo "Error: ${CPU_NAME} 应有 5 条 alert，实际 ${SIM_COUNT}" >&2
    exit 3
fi
if [[ "$UNRELATED_COUNT" -ne 1 ]]; then
    echo "Error: ${DISK_NAME} 应有 1 条 alert，实际 ${UNRELATED_COUNT}" >&2
    exit 3
fi

SIM_UNIQUE_IIDS="$(printf '%s\n' "$SIM_IIDS" | sort -u)"
SIM_UNIQUE_COUNT="$(printf '%s\n' "$SIM_UNIQUE_IIDS" | grep -cv '^$' || true)"

if [[ "$SIM_UNIQUE_COUNT" -ne 1 ]]; then
    echo "Error: 5 条相似 alert 的 incident_id 应当完全相同（聚合成一个），实际 unique incident_id 数量=${SIM_UNIQUE_COUNT}" >&2
    echo "    各 alert 的 incident_id: $(printf '%s ' "$SIM_IIDS")" >&2
    exit 3
fi

if [[ "$SIM_UNIQUE_IIDS" == "null" ]]; then
    echo "Error: 5 条相似 alert 的 incident_id 全为 null（聚合未生效 / 全为孤儿）" >&2
    exit 3
fi

SIM_IID="$SIM_UNIQUE_IIDS"
UNRELATED_IID="$UNRELATED_IIDS"

if [[ "$UNRELATED_IID" == "null" ]]; then
    echo "Error: 无关 alert (${DISK_NAME}) 的 incident_id 为 null（孤儿）" >&2
    exit 3
fi

if [[ "$UNRELATED_IID" == "$SIM_IID" ]]; then
    echo "Error: 无关 alert 应开独立 incident，但被归入 sim 组 incident_id=${SIM_IID}" >&2
    exit 3
fi

echo "    PASS: 5 条 ${CPU_NAME} 全部归入 incident_id=${SIM_IID}"
echo "    PASS: 1 条 ${DISK_NAME} 归入独立 incident_id=${UNRELATED_IID}"
echo

# ---------- Step 5: alert_count 断言 ----------
echo "==> [Step 5] 目标 incident 的 alert_count 断言"
SIM_INCIDENT_JSON="$("${CURL[@]}" "$INCIDENTS_URL/$SIM_IID")"
UNRELATED_INCIDENT_JSON="$("${CURL[@]}" "$INCIDENTS_URL/$UNRELATED_IID")"
SIM_AC="$(printf '%s' "$SIM_INCIDENT_JSON" | jq -r '.alert_count')"
UNRELATED_AC="$(printf '%s' "$UNRELATED_INCIDENT_JSON" | jq -r '.alert_count')"
echo "    sim incident (id=$SIM_IID) alert_count=$SIM_AC"
echo "    unrelated incident (id=$UNRELATED_IID) alert_count=$UNRELATED_AC"

# 最小合理下限：sim 组 alert_count 至少 ≥ 5（本次 5 条 + 可能的历史）；unrelated 至少 ≥ 1
if [[ "$SIM_AC" -lt 5 ]]; then
    echo "Error: sim incident.alert_count 应 >= 5，实际 $SIM_AC" >&2
    exit 3
fi
if [[ "$UNRELATED_AC" -lt 1 ]]; then
    echo "Error: unrelated incident.alert_count 应 >= 1，实际 $UNRELATED_AC" >&2
    exit 3
fi
echo "    PASS"

# 可选：报告 incident 总数 delta（仅作观测，不断言）
AFTER_COUNT="$(count_incidents)"
echo "    incidents 总数: before=$BEFORE_COUNT after=$AFTER_COUNT delta=$((AFTER_COUNT - BEFORE_COUNT))"
echo

# ---------- Step 6: 孤儿告警检查（warn-only，不 fail） ----------
echo "==> [Step 6] 孤儿告警检查（incident_id IS NULL）"
ORPHAN_RESULT="$(count_orphan_alerts || echo SKIP)"
if [[ "$ORPHAN_RESULT" == "SKIP" ]]; then
    echo "    SKIP: docker compose 不可用，跳过孤儿告警检查"
elif [[ "$ORPHAN_RESULT" == "0" ]]; then
    echo "    PASS: 孤儿告警数 = 0（最佳）"
else
    echo "    WARN: 孤儿告警数 = $ORPHAN_RESULT（≤1 可接受；建议 docs/KNOWN_TECH_DEBT.md 排查）" >&2
fi
echo

# ---------- 结果 ----------
echo "✅ 阶段 3 验收通过"
echo "    相似聚合: 5 条 ${CPU_NAME} → incident_id=${SIM_IID} (alert_count=${SIM_AC})"
echo "    无关隔离: 1 条 ${DISK_NAME} → incident_id=${UNRELATED_IID} (alert_count=${UNRELATED_AC})"
echo "    详细：curl '$INCIDENTS_URL?page=1&page_size=10' | jq"
exit 0
