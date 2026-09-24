"""题目主知识点人工复核覆盖层。

自动标题匹配容易被题干中的流程背景和选项文本误导。这里保留少量可追溯的
人工主标签覆盖；未列出的题目仍使用启发式标签，并继续标记为 candidate。
"""
from __future__ import annotations

import hashlib


REVIEW_VERSION = "manual-tag-v1-2026-09-12"

# (question_id) -> (domain, canonical knowledge-point name, reason)
MANUAL_PRIMARY = {
    "51CTO_一套_80": ("业务连续性", "灾难恢复策略实现", "题目问策略实现阶段的排除项"),
    "700题_b1_83": ("业务连续性", "灾难恢复策略实现", "题目问策略实现阶段的排除项"),
    "700题_b1_378": ("业务连续性", "业务连续性管理基础", "题干考查安全管理缺失这一管理基础"),
    "51CTO_一套_1": ("信息安全保障", "信息安全保障基础", "题干直接考查信息安全保障定义和要素"),
    "700题_b2_133": ("信息安全保障", "信息安全保障总体要求", "题干考查国家信息安全保障总体要求"),
    "51CTO_二套_17": ("信息安全支撑技术", "非对称密码算法", "题干考查非对称密码技术的通用优点"),
    "700题_b1_545": ("信息安全支撑技术", "非对称密码算法", "题干比较 RSA 与 DES 的算法属性"),
    "700题_b2_125": ("信息安全管理", "ISMS的PDCA模型", "题干直接考查 PDCA 的 ACT 环节"),
    "51CTO_六套_60": ("物理与网络通信安全", "DNS安全", "DNS 欺骗属于应用层问题"),
    "700题_b2_39": ("物理与网络通信安全", "TCP/IP数据封装", "题干直接考查 TCP/IP 封装顺序"),
    "51CTO_六套_61": ("物理与网络通信安全", "IP欺骗", "题干直接考查 IP 欺骗所在协议层"),
    "51CTO_五套_44": ("软件安全开发", "降低攻击面策略", "题目考查降低软件漏洞危害的整体策略"),
}


def _point_id(domain: str, name: str) -> str:
    digest = hashlib.sha1(f"{domain}\x1f{name}".encode("utf-8")).hexdigest()[:20]
    return f"kp_{digest}"


def apply_manual_tag_review(points: list[dict], tags: list[dict]) -> tuple[list[dict], list[dict]]:
    """覆盖人工复核题目的启发式标签，并补齐缺失的细粒度知识点。"""
    point_by_key = {(p["domain"], p["name"]): p for p in points}
    for domain, name, _reason in MANUAL_PRIMARY.values():
        point = point_by_key.get((domain, name))
        if point is None:
            point = {
                "id": _point_id(domain, name), "domain": domain, "name": name,
                "parent_name": None, "description": None,
                "evidence_count": 0, "question_count": 0,
                "status": "active", "recommended": True,
                "review": {
                    "version": REVIEW_VERSION, "decision": "accept",
                    "reason": "人工题目标签补充的细粒度知识点",
                    "original_name": name,
                },
            }
            points.append(point)
            point_by_key[(domain, name)] = point

    reviewed_ids = set(MANUAL_PRIMARY)
    tags[:] = [tag for tag in tags if tag["question_id"] not in reviewed_ids]
    for question_id, (domain, name, reason) in MANUAL_PRIMARY.items():
        point = point_by_key[(domain, name)]
        tags.append({
            "question_id": question_id,
            "knowledge_point_id": point["id"],
            "confidence": 1.0,
            "is_primary": True,
            "tag_source": "manual",
            "review": {"version": REVIEW_VERSION, "reason": reason},
        })
    # 标签被覆盖后，重新计算计数，避免保留已删除启发式标签留下的陈旧值。
    question_counts: dict[str, int] = {}
    for tag in tags:
        point_id = tag["knowledge_point_id"]
        question_counts[point_id] = question_counts.get(point_id, 0) + 1
    for point in points:
        point["question_count"] = question_counts.get(point["id"], 0)
    tags.sort(key=lambda t: (t["question_id"], not t["is_primary"], t["knowledge_point_id"]))
    points.sort(key=lambda p: (p["domain"], p["name"], p["id"]))
    return points, tags
