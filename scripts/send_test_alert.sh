#!/usr/bin/env bash
# scripts/send_test_alert.sh - 向 AlertMind 发送一条模拟 Alertmanager webhook
#
# 用法：
#   ./scripts/send_test_alert.sh [--status firing|resolved] [--severity critical|warning|info] [--url URL]
#
# 依赖：curl、uuidgen
# 退出码：
#   0 - 成功（HTTP 200）
#   1 - 参数错误
#   2 - 网络/HTTP 失败
#
# 示例：
#   ./scripts/send_test_alert.sh
#   ./scripts/send_test_alert.sh --status resolved --severity critical

set -euo pipefail

# ---------- 默认参数 ----------
STATUS="firing"
SEVERITY="warning"
URL="http://localhost:8000/api/v1/webhook/alertmanager"

# ---------- 用法说明 ----------
usage() {
    cat <<'USAGE'
用法：
  send_test_alert.sh [--status firing|resolved] [--severity critical|warning|info] [--url URL]

可选参数：
  --status    告警状态（firing 或 resolved），默认 firing
  --severity  告警严重度（critical / warning / info），默认 warning
  --url       AlertMind webhook 地址，默认 http://localhost:8000/api/v1/webhook/alertmanager
  -h, --help  打印此帮助信息并退出

退出码：
  0  成功（HTTP 2xx）
  1  参数错误
  2  网络 / HTTP 失败
USAGE
}

# ---------- 参数解析 ----------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --status)
            if [[ $# -lt 2 ]]; then
                echo "Error: --status 需要一个值（firing|resolved）" >&2
                usage >&2
                exit 1
            fi
            STATUS="$2"
            shift 2
            ;;
        --severity)
            if [[ $# -lt 2 ]]; then
                echo "Error: --severity 需要一个值（critical|warning|info）" >&2
                usage >&2
                exit 1
            fi
            SEVERITY="$2"
            shift 2
            ;;
        --url)
            if [[ $# -lt 2 ]]; then
                echo "Error: --url 需要一个值" >&2
                usage >&2
                exit 1
            fi
            URL="$2"
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

# ---------- 枚举值校验 ----------
case "$STATUS" in
    firing|resolved) ;;
    *)
        echo "Error: --status 仅支持 firing 或 resolved，当前为 '$STATUS'" >&2
        exit 1
        ;;
esac

case "$SEVERITY" in
    critical|warning|info) ;;
    *)
        echo "Error: --severity 仅支持 critical / warning / info，当前为 '$SEVERITY'" >&2
        exit 1
        ;;
esac

# ---------- 依赖检查 ----------
if ! command -v curl >/dev/null 2>&1; then
    echo "Error: 未找到 curl，请先安装" >&2
    exit 1
fi
if ! command -v uuidgen >/dev/null 2>&1; then
    echo "Error: 未找到 uuidgen，请先安装" >&2
    exit 1
fi

# ---------- 动态字段生成 ----------
# fingerprint：16 位小写 hex，每次运行都不同，避免与旧记录 upsert 冲突
FINGERPRINT="$(uuidgen | tr -d '-' | cut -c1-16 | tr '[:upper:]' '[:lower:]')"
STARTS_AT="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
if [[ "$STATUS" == "resolved" ]]; then
    ENDS_AT="$STARTS_AT"
else
    # Alertmanager 对 firing 告警的零值 endsAt
    ENDS_AT="0001-01-01T00:00:00Z"
fi

# groupKey 保证同一脚本不同次调用不聚合在一起
GROUP_KEY="{}/{alertname=\"TestAlert\"}:{alertname=\"TestAlert\",fingerprint=\"${FINGERPRINT}\"}"

# ---------- 打印发送摘要 ----------
echo "==> Sending test alert"
echo "    URL:         $URL"
echo "    Status:      $STATUS"
echo "    Severity:    $SEVERITY"
echo "    Fingerprint: $FINGERPRINT"
echo "    StartsAt:    $STARTS_AT"
echo "    EndsAt:      $ENDS_AT"
echo

# ---------- 构造 payload ----------
# 使用 printf 注入动态字段；其余内容保持纯文本，避免 shell 意外展开
PAYLOAD="$(printf '{
  "version": "4",
  "groupKey": "%s",
  "truncatedAlerts": 0,
  "status": "%s",
  "receiver": "alertmind",
  "groupLabels": {
    "alertname": "TestAlert"
  },
  "commonLabels": {
    "alertname": "TestAlert",
    "severity": "%s",
    "instance": "test-instance:9100",
    "job": "test-job"
  },
  "commonAnnotations": {
    "summary": "AlertMind send_test_alert.sh 生成的测试告警",
    "description": "这是一条由本地测试脚本生成的告警，用于验证 AlertMind webhook 链路"
  },
  "externalURL": "http://localhost:9093",
  "alerts": [
    {
      "status": "%s",
      "labels": {
        "alertname": "TestAlert",
        "severity": "%s",
        "instance": "test-instance:9100",
        "job": "test-job"
      },
      "annotations": {
        "summary": "AlertMind send_test_alert.sh 生成的测试告警",
        "description": "这是一条由本地测试脚本生成的告警，用于验证 AlertMind webhook 链路"
      },
      "startsAt": "%s",
      "endsAt": "%s",
      "generatorURL": "http://localhost:9090/graph",
      "fingerprint": "%s"
    }
  ]
}' "$GROUP_KEY" "$STATUS" "$SEVERITY" "$STATUS" "$SEVERITY" "$STARTS_AT" "$ENDS_AT" "$FINGERPRINT")"

# ---------- 发送请求 ----------
# --noproxy '*' 绕开 http_proxy 环境变量对 localhost 的干扰
RESPONSE="$(
    curl -sS --noproxy '*' \
        -X POST \
        -H 'Content-Type: application/json' \
        --data "$PAYLOAD" \
        -w '\n__HTTP_CODE__:%{http_code}\n' \
        "$URL"
)"

# 拆出 http_code 与 body
HTTP_CODE="$(printf '%s\n' "$RESPONSE" | awk -F':' '/^__HTTP_CODE__:/ {print $2}' | tr -d '[:space:]')"
BODY="$(printf '%s\n' "$RESPONSE" | sed '/^__HTTP_CODE__:/d')"

echo "HTTP $HTTP_CODE"
if command -v jq >/dev/null 2>&1 && printf '%s' "$BODY" | jq empty >/dev/null 2>&1; then
    printf '%s' "$BODY" | jq .
else
    # jq 缺失或 body 非 JSON：降级纯文本打印，不因此退出非 0
    printf '%s\n' "$BODY"
fi

# ---------- 状态码判定 ----------
if [[ -z "$HTTP_CODE" ]]; then
    echo "Failed: 无法解析 HTTP 状态码（可能是网络错误）" >&2
    exit 2
fi

if [[ "$HTTP_CODE" -lt 200 || "$HTTP_CODE" -ge 300 ]]; then
    echo "Failed: HTTP $HTTP_CODE" >&2
    exit 2
fi

exit 0
