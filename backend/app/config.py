from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置。环境变量前缀 ZY_，见 .env.example。"""

    model_config = SettingsConfigDict(env_prefix="ZY_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://zhiyuan:zhiyuan_dev@localhost:5433/zhiyuan"
    llm_gateway_url: str = "http://localhost:9000/v1"
    llm_model: str = "internal-chat"
    # API key 默认空 = 内网免鉴权网关；非空时带 Authorization: Bearer 头，
    # 任何 OpenAI 兼容公有云（DeepSeek / SiliconFlow …）都能直接接。
    llm_api_key: str = ""
    # embedding 可以走与 chat 不同的网关（例：chat 用 DeepSeek、embedding 用 SiliconFlow
    # 的 BAAI/bge-m3 —— DeepSeek 不提供 embedding）。留空 = 跟随主网关。
    # 注意维度钉死 1024（pgvector 列是 vector(1024)）：换维度必须重建 vec 列并
    # `reindex.py --embeddings --force` 全量回填，别只改这里。
    embedding_gateway_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = "bge-m3"
    embedding_dim: int = 1024
    webhook_secret: str = "change-me"

    # 复核触发去抖窗口（小时）与按需治理阈值，见 docs/design.md §4/§7
    review_debounce_hours: int = 24
    governance_usage_days: int = 90
    # 按需治理的「高风险标签」（逗号分隔）：命中任一即进人工复核队列，且优先级风险系数取 2。
    # design.md §4 只说「高风险标签」没冻结取值，具体哪些标签算高风险由团队自己配。
    high_risk_tags: str = "高风险"

    # 知识缺口的合并阈值（M3）：两条「没有找到答案」的问句词集合 Jaccard 达到多少算同一个需求。
    # 0.5 是折中值 —— 再低会把「vllm-ascend 部署」和「sglang 部署」并成一条，
    # 再高则同义改写（「PD 分离」/「Prefill Decode 分离」）合不上。判据细节见 services/gaps.py。
    gap_merge_similarity: float = 0.5

    # ---- 检索（M2，见 docs/design.md §5） ----
    # 两路召回各取多少条进 RRF。
    # RRF 的 k 越大越平滑：论文里的 60 是给 TREC 那种上千条的长列表用的，放在这里会把
    # rel 压成一条平线（第 1 名 30.0、第 10 名 29.5），相关度实际上失声、排序全由 trust
    # 说了算。我们两路各取 50 条，k=10 时 1 名 30 / 10 名 15 / 50 名 5，梯度才有意义。
    recall_limit: int = 50
    rrf_k: int = 10
    # 向量召回与 AI 摘要开关：auto = 探测可用性，用不了就自动降级；on = 强制（用不了就报错）；off = 关闭
    vector_search: str = "auto"
    ai_summary: str = "auto"

    # 检索路径的网关超时（秒）：查询 embedding 在搜索的同步路径上，超时必须短，
    # 否则一次网关抖动拖垮整页搜索。只有 embed 用它。
    llm_timeout: float = 6.0
    # 生成路径的网关超时（秒）：摘要/影响摘要/更新草稿/问答/缺口底稿都是 chat 生成，
    # 公有云生成一段中文普遍要 10–25s 且波动大（M5 实测 DeepSeek：同一问题 22s 与 30s+
    # 都出现过）。拿 6s 的检索超时去卡它们，只会频繁超时→熔断→连带把别的生成也降级掉；
    # 30s 也被实测打穿过一次，取 60。
    generation_timeout: float = 60.0
    # 网关不可达后的熔断静默期（秒）：期间直接降级，不再重试，避免每个请求都等一次超时
    llm_circuit_seconds: float = 60.0

    # ---- 问答（M5，见 docs/design.md §6） ----
    # 一条召回结果的 rel 分项（关键词+语义，0–30）低于此值就不算「命中」——阈值打在 rel
    # 而不是总分上：VERIFIED 的 trust +14 会把毫不相关的资产也抬过总分线。
    ask_min_rel: float = 5.0
    # 最多送进上下文的资产条数（正文全文截断见 services/ai.py）。
    ask_max_context: int = 5

    # ---- 看板（M5，见 docs/design.md §9） ----
    # 需求会话去重窗口（分钟）：同人同主题在窗口内的搜索/问答合并为一次需求。
    dashboard_session_minutes: int = 30
    # 重复探索工时估算系数（小时/次）：每次「重复需求」折算的平均排查耗时。估算值只看趋势。
    rework_hours_per_miss: float = 3.5


settings = Settings()
