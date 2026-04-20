"""Embedding 计算工具。

对 ``sentence-transformers`` 的轻量包装，MVP 只支持 ``BAAI/bge-small-zh-v1.5``
（512 维），CPU 推理。模型实例在进程内全局缓存，避免重复加载。

本模块提供两个核心导出：

- :class:`Embedder`：封装模型加载与单条 ``embed`` 调用。
- :func:`build_alert_text`：把告警（dict 或 ORM 对象）归一化为用于 embedding 的
  中文字符串，负责高熵标签过滤与稳定排序。

聚合逻辑本身不在本模块范围内（见阶段 3 Wave 3 ``core/aggregator.py``）。
"""

from __future__ import annotations

from threading import Lock
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from loguru import logger

from alertmind.config import settings

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer


@runtime_checkable
class AlertLike(Protocol):
    """结构化告警的 duck typing 协议。

    用于给 :func:`build_alert_text` 的类型检查提供依据；凡具备
    ``alertname`` / ``labels`` / ``annotations`` 三个属性的对象均满足。
    """

    alertname: str
    labels: dict[str, Any]
    annotations: dict[str, Any]


# 进程级模型缓存。key 为模型名，value 为已加载的 SentenceTransformer 实例。
# 用锁保证多线程/多协程首次加载时的幂等性。
_MODEL_CACHE: dict[str, SentenceTransformer] = {}
_MODEL_LOCK: Lock = Lock()


def _get_model(name: str) -> SentenceTransformer:
    """按模型名获取（必要时加载）全局缓存的 SentenceTransformer 实例。

    :param name: HuggingFace 模型名，例如 ``BAAI/bge-small-zh-v1.5``。
    :returns: 已就绪的 ``SentenceTransformer`` 实例。
    :raises Exception: 若模型下载/加载失败，原样抛出（fail fast，让 lifespan 终止）。
    """
    cached = _MODEL_CACHE.get(name)
    if cached is not None:
        return cached
    with _MODEL_LOCK:
        cached = _MODEL_CACHE.get(name)
        if cached is not None:
            return cached
        # 惰性 import：避免在仅 import 本模块（例如类型检查/文档生成）时
        # 触发 sentence-transformers 的重型加载。
        from sentence_transformers import SentenceTransformer

        logger.info("loading sentence-transformers model={}", name)
        model: SentenceTransformer = SentenceTransformer(name, device="cpu")
        _MODEL_CACHE[name] = model
        logger.info("sentence-transformers model loaded: {}", name)
        return model


class Embedder:
    """bge-small-zh-v1.5 的轻量包装。

    设计：

    - 同一进程内所有 ``Embedder`` 实例共享底层 ``SentenceTransformer``
      （通过模块级缓存），避免多次加载；也便于测试时 monkeypatch。
    - 本类是线程 / 协程安全的（``SentenceTransformer.encode`` 内部持锁）。
    - 模型 forward 在 CPU 上跑；MVP 不考虑 GPU（与 PRD 一致）。
    """

    def __init__(self, model_name: str | None = None) -> None:
        """初始化 Embedder。

        :param model_name: 若为空则读取 ``settings.embedding_model``。
        """
        self._model_name: str = model_name or settings.embedding_model
        # 立即解析模型，保证初始化完成即可用（配合 lifespan fail fast）。
        self._model: SentenceTransformer = _get_model(self._model_name)

    @property
    def model_name(self) -> str:
        """返回当前使用的模型名，如 ``'BAAI/bge-small-zh-v1.5'``。"""
        return self._model_name

    def embed(self, text: str) -> list[float]:
        """计算单条文本的 Embedding。

        :param text: 已拼接好的告警文本；不可为空串或全空白。
        :returns: 512 维 Python ``list[float]``，已做 L2 归一化（可直接做
            cosine similarity = dot product）。
        :raises ValueError: ``text`` 为空或全空白。
        """
        if not text or not text.strip():
            raise ValueError("embed() received empty text")
        vector = self._model.encode(
            text,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return vector.tolist()  # type: ignore[no-any-return]


def _extract_field(alert: dict[str, Any] | AlertLike, key: str) -> Any:
    """从 dict 或 ORM 对象中取同名字段，dict 优先。"""
    if isinstance(alert, dict):
        return alert.get(key)
    return getattr(alert, key, None)


def build_alert_text(
    alert: dict[str, Any] | AlertLike,
    drop_labels: list[str] | None = None,
) -> str:
    """把 alert 归一化成用于 embedding 的中文字符串。

    拼接格式（决策 B）::

        告警：{alertname}
        标签：{k=v, k=v, ...}
        摘要：{summary}

    规则：

    - ``alertname`` 缺失时用 ``'unknown'``。
    - ``labels`` 过滤掉 ``drop_labels`` 中的 key；``drop_labels`` 为空时
      读取 ``settings.embedding_drop_labels``。
    - ``labels`` 按 key 字母序稳定排序，保证同一 alert 每次拼接结果一致
      （便于缓存 / 测试断言）。
    - ``annotations.summary`` 缺失时，该行整行省略（不写 ``'摘要：None'``）；
      ``annotations.description`` 作为 summary 缺失时的兜底。

    :param alert: 支持两种输入形态：

        - ``dict``，至少包含 ``alertname`` / ``labels`` / ``annotations`` 三个 key
        - Alert ORM 实例（或任何具备 ``.alertname`` / ``.labels`` / ``.annotations``
          属性的对象，见 :class:`AlertLike`）
    :param drop_labels: 覆盖默认的高熵 label 过滤列表；传 ``[]`` 表示不过滤任何标签。
    :returns: 拼接后的字符串；保证非空（最坏情况至少包含 ``'告警：unknown'``）。
    """
    effective_drop = (
        list(drop_labels) if drop_labels is not None else list(settings.embedding_drop_labels)
    )
    drop_set = {key.lower() for key in effective_drop}

    alertname_value = _extract_field(alert, "alertname")
    alertname = str(alertname_value).strip() if alertname_value else ""
    if not alertname:
        alertname = "unknown"

    labels_raw = _extract_field(alert, "labels") or {}
    if not isinstance(labels_raw, dict):
        labels_raw = {}
    # alertname 不应重复出现在 labels 行；过滤掉高熵 key
    filtered_labels: list[tuple[str, str]] = []
    for key in sorted(labels_raw.keys()):
        if key.lower() == "alertname":
            continue
        if key.lower() in drop_set:
            continue
        value = labels_raw[key]
        if value is None:
            continue
        filtered_labels.append((str(key), str(value)))

    annotations_raw = _extract_field(alert, "annotations") or {}
    if not isinstance(annotations_raw, dict):
        annotations_raw = {}
    summary = annotations_raw.get("summary") or annotations_raw.get("description")
    summary_text = str(summary).strip() if summary else ""

    lines: list[str] = [f"告警：{alertname}"]
    if filtered_labels:
        label_str = ", ".join(f"{k}={v}" for k, v in filtered_labels)
        lines.append(f"标签：{label_str}")
    if summary_text:
        lines.append(f"摘要：{summary_text}")
    return "\n".join(lines)


__all__ = ["AlertLike", "Embedder", "build_alert_text"]
