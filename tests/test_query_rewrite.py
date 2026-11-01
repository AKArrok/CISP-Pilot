import config
from retrieval.query_rewrite import rewrite_query


def test_gold_baseline_retrieval_defaults():
    assert config.DENSE_K == 10
    assert config.SPARSE_K == 20
    assert config.RRF_K == 20
    assert config.ENABLE_QUERY_REWRITE
    assert config.ENABLE_FEATURE_RERANKING
    # 外部精排当前不可用，用户选择默认仅用 embedding + 本地检索排序。
    assert not config.ENABLE_RERANKING
    assert config.RERANK_BACKEND == "dashscope"
    assert config.RERANK_FUSION == "alpha"
    assert config.RERANK_FUSION_ALPHA == 0.5


def test_rewrite_network_security_law_date_query():
    rewritten = rewrite_query("《网络安全法》从什么时候开始实施？")

    assert "网络安全法" in rewritten
    assert "实施日期" in rewritten
    assert "施行" in rewritten
    assert "网络运行安全" in rewritten


def test_rewrite_leaves_unrelated_query_unchanged():
    query = "AES支持哪三种密钥长度？"

    assert rewrite_query(query) == query


def test_rewrite_common_security_acronyms():
    assert "证书撤销列表" in rewrite_query("PKI中如何查询已经被撤销的证书？")
    assert "灾难恢复需求分析" in rewrite_query("业务影响分析BIA主要分析什么？")


def test_rewrite_eal_level_query():
    rewritten = rewrite_query("CC标准中的评估保证级EAL如何划分？")

    assert "EAL1" in rewritten
    assert "EAL7" in rewritten
    assert "保护轮廓" in rewritten
