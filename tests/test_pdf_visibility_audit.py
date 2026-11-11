import pymupdf

from scripts.audit_pdf_visibility import covered_text_spans


def test_later_opaque_shape_is_flagged_without_exposing_text():
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 70), "hidden example", fontsize=12)
    page.draw_rect(pymupdf.Rect(40, 48, 180, 80), color=None, fill=(1, 1, 1))
    findings = covered_text_spans(page)
    assert len(findings) == 1
    assert findings[0]["covered_fraction"] >= 0.8
    assert "hidden example" not in repr(findings)


def test_background_before_text_is_not_flagged():
    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_rect(pymupdf.Rect(40, 48, 180, 80), color=None, fill=(1, 1, 1))
    page.insert_text((50, 70), "visible example", fontsize=12)
    assert covered_text_spans(page) == []


def test_transparent_shape_is_not_treated_as_opaque():
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 70), "visible example", fontsize=12)
    page.draw_rect(pymupdf.Rect(40, 48, 180, 80), color=None, fill=(1, 1, 1), fill_opacity=0.2)
    assert covered_text_spans(page) == []

def test_partial_coverage_below_threshold_is_not_flagged():
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 70), "partially visible example", fontsize=12)
    page.draw_rect(pymupdf.Rect(48, 48, 59, 80), color=None, fill=(1, 1, 1))
    assert covered_text_spans(page) == []
