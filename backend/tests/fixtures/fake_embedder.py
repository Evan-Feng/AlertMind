"""确定性 hash-based 512 维 Embedder 替身，用于单元测试。

设计目标：
- 相似输入（共享 keyword）→ 高 cosine 相似度 > 0.85
- 无关输入（keyword 不重叠）→ 低 cosine 相似度 < 0.3
- 同一输入两次调用返回完全相同向量

使用 hashlib.sha256 而非 Python 内置 hash()，保证任何环境（不同
PYTHONHASHSEED）下两次运行的输出完全一致，杜绝 CI 片状失败。

不模拟 bge 的语义理解能力（只有真 embed 才能），只保证
"字面共享 keyword 的文本会被聚合"这一 proxy 属性，够用来写
aggregator 的归并/新建/严重度/窗口等决策分支的确定性测试。

⚠️ 严禁引入 random / 时间戳 / 任何非确定性元素。

相似度设计原理：
  文本先分词，再对每个 token 用正则拆出纯字母子片段（alpha parts），
  过滤掉纯数字片段（高熵、无业务语义）。

  例如：
    "HighCPU on node-1" → alpha parts: {highcpu, on, node}  (1 被过滤)
    "HighCPU on node-2" → alpha parts: {highcpu, on, node}  (2 被过滤)
    cosine sim = 1.0

    "DiskFull on db-1"  → alpha parts: {diskfull, on, db}   (1 被过滤)
    "HighCPU on node-1" → alpha parts: {highcpu, on, node}
    共享 {on}  / 总量各 3 → cos ≈ 0.33（低于 0.3 只要实例名不同即可）

  这样同名告警（仅实例编号不同）总是高相似度；完全不同告警低相似度。
  数字过滤是合理的 proxy：真实 bge 模型也对"node-1"和"node-2"的语义差异不敏感。
"""

from __future__ import annotations

import hashlib
import math
import re


def _sha256_index(text: str, bucket: int = 100) -> int:
    """用 SHA-256 把任意字符串映射到 [0, bucket) 范围内的稳定整数索引。

    :param text: 任意字符串
    :param bucket: 取模的桶大小（默认 100，对应向量前 100 维）
    :returns: [0, bucket) 内的整数
    """
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    value = int.from_bytes(digest[:4], byteorder="big", signed=False)
    return value % bucket


def _extract_alpha_tokens(text: str) -> set[str]:
    """从文本中提取「纯字母子片段」集合，过滤掉纯数字 token。

    处理流程：
    1. 小写化
    2. 按空白分词
    3. 对每个词用 ``[a-z]+`` 匹配所有字母片段
    4. 过滤长度 < 2 的片段（单字母噪音）

    目的：让 "node-1" 和 "node-2" 都只贡献 "node"，
    消除实例编号带来的无意义差异，使同名告警 cosine sim 趋近 1.0。

    :param text: 原始文本
    :returns: 过滤后的字母 token 集合（无序）
    """
    tokens: set[str] = set()
    for word in text.lower().split():
        # 拆出所有连续字母片段
        parts = re.findall(r"[a-z]+", word)
        for part in parts:
            if len(part) >= 2:  # 过滤单字母噪音（如连字符周围的 'a', 's'）
                tokens.add(part)
    return tokens


class FakeEmbedder:
    """确定性 hash-based 512 维 Embedder 替身，用于单元测试。

    每个字母 token 通过 SHA-256 映射到 512 维向量的前 100 维中的某个位置
    （value=1.0），向量归一化为单位向量。

    相似文本（共享字母 token）的归一化向量点积（cosine sim）会较高；
    完全不相关的文本（无公共字母 token）cosine sim 会接近 0。

    :ivar model_name: 固定字符串，兼容 aggregator 写回 embedding_model 字段
    """

    model_name: str = "fake-embedder-v1"

    def embed(self, text: str) -> list[float]:
        """计算文本的确定性 512 维向量。

        :param text: 告警文本；不可为空串或全空白
        :returns: 512 维归一化向量（L2 norm ≈ 1.0）
        :raises ValueError: text 为空或全空白
        """
        if not text or not text.strip():
            raise ValueError("FakeEmbedder received empty text")

        tokens = _extract_alpha_tokens(text)

        # 若过滤后没有有效 token（例如全数字文本），回退到使用原始分词
        if not tokens:
            for word in text.lower().split():
                if word:
                    tokens.add(word)

        vec = [0.0] * 512
        for token in tokens:
            idx = _sha256_index(token)
            vec[idx] = 1.0

        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]

        return vec


__all__ = ["FakeEmbedder"]
