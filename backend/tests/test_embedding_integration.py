"""真实 bge-small-zh-v1.5 模型 sanity test。

验证：
- embed 返回 512 维向量
- 语义相似文本的 cosine similarity > 0.6（保守下限）
- 语义相似对的 cosine sim > 语义无关对

不跳过（不加 @pytest.mark.skip / @pytest.mark.integration），
pyproject.toml 默认全跑。CI 有 HuggingFace 缓存，首次下载后不需要重新加载。
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session")
def real_embedder():
    """加载真实 bge-small-zh-v1.5 模型（session scope，整个测试运行只加载一次）。

    进程内模块级缓存（_MODEL_CACHE）保证不会重复加载。
    """
    from alertmind.utils.embedding import Embedder

    return Embedder()


def test_bge_model_produces_512_dim_and_semantic_similarity(real_embedder) -> None:
    """真实 bge 模型的 sanity 检查：512 维 + 语义相似度正确。

    相似文本（CPU 相关告警）的 cosine sim 应 > 0.6（保守下限）。
    无关文本（证书过期 vs CPU 告警）的 cosine sim 应低于相似对。
    """
    # Arrange
    text_cpu_1 = "节点 CPU 使用率超过 90%"
    text_cpu_2 = "CPU 使用率过高告警"
    text_cert = "证书即将过期"

    # Act
    v1 = real_embedder.embed(text_cpu_1)
    v2 = real_embedder.embed(text_cpu_2)
    v3 = real_embedder.embed(text_cert)

    # Assert: 维度
    assert len(v1) == 512, f"向量维度应=512，实际={len(v1)}"
    assert len(v2) == 512
    assert len(v3) == 512

    # Assert: cosine similarity（已归一化，dot product = cosine sim）
    cos_12 = sum(a * b for a, b in zip(v1, v2, strict=True))
    cos_13 = sum(a * b for a, b in zip(v1, v3, strict=True))

    assert cos_12 > 0.6, f"CPU 相关文本的 cosine sim 应 > 0.6（保守下限），实际 cos_12={cos_12:.4f}"
    assert cos_12 > cos_13, f"相似对 cos_sim({cos_12:.4f}) 应大于无关对 cos_sim({cos_13:.4f})"
