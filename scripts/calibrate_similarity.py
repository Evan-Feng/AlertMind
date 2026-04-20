"""阶段 3 Wave 2.5 阈值校准脚本（一次性工具）。

用法::

    cd backend && uv run python ../scripts/calibrate_similarity.py

读取 ``backend/tests/fixtures/similarity_calibration.json``，
对每一对告警计算 bge-small-zh-v1.5 的 cosine similarity，打印表格。

输出目的：人工判断默认阈值 0.85 是否落在 "相同事件" 和 "无关事件" 之间。
本脚本不做 PASS/FAIL 判定，输出供工程师肉眼 review。
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

from alertmind.utils.embedding import Embedder, build_alert_text


def _find_fixture() -> Path:
    """定位 fixture 文件；兼容从 backend/ 或项目根目录执行。"""
    here = Path(__file__).resolve().parent
    candidates = [
        here.parent / "backend" / "tests" / "fixtures" / "similarity_calibration.json",
        Path.cwd() / "tests" / "fixtures" / "similarity_calibration.json",
        Path.cwd() / "backend" / "tests" / "fixtures" / "similarity_calibration.json",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "similarity_calibration.json not found; tried: " + ", ".join(str(c) for c in candidates)
    )


_LABEL_TRANSLATE = {
    "same": "相同事件",
    "unrelated": "无关事件",
    "ambiguous": "模糊边界",
}


def main() -> int:
    """执行一轮相似度校准并把结果打印到 stdout。"""
    fixture_path = _find_fixture()
    print(f"[calibrate] loading fixture: {fixture_path}")
    with fixture_path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    pairs = data.get("pairs", [])
    if not pairs:
        print("[calibrate] fixture contains no pairs", file=sys.stderr)
        return 1

    print("[calibrate] initializing embedder (first run will download ~100MB model)")
    embedder = Embedder()
    print(f"[calibrate] model loaded: {embedder.model_name}")
    print()

    group_scores: dict[str, list[float]] = defaultdict(list)

    header = f"{'label':<8} | {'sim':>6} | pair"
    separator = "-" * len(header) + "----------------------------------"
    print(header)
    print(separator)

    for idx, pair in enumerate(pairs, start=1):
        label = pair.get("label", "?")
        alert_a = pair["a"]
        alert_b = pair["b"]
        text_a = build_alert_text(alert_a)
        text_b = build_alert_text(alert_b)
        vec_a = np.asarray(embedder.embed(text_a), dtype=np.float64)
        vec_b = np.asarray(embedder.embed(text_b), dtype=np.float64)
        # embed() 已 L2 归一化，余弦相似度等价于点积
        similarity = float(np.dot(vec_a, vec_b))
        group_scores[label].append(similarity)
        name_a = alert_a.get("alertname", "unknown")
        name_b = alert_b.get("alertname", "unknown")
        print(f"{label:<8} | {similarity:6.4f} | #{idx:02d} {name_a} <-> {name_b}")

    print()
    print("===== 分组均值 =====")
    for label_key in ("same", "unrelated", "ambiguous"):
        scores = group_scores.get(label_key, [])
        if not scores:
            continue
        zh = _LABEL_TRANSLATE.get(label_key, label_key)
        mean = sum(scores) / len(scores)
        lo = min(scores)
        hi = max(scores)
        print(
            f"{zh}（{label_key}, n={len(scores)}）：均值 {mean:.4f} | min {lo:.4f} | max {hi:.4f}"
        )

    print()
    print("[calibrate] done. 人工判断默认阈值 0.85 是否合理；本脚本不返回 PASS/FAIL。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
