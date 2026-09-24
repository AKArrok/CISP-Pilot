"""51CTO 异形题号、选项和结构污染的解析回归。"""


def test_delimiterless_question_numbers_and_options_are_split():
    from data_ingest.parse_questions import _parse_51cto_paragraphs

    questions, failures = _parse_51cto_paragraphs("sample", [
        "60.关于证书的说法错误的是：",
        "A.甲", "B.乙", "C.丙", "D.丁",
        "61 认证中心的核心职责是？",
        "A签发证书", "B 验证信息", "C.公布黑名单", "D、撤销证书",
        "60 A", "61 B",
    ])

    assert [question["num"] for question in questions] == [60, 61]
    assert questions[1]["options"] == {
        "A": "签发证书", "B": "验证信息", "C": "公布黑名单", "D": "撤销证书",
    }
    assert [question["answer"] for question in questions] == ["A", "B"]
    assert not [failure for failure in failures if "embedded_question" in failure["reason"]]


def test_unnumbered_question_after_complete_options_is_quarantined():
    from data_ingest.parse_questions import _parse_51cto_paragraphs

    questions, failures = _parse_51cto_paragraphs("sample", [
        "5.以下关于置换密码的说法正确的是：B",
        "A.甲", "B.乙", "C.丙", "D.丁",
        "以下关于代替密码的说法正确的是：A",
        "A.甲", "B.乙", "C.丙", "D.丁",
    ])

    assert [question["num"] for question in questions] == [5, 6]
    assert questions[1]["number_inferred"] is True
    assert questions[1]["needs_review"] is True
    assert any(failure["reason"] == "question_number_inferred" for failure in failures)


def test_out_of_sequence_number_is_not_merged_into_previous_question():
    from data_ingest.parse_questions import _parse_51cto_paragraphs

    questions, failures = _parse_51cto_paragraphs("sample", [
        "26.第一题？A", "A.甲", "B.乙", "C.丙", "D.丁",
        "2.UNIX 权限题？D", "A.甲", "B.乙", "C.丙", "D.丁",
        "28、下一题？B", "A.甲", "B.乙", "C.丙", "D.丁",
    ])

    assert [question["num"] for question in questions] == [26, 27, 28]
    assert questions[1]["original_num"] == 2
    assert questions[1]["needs_review"] is True
    assert any(failure["reason"] == "out_of_sequence_number" for failure in failures)


def test_structural_audit_quarantines_embedded_next_question():
    from data_ingest.parse_questions import new_question, quarantine_structural_failures

    question = new_question("sample", 60)
    question.update({
        "stem": "60.第一题？61 下一题？",
        "options": {"A": "甲", "B": "乙", "C": "丙", "D": "丁"},
        "answer": "A",
    })

    failures = quarantine_structural_failures([question])

    assert question["needs_review"] is True
    assert failures[0]["reason"] == "embedded_question_or_oversized_stem"


def test_lowercase_answer_key_and_numbered_subitems_are_not_questions():
    from data_ingest.parse_questions import _parse_51cto_paragraphs

    questions, _failures = _parse_51cto_paragraphs("sample", [
        "72、下列控制措施如何分类？", "1、网络防火墙", "2、编辑和确认程序",
        "A、第一组", "B、第二组", "C、第三组", "D、第四组",
        "72 c",
    ])

    assert len(questions) == 1
    assert questions[0]["num"] == 72
    assert "1、网络防火墙" in questions[0]["stem"]
    assert questions[0]["answer"] == "C"


def test_time_continuation_is_not_treated_as_question_number():
    from data_ingest.parse_questions import _parse_51cto_paragraphs

    questions, _failures = _parse_51cto_paragraphs("sample", [
        "77.文件权限题？", "09：14 file", "A文件", "B目录", "C用户", "D组",
        "77 A",
    ])

    assert len(questions) == 1
    assert "09：14 file" in questions[0]["stem"]


def test_unexpected_pdf_number_drop_is_quarantined_not_a_new_bank():
    from data_ingest.parse_questions import new_question, split_banks

    questions = [new_question("raw", num) for num in (205, 206, 56, 207)]
    split = split_banks(questions)

    assert {question["source"] for question in split} == {"700题_b1"}
    conflict = next(question for question in split if question["num"] == 56)
    assert conflict["needs_review"] is True
    assert "unsequenced" in conflict["id"]


def test_multiple_options_in_one_paragraph_are_split():
    from data_ingest.parse_questions import _parse_51cto_paragraphs

    questions, _failures = _parse_51cto_paragraphs("sample", [
        "71 在关系型数据库中，视图实现什么原则？",
        "A.纵深防御 B.最小权限 C.职责分离 D.便利性平衡",
        "71 B",
    ])

    assert questions[0]["options"] == {
        "A": "纵深防御", "B": "最小权限", "C": "职责分离", "D": "便利性平衡",
    }
    assert questions[0]["needs_review"] is False
