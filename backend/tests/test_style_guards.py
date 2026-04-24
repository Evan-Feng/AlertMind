"""测试风格守门员：防止未来测试引入时间炸弹等反模式。

本文件的测试不依赖任何外部 fixture，只做静态代码扫描。

当前守门员：
- ``test_no_absolute_datetime_literals_in_fixtures``：
  禁止在 ``backend/tests/**/*.py`` 里出现 ``datetime(20XX, M, D, ...)`` 或
  ``"20XX-MM-DDT..."`` 的绝对日期字面量。aggregator 等模块基于相对 ``now()`` 的
  时间窗口计算，绝对日期会随测试执行日期推移超出窗口，形成"时间炸弹"。

例外机制：
- 以 ``#`` 开头的注释行不触发（便于在注释中解释反模式）。
- 本文件自身不扫描（正则 pattern 本身需包含示例字符串）。
- ``tests/fixtures/*.json`` 里的绝对日期由 ``conftest.py::sample_payloads``
  fixture 动态替换，守门员只扫描 ``.py`` 文件，互不重叠。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent


def test_no_absolute_datetime_literals_in_fixtures() -> None:
    """禁止测试代码中硬编码绝对日期作为 fixture 锚点（时间炸弹反模式）。

    正确做法：``datetime.now(UTC) - timedelta(...)`` 构造相对锚点。
    详见 CLAUDE.md「测试风格硬约束」段。
    """
    # 匹配 datetime(YYYY, M, D, ...)，YYYY ∈ {2024..2030}
    date_pattern = re.compile(r"datetime\(20(2[4-9]|30),\s*\d+,\s*\d+")
    # 匹配 "YYYY-MM-DDT..."，YYYY ∈ {2024..2030}
    iso_date_pattern = re.compile(r'"20(2[4-9]|30)-\d{2}-\d{2}T')

    offenders: list[tuple[str, int, str]] = []

    for py_file in TESTS_DIR.rglob("*.py"):
        # 本文件自身不扫描（它的 regex pattern 含示例字符串会自触发）
        if py_file.name == "test_style_guards.py":
            continue

        content = py_file.read_text(encoding="utf-8")
        for lineno, line in enumerate(content.splitlines(), start=1):
            stripped = line.strip()
            # 跳过 # 注释行（便于在注释中解释反模式）
            if stripped.startswith("#"):
                continue

            if date_pattern.search(line) or iso_date_pattern.search(line):
                rel_path = py_file.relative_to(TESTS_DIR).as_posix()
                offenders.append((rel_path, lineno, stripped))

    if offenders:
        msg_lines = [
            "禁止在测试代码中硬编码绝对日期（时间炸弹反模式）：",
            *[f"  tests/{path}:{ln}: {text}" for path, ln, text in offenders],
            "",
            "修复方式：改用 `datetime.now(UTC) - timedelta(hours=1)` 或类似相对锚点。",
            "详见 CLAUDE.md「测试风格硬约束」段与 ADR-009 聚合窗口语义。",
        ]
        pytest.fail("\n".join(msg_lines))
