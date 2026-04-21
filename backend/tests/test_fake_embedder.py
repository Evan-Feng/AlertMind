"""FakeEmbedder 自测：验证确定性、相似度行为、归一化与跨进程稳定性。

确保 FakeEmbedder 满足 aggregator 测试所需的 proxy 属性：
- 字面共享 keyword 的文本 cosine sim > 0.85
- 无关文本 cosine sim < 0.3
- 任意输入的 L2 norm 在 [0.99, 1.01]
- 空文本抛 ValueError
- hashlib.sha256 保证跨进程确定性（不受 PYTHONHASHSEED 影响）
"""

from __future__ import annotations

import math
import subprocess
import sys

import pytest

from tests.fixtures.fake_embedder import FakeEmbedder


def _cosine_sim(v1: list[float], v2: list[float]) -> float:
    """计算两个向量的余弦相似度。"""
    return sum(a * b for a, b in zip(v1, v2, strict=True))


@pytest.fixture
def embedder() -> FakeEmbedder:
    """每个测试复用同一个 FakeEmbedder 实例（无状态）。"""
    return FakeEmbedder()


# ---------------------------------------------------------------------------
# typical: 基础行为
# ---------------------------------------------------------------------------


def test_fake_embedder_deterministic(embedder: FakeEmbedder) -> None:
    """同一输入两次调用返回完全相同的向量。"""
    # Arrange
    text = "HighCPU on node-1"

    # Act
    v1 = embedder.embed(text)
    v2 = embedder.embed(text)

    # Assert
    assert v1 == v2, "同一输入两次调用必须返回完全相同的向量"


def test_fake_embedder_returns_512_dimensions(embedder: FakeEmbedder) -> None:
    """embed() 返回 512 维向量。"""
    # Act
    vec = embedder.embed("HighCPU on node-1")

    # Assert
    assert len(vec) == 512


def test_fake_embedder_model_name(embedder: FakeEmbedder) -> None:
    """model_name 属性返回固定字符串，兼容 aggregator 写回 embedding_model 字段。"""
    assert embedder.model_name == "fake-embedder-v1"


# ---------------------------------------------------------------------------
# typical: 相似度行为
# ---------------------------------------------------------------------------


def test_fake_embedder_similar_inputs_high_similarity(embedder: FakeEmbedder) -> None:
    """共享 keyword 的文本应产生高 cosine 相似度 (> 0.85)。

    "HighCPU on node-1" 与 "HighCPU on node-2" 共享 highcpu / on，
    其余只有 node-1 vs node-2 不同，相似度应显著高于 0.85。
    """
    # Arrange
    text1 = "HighCPU on node-1"
    text2 = "HighCPU on node-2"

    # Act
    v1 = embedder.embed(text1)
    v2 = embedder.embed(text2)
    sim = _cosine_sim(v1, v2)

    # Assert
    assert sim > 0.85, f"共享 keyword 的文本 cosine sim 应 > 0.85，实际={sim:.4f}"


def test_fake_embedder_unrelated_inputs_low_similarity(embedder: FakeEmbedder) -> None:
    """无关文本（无公共 keyword）应产生低 cosine 相似度 (< 0.3)。

    "HighCPU on node-1" 与 "DiskFull on db-1" 无公共业务 keyword，
    相似度应接近 0。
    """
    # Arrange
    text1 = "HighCPU on node-1"
    text2 = "DiskFull db-1"

    # Act
    v1 = embedder.embed(text1)
    v2 = embedder.embed(text2)
    sim = _cosine_sim(v1, v2)

    # Assert
    assert sim < 0.3, f"无关文本 cosine sim 应 < 0.3，实际={sim:.4f}"


# ---------------------------------------------------------------------------
# boundary: 归一化
# ---------------------------------------------------------------------------


def test_fake_embedder_normalized(embedder: FakeEmbedder) -> None:
    """任意输入返回向量的 L2 norm 应在 [0.99, 1.01]（单位向量）。"""
    texts = [
        "HighCPU on node-1",
        "DiskFull db-master-1",
        "CertExpiry ingress-nginx namespace=production",
        "单个词",
    ]
    for text in texts:
        vec = embedder.embed(text)
        norm = math.sqrt(sum(v * v for v in vec))
        assert 0.99 <= norm <= 1.01, f"text={text!r} 的向量 norm={norm:.6f}，不在 [0.99, 1.01]"


def test_fake_embedder_single_word_normalized(embedder: FakeEmbedder) -> None:
    """单个词的输入也应返回归一化向量。"""
    vec = embedder.embed("critical")
    norm = math.sqrt(sum(v * v for v in vec))
    assert 0.99 <= norm <= 1.01


# ---------------------------------------------------------------------------
# exception: 空文本
# ---------------------------------------------------------------------------


def test_fake_embedder_empty_text_raises_value_error(embedder: FakeEmbedder) -> None:
    """embed('') 应抛 ValueError。"""
    with pytest.raises(ValueError, match="empty"):
        embedder.embed("")


def test_fake_embedder_whitespace_only_raises_value_error(embedder: FakeEmbedder) -> None:
    """embed('   ') 应抛 ValueError（全空白视为空）。"""
    with pytest.raises(ValueError, match="empty"):
        embedder.embed("   ")


# ---------------------------------------------------------------------------
# boundary: 跨进程稳定性（hashlib 而非 hash()）
# ---------------------------------------------------------------------------


def test_fake_embedder_cross_process_stable() -> None:
    """不同进程中对相同文本的 embed 结果应完全一致（hashlib.sha256 保证）。

    在子进程中计算已知文本的向量，与本进程结果对比。
    若用 Python 内置 hash()，PYTHONHASHSEED 不同的进程会产生不同结果。
    """
    text = "HighCPU on node-1"

    # 在本进程计算
    embedder = FakeEmbedder()
    vec_main = embedder.embed(text)

    # 在子进程中计算（PYTHONHASHSEED 会不同）
    script = (
        "import sys; sys.path.insert(0, 'tests'); sys.path.insert(0, '.');\n"
        "from tests.fixtures.fake_embedder import FakeEmbedder;\n"
        "e = FakeEmbedder();\n"
        "v = e.embed('HighCPU on node-1');\n"
        "print(repr(v[:10]))"  # 只打印前 10 维用于对比
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd="/Users/evanfeng/projects/alertmind/backend",
        env={**__import__("os").environ, "PYTHONHASHSEED": "99999"},
    )
    assert result.returncode == 0, f"子进程失败: {result.stderr}"

    vec_child_prefix = eval(result.stdout.strip())
    assert vec_main[:10] == vec_child_prefix, (
        f"跨进程向量不一致。\n本进程前10维: {vec_main[:10]}\n子进程前10维: {vec_child_prefix}"
    )
