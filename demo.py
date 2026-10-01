"""Demo: Pure-HTTP zhixuewang login + score query + answer sheet rendering.

Usage:
    python demo.py
    python demo.py --username 12345678 --password yourpassword
    python demo.py --sheet 数学              # render annotated answer sheet
    python demo.py --sheet 数学 -e 2         # pick exam by index

This script demonstrates the full login flow, fetches the latest exam scores,
and can render the annotated answer sheet (score bar, wrong-question boxes,
per-subquestion deductions "16(2): -3/3", teacher spot marks) as a JPEG.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import sys
import time

from custom_provider import http_login


def _make_account(cookies: dict):
    """Use zhixuewang library to construct an account from the cookies."""
    try:
        from zhixuewang.account import login_cookie
    except ImportError:
        print("[Error] zhixuewang library not installed. Run: pip install zhixuewang")
        sys.exit(1)

    if "uname" not in cookies and "loginUserName" in cookies:
        cookies["uname"] = base64.b64encode(
            cookies["loginUserName"].encode("utf-8")
        ).decode("utf-8")
    return login_cookie(cookies)


def query_scores(account) -> None:
    """Query and print the latest exam scores."""
    print(f"\n  Student: {account.name}")

    # Get exam list
    exams = list(account.get_exams())
    if not exams:
        print("  No exams found.")
        return

    # Show latest 5 exams
    print(f"\n  Recent exams (showing up to 5, use -a for all):")
    for i, exam in enumerate(exams[:5], 1):
        print(f"    {i}. {exam.name}")

    # Get latest exam scores
    marks = account.get_self_mark()
    if not marks:
        print("\n  No scores available for the latest exam.")
        return

    exam_name = getattr(marks[0].exam, "name", "Latest Exam") if hasattr(marks[0], "exam") else "Latest Exam"
    print(f"\n  Scores for: {exam_name}")
    print(f"  {'Subject':<12} {'Score':>8} {'Class Rank':>12} {'Grade Rank':>12}")
    print(f"  {'-'*12} {'-'*8} {'-'*12} {'-'*12}")

    total = 0.0
    count = 0
    for m in marks:
        name = getattr(m.subject, "name", "?")
        score = getattr(m, "score", None)
        class_rank = getattr(m, "class_rank", None)
        grade_rank = getattr(m, "grade_rank", None)

        score_str = f"{score:.1f}" if score is not None else "-"
        cr_str = str(class_rank) if class_rank is not None else "-"
        gr_str = str(grade_rank) if grade_rank is not None else "-"

        print(f"  {name:<12} {score_str:>8} {cr_str:>12} {gr_str:>12}")

        if isinstance(score, (int, float)):
            total += float(score)
            count += 1

    if count > 0:
        print(f"  {'-'*12} {'-'*8} {'-'*12} {'-'*12}")
        print(f"  {'Total':<12} {total:>8.1f}")


def render_answer_sheet(account, subject_keyword: str, exam_index: int = 1) -> str:
    """Fetch, annotate and save the answer sheet of the given subject.

    Returns the output file path.
    """
    from zhixuewang.student.urls import Url

    from answer_sheet import add_score_bar, annotate_page, build_score_map

    exams = list(account.get_exams())
    if not exams:
        raise ValueError("No exams found.")
    exam = exams[exam_index - 1]
    print(f"  Exam: {exam.name}")

    subjects = list(account.get_subjects(exam))
    subject = None
    for s in subjects:
        if subject_keyword in s.name or s.name == subject_keyword:
            subject = s
            break
    if subject is None:
        names = ", ".join(s.name for s in subjects)
        raise ValueError(f"Subject '{subject_keyword}' not found. Available: {names}")

    # checksheet API: image URLs + layout data + per-step full scores
    r = account._session.get(
        Url.GET_ORIGINAL_URL,
        params={"examId": exam.id, "paperId": subject.id},
        headers=account.get_auth_header(),
    )
    r.raise_for_status()
    result = r.json().get("result") or {}
    image_urls = json.loads(result.get("sheetImages") or "[]")
    if not image_urls:
        raise ValueError(f"No answer sheet images uploaded for {subject.name} yet.")
    sheet_datas = json.loads(result.get("sheetDatas") or "{}")
    step_datas = result.get("stepDatas") or []

    score_map = build_score_map(sheet_datas, step_datas)
    pages = (sheet_datas.get("answerSheetLocationDTO") or {}).get("pageSheets") or []

    from PIL import Image

    rendered = []
    for idx, url in enumerate(image_urls):
        resp = account._session.get(url)
        resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content)).convert("RGB")
        if idx < len(pages):
            annotate_page(img, pages[idx], score_map)
        rendered.append(img)

    max_w = max(p.width for p in rendered)
    scaled = [
        p if p.width == max_w else p.resize((max_w, int(p.height * max_w / p.width)))
        for p in rendered
    ]
    total_h = sum(p.height for p in scaled)
    canvas = Image.new("RGB", (max_w, total_h), "white")
    y = 0
    for p in scaled:
        canvas.paste(p, (0, y))
        y += p.height
    canvas = add_score_bar(
        canvas, result.get("score"), result.get("standardScore"),
        f"{exam.name}  {subject.name}",
    )

    out_fp = f"answer_sheet_{subject.name}_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
    canvas.save(out_fp, quality=90)
    return out_fp


def main():
    parser = argparse.ArgumentParser(description="zhixuewang pure-HTTP login demo")
    parser.add_argument("-u", "--username", help="zhixue.com student ID or phone")
    parser.add_argument("-p", "--password", help="plaintext password")
    parser.add_argument("--sheet", metavar="SUBJECT", help="render annotated answer sheet of the subject")
    parser.add_argument("-e", "--exam-index", type=int, default=1, help="exam index (1 = latest)")
    args = parser.parse_args()

    username = args.username or input("Username: ").strip()
    password = args.password or input("Password: ").strip()

    print(f"\nLogging in as {username} (pure HTTP, may take 30-60s)...\n")

    try:
        cookies = http_login(username, password, print_fn=print)
    except Exception as e:
        print(f"\n[FAILED] Login error: {e}")
        sys.exit(1)

    print(f"\n[OK] Login successful! Got {len(cookies)} cookies.")
    account = _make_account(cookies)

    if args.sheet:
        print(f"\nRendering answer sheet for '{args.sheet}'...")
        try:
            fp = render_answer_sheet(account, args.sheet, args.exam_index)
            print(f"\n[OK] Saved: {fp}")
        except Exception as e:
            print(f"\n[Error] Answer sheet rendering failed: {e}")
            sys.exit(1)
        return

    # Query scores
    print("\nQuerying scores...")
    try:
        query_scores(account)
    except Exception as e:
        print(f"\n[Error] Score query failed: {e}")


if __name__ == "__main__":
    main()
