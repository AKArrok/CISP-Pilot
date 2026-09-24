"""掌握度画像模型（Beta 后验 / Elo / 遗忘曲线）的纯函数测试。"""
import pytest

from quiz.stats import (
    _beta_mean,
    _beta_p_below,
    _confidence,
    _elo_expected,
    _memory_strength,
    domain_profiles,
)


def test_beta_mean_is_accuracy_with_prior():
    # 5 对 1 错 + 先验 1:1 → (1+1)/(7+2) = 0.333
    assert _beta_mean(1 + 1, 1 + 1) == pytest.approx(0.5)
    assert _beta_mean(5 + 1, 1 + 1) == pytest.approx(6 / 8)


def test_beta_p_below_threshold():
    # 连续答错 3 题：P(能力<0.6) 很高 → 判薄弱
    assert _beta_p_below(1 + 1, 3 + 1, 0.6) > 0.9
    # 答对 1 错 1：P(能力<0.6) 不该超过 0.7 阈值
    assert _beta_p_below(1 + 1, 1 + 1, 0.6) < 0.7


def test_confidence_grows_with_samples():
    low = _confidence(2, 2)
    high = _confidence(20, 20)
    assert high > low  # 样本越多置信度越高
    assert 0.0 <= low <= 1.0


def test_elo_expected_between_0_and_1():
    assert 0 < _elo_expected(1500, 1500) < 1
    assert _elo_expected(1500, 1500) == pytest.approx(0.5)
    assert _elo_expected(2000, 1500) > 0.7  # 高 rating 期望获胜概率高


def test_memory_strength_decays():
    assert _memory_strength(0) == 1.0
    assert _memory_strength(7) == pytest.approx(0.5)   # 一个半衰期 → 0.5
    assert _memory_strength(14) == pytest.approx(0.25)  # 两个半衰期 → 0.25
    assert _memory_strength(100) < 0.01


def _fake_attempts(now: float) -> list[tuple[str, int, float]]:
    """构造一小时内连续答错的域 + 7 天前答对的域，验证画像各字段。"""
    hour_ago = now - 3600
    week_ago = now - 7 * 86400
    return [
        ("薄弱域", 0, hour_ago),
        ("薄弱域", 0, hour_ago - 10),
        ("薄弱域", 0, hour_ago - 20),
        ("复习域", 1, week_ago),
        ("复习域", 1, week_ago - 10),
        ("复习域", 1, week_ago - 20),
    ]


def test_domain_profiles_full_shape(monkeypatch):
    now = 1_800_000_000.0
    monkeypatch.setattr(
        "quiz.stats.repos.attempt_rows", lambda _thread_id: _fake_attempts(now)
    )
    monkeypatch.setattr("quiz.stats.repos.signal_rows", lambda _thread_id: [])
    profiles = domain_profiles("thread-stats", now=now)

    # 薄弱域：连续答错 3 次，后验概率高 → weak
    weak = profiles["薄弱域"]
    assert weak["attempts"] == 3
    assert weak["correct"] == 0
    assert weak["weak"] is True
    assert weak["insufficient"] is False
    assert weak["mastery"] < 0.4
    assert weak["streak"] == 0
    assert weak["due_for_review"] is False          # 一小时前刚答，未到期

    # 复习域：全对但 7 天没答 → 记忆衰减到期（恰好一个半衰期，强度=0.5 判到期）
    review = profiles["复习域"]
    assert review["correct"] == 3
    assert review["weak"] is False
    assert review["due_for_review"] is True
    assert review["memory_strength"] == pytest.approx(0.5)
    assert review["last_seen_days"] == pytest.approx(7.0)


def test_elo_rises_with_correct_answers():
    # 直接验证 Elo 更新逻辑：连续答对 rating 升
    from quiz.stats import _elo_expected
    rating = 1500.0
    for _ in range(5):
        opp = 1500.0
        expected = _elo_expected(rating, opp)
        rating += 32 * (1.0 - expected)
    assert rating > 1500  # 答对越多 rating 越高
