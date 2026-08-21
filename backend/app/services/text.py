"""中文分词与检索文本处理（M2）。

PG 的内置分词器不认中文词边界，所以走「jieba 预分词」路线：入库前把标题/标签/摘要/正文
切成空格分隔的词串，PG 侧只用 simple 配置做 to_tsvector（见 AssetSearchDoc 与迁移里的
tsv 生成列）。选 jieba 而不是 zhparser：zhparser 是需要编译安装的 PG 扩展，本项目的
Windows 开发库（scripts/devdb.ps1 起的 pgserver）装不了；jieba 是纯 Python，PG 与
sqlite 两条路都能用同一份分词结果，测试不必依赖 PG。
"""
from __future__ import annotations

import logging
import re

import jieba

# jieba 首次分词会往 stderr 打「Building prefix dict…」，会混进 uvicorn 日志，关掉。
jieba.setLogLevel(logging.WARNING)

# 只保留中日韩文字、拉丁字母、数字、点号（版本号 v0.10.0rc1）与下划线；其余一律当分隔符。
_KEEP = re.compile(r"[^\w一-鿿.]+", re.UNICODE)
_CJK = re.compile(r"[一-鿿]")

# 技术原子词：版本号与带连接符的标识符。jieba 会把 v0.10.0rc1 切成 v0.10 / 0rc1、
# 把 max_num_batched_tokens 切成四段，而这两类恰恰是本领域最该整体命中的东西
# （版本区间命中在排序里是独立加分项）。所以先用正则把它们整体抠出来，再叠加 jieba 的碎片：
# 整体形式保证精确命中，碎片保证模糊召回。
_ATOMIC = re.compile(r"[a-z][a-z0-9]*(?:[-_.][a-z0-9]+)+|v?\d+(?:\.\d+)+[a-z0-9.]*")

# 停用词：中文虚词 + 英文冠词/介词 + 纯符号残渣。列表刻意短 —— 技术查询里几乎不出现，
# 删多了反而会伤到「在 NPU 上」这类含义词。
_STOPWORD_TEXT = (
    "的 了 和 与 或 是 在 有 被 把 对 从 到 就 都 也 而 及 等 这 那 一个 一些 可以 如何 怎么 什么 为什么"
    " a an the is are was were be of to in on for with and or by at as it its this that how what why"
)
STOPWORDS = frozenset(_STOPWORD_TEXT.split())

# 高亮词最短长度：与 prototype 的 highlight(text, terms) 一致（terms.filter(t=>t.length>=2)），
# 单字命中会把正文标得到处都是。
HIGHLIGHT_MIN_LEN = 2


def tokenize(text: str) -> list[str]:
    """切词并归一化：小写、去标点、去停用词。保留重复（词频是关键词打分的一部分）。"""
    if not text:
        return []
    lowered = text.lower()
    out: list[str] = [m.group(0).strip(".") for m in _ATOMIC.finditer(lowered)]
    cleaned = _KEEP.sub(" ", lowered)
    for tok in jieba.cut_for_search(cleaned):
        tok = tok.strip(" .")
        if not tok or tok in STOPWORDS:
            continue
        # 单个拉丁字母/数字没有检索价值（中文单字有，比如「图」「核」）
        if len(tok) == 1 and not _CJK.match(tok):
            continue
        out.append(tok)
    return out


def index_text(text: str) -> str:
    """索引侧：切词后用空格拼回去，交给 PG 的 simple 配置或可移植路径。"""
    return " ".join(tokenize(text))


def query_terms(q: str) -> list[str]:
    """查询侧：切词并去重保序。空查询返回空列表（浏览模式）。"""
    seen: dict[str, None] = {}
    for tok in tokenize(q):
        seen.setdefault(tok, None)
    return list(seen)


def highlight_terms(terms: list[str]) -> list[str]:
    """返回给前端做 <mark> 的词：滤掉单字，避免整段被标黄。"""
    return [t for t in terms if len(t) >= HIGHLIGHT_MIN_LEN]
