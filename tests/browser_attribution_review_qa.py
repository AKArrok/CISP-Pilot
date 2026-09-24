"""Read-only Playwright QA for the paginated attribution review UI."""
from __future__ import annotations

import argparse
import json
import re

from playwright.sync_api import sync_playwright


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--screenshot", required=True)
    parser.add_argument("--executable-path")
    args = parser.parse_args()

    with sync_playwright() as playwright:
        launch_options = {"headless": True}
        if args.executable_path:
            launch_options["executable_path"] = args.executable_path
        browser = playwright.chromium.launch(**launch_options)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page_errors = []
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        try:
            page.goto(args.base_url, wait_until="networkidle")
            page.get_by_role("button", name="刷题").click()
            status = page.locator("#attribution-review-status")
            status.get_by_text(re.compile(r"已加载 30 / \d+ 道题")).wait_for()
            first_ids = page.locator("#attribution-table tbody tr").evaluate_all(
                "rows => rows.map(row => row.querySelector('.attribution-question-id').textContent.trim())"
            )
            assert len(first_ids) == 30 and len(set(first_ids)) == 30
            match = re.search(r"已加载 30 / (\d+) 道题", status.inner_text())
            assert match and int(match.group(1)) > 60

            page.get_by_role("button", name=re.compile("加载更多题目")).click()
            status.get_by_text(re.compile(r"已加载 60 / \d+ 道题")).wait_for()
            loaded_ids = page.locator("#attribution-table tbody tr").evaluate_all(
                "rows => rows.map(row => row.querySelector('.attribution-question-id').textContent.trim())"
            )
            assert len(loaded_ids) == 60 and len(set(loaded_ids)) == 60

            query_id = first_ids[-1]
            page.locator("#attribution-review-query").fill(query_id)
            page.get_by_role("button", name="筛选", exact=True).click()
            page.wait_for_function("() => document.querySelector('#attribution-profile')?.getAttribute('aria-busy') === 'false'")
            filtered = page.locator("#attribution-table tbody tr").evaluate_all(
                "rows => rows.map(row => row.querySelector('.attribution-question-id').textContent.trim())"
            )
            search_match = re.search(r"已加载 (\d+) / (\d+) 道题", status.inner_text())
            assert search_match and int(search_match.group(1)) == len(filtered)
            assert int(search_match.group(2)) >= len(filtered)
            assert query_id in filtered

            page.get_by_role("button", name="清除", exact=True).click()
            status.get_by_text(re.compile(r"已加载 30 / \d+ 道题")).wait_for()
            first_row = page.locator("#attribution-table tbody tr").first
            assert first_row.locator(".attribution-point-name").inner_text()
            details = first_row.locator(".attribution-question-details")
            details.locator("summary").click()
            assert details.locator("p").inner_text().strip()
            assert details.locator("li").count() > 0
            page.screenshot(path=args.screenshot, full_page=True)

            page.set_viewport_size({"width": 390, "height": 844})
            widths = page.evaluate("""() => ({
              viewport: window.innerWidth,
              document: document.documentElement.scrollWidth,
              review: document.querySelector('#attribution-profile').getBoundingClientRect().width
            })""")
            assert widths["document"] <= widths["viewport"] + 1, widths
            assert page_errors == [], page_errors
            print(json.dumps({
                "status": "passed",
                "first_page": len(first_ids),
                "after_more": len(loaded_ids),
                "total": int(match.group(1)),
                "filtered_question_id": query_id,
                "matching_questions": len(filtered),
                "mobile_layout": widths,
                "screenshot": args.screenshot,
            }, ensure_ascii=False))
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
