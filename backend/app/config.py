from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置。环境变量前缀 ZY_，见 .env.example。"""

    model_config = SettingsConfigDict(env_prefix="ZY_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://zhiyuan:zhiyuan_dev@localhost:5433/zhiyuan"
    llm_gateway_url: str = "http://localhost:9000/v1"
    llm_model: str = "internal-chat"
    embedding_model: str = "bge-m3"
    embedding_dim: int = 1024
    webhook_secret: str = "change-me"

    # 复核触发去抖窗口（小时）与按需治理阈值，见 docs/design.md §4/§7
    review_debounce_hours: int = 24
    governance_usage_days: int = 90

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

    # LLM 网关调用超时（秒）。检索是同步路径，超时必须短，否则一次网关抖动拖垮整页搜索。
    llm_timeout: float = 6.0
    # 网关不可达后的熔断静默期（秒）：期间直接降级，不再重试，避免每个请求都等一次超时
    llm_circuit_seconds: float = 60.0


settings = Settings()
