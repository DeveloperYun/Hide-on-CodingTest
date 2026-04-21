#!/usr/bin/env python3
"""
solved.ac API로 브론즈·실버·골드·다이아 티어를 고르고,
문제 유형(태그) 목록을 보여 준 뒤 사용자가 고르거나(대화형),
옵션으로 유형 무작위·미지정을 쓸 수 있다.

문제 언어는 선택 시에만 solved.ac 검색에 `lang:ko` / `lang:en`을 붙인다(메타 분류).
본문에 영어 단어가 섞였다고 제외하지 않으며, `lang:`를 아예 쓰지 않는 **전체** 모드도 있다.

문제 본문은 acmicpc.net 페이지의 `#problem-body`에서 텍스트로 추출해 터미널에 출력한다.

프로그래머스(선택 시)는 `school.programmers.co.kr` API로 난이도(레벨)·파트(`partTitle`)별 후보를 만든 뒤,
문제 페이지의 `.markdown` 블록에서 본문을 추출한다.

한 번 추천된 문제는 기본적으로 이 스크립트와 같은 디렉터리의 `seen.json`에
(플랫폼·문제 번호·유형·URL·난이도·제목 등) 저장되며,
다음 실행부터 같은 조건의 무작위 추천에서 제외한다(`--no-skip-seen`으로 끌 수 있음).

검색 쿼리는 solved.ac 웹 검색과 동일한 문법을 쓴다.
티어는 *b/*s/*g/*d 쇼트핸드를 사용한다(tier: 접두는 API에서 기대대로 동작하지 않을 수 있음).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import html as html_module
import json
import random
import sys
import time
import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

API_BASE = "https://solved.ac/api/v3"
REQUEST_TIMEOUT = 30

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; bbaek/1.0)",
    "Accept": "application/json",
    "x-solvedac-language": "ko",
}

# 백준 HTML은 브라우저 UA가 없으면 502 등으로 거절되는 경우가 있다.
BOJ_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}

PROGRAMMERS_SCHOOL_API = "https://school.programmers.co.kr/api/v2/school/challenges/"
PROGRAMMERS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}

# 백준 티어 → 프로그래머스 challenge level (API `levels[]`)
TIER_PROGRAMMERS_LEVELS: Dict[str, List[int]] = {
    "bronze": [0, 1],
    "silver": [2],
    "gold": [3],
    "diamond": [4, 5],
}

PROGRAMMERS_LEVEL_LABEL_KO: Dict[int, str] = {
    0: "레벨 0",
    1: "레벨 1",
    2: "레벨 2",
    3: "레벨 3",
    4: "레벨 4",
    5: "레벨 5",
}

COURSE_ID_DEFAULT = 30  # 코딩테스트 연습


class _ProblemBodyTextParser(HTMLParser):
    """`<div id="problem-body">` 안의 텍스트만 뽑는다(스크립트·스타일 제외).

    깊이는 **div만** 센다. section/p 등으로 깊이를 세면 BOJ HTML이 불균형일 때
    problem-body가 일찍 닫혀 본문·힌트가 잘리는 문제가 생긴다.
    """

    def __init__(self) -> None:
        super().__init__()
        self._capture = False
        self._div_depth = 0
        self._skip = 0
        self.parts: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        a = {k: v for k, v in attrs}
        if not self._capture:
            if tag == "div" and a.get("id") == "problem-body":
                self._capture = True
                self._div_depth = 1
            return
        if tag in ("script", "style", "textarea", "noscript"):
            self._skip += 1
            return
        if self._skip:
            return
        if tag == "br":
            self.parts.append("\n")
            return
        if tag == "div":
            self._div_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if not self._capture:
            return
        if tag in ("script", "style", "textarea", "noscript") and self._skip:
            self._skip -= 1
            return
        if self._skip:
            return
        if tag == "div":
            self._div_depth -= 1
            if self._div_depth <= 0:
                self._capture = False

    def handle_data(self, data: str) -> None:
        if self._capture and not self._skip:
            self.parts.append(data)


def fetch_boj_problem_html(problem_id: int, retries: int = 4) -> str:
    url = f"https://www.acmicpc.net/problem/{problem_id}"
    last: Optional[BaseException] = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers=BOJ_HEADERS, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 502, 503, 504) and attempt + 1 < retries:
                time.sleep(0.6 * (attempt + 1))
                continue
            raise
        except urllib.error.URLError as e:
            last = e
            if attempt + 1 < retries:
                time.sleep(0.6 * (attempt + 1))
                continue
            raise
    assert last is not None
    raise last


def _line_looks_like_base64_payload(line: str) -> bool:
    """백준 힌트 등에 숨은 긴 base64/JSON 페이로드 한 줄 제거용."""
    s = line.strip()
    if len(s) < 80:
        return False
    # base64 문자 집합 위주(공백 거의 없음)
    allowed = frozenset(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=_-"
    )
    ratio = sum(1 for c in s if c in allowed) / len(s)
    return ratio >= 0.97


def clean_statement_for_terminal(text: str) -> str:
    """터미널 표시용: LaTeX 꼬리표·base64 덩어리·과한 공백을 줄인다."""
    out_lines: List[str] = []
    for line in text.splitlines():
        if _line_looks_like_base64_payload(line):
            continue
        s = line.strip()
        if s == "복사":
            continue
        line = re.sub(r"\s*복사\s*$", "", line)
        line = re.sub(r"[\t ]+", " ", line).rstrip()
        out_lines.append(line)

    text = "\n".join(out_lines)

    # 흔한 LaTeX 구분자·환경(완벽하지 않으나 터미널 가독성용)
    text = text.replace("\\(", " ").replace("\\)", " ")
    text = text.replace("\\[", " ").replace("\\]", " ")
    text = re.sub(r"\$([^$\n]+)\$", r" \1 ", text)
    text = re.sub(r"\\begin\{[^}]+\}", "", text)
    text = re.sub(r"\\end\{[^}]+\}", "", text)
    text = text.replace("&", " ")
    text = re.sub(r"\\\\", "\n", text)
    text = re.sub(r"\\[ ,;.]", " ", text)
    # \pi, \dots, \le 등 단일 백슬래시 명령(남은 것)
    text = re.sub(r"\\[a-zA-Z]+", " ", text)

    lines2 = []
    for ln in text.splitlines():
        ln = re.sub(r" {2,}", " ", ln).rstrip()
        lines2.append(ln)
    text = "\n".join(lines2)
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    text = text.strip()
    return _annotate_empty_hint_section(text)


def _annotate_empty_hint_section(text: str) -> str:
    """본문이 '힌트' 제목만 있고 내용이 없을 때(HTML 빈 블록 등) 안내를 붙인다."""
    meaningful = [ln for ln in text.splitlines() if ln.strip()]
    if not meaningful:
        return text
    if meaningful[-1].strip() != "힌트":
        return text
    note = (
        "\n\n(이 문제는 백준 HTML에 표시된 힌트 본문이 없습니다. "
        "일부 문제는 브라우저에서만 보이거나, 터미널은 스크립트로 채워지는 힌트를 가져오지 못할 수 있습니다.)"
    )
    return text.rstrip() + note


def extract_problem_body_text(page_html: str) -> str:
    p = _ProblemBodyTextParser()
    p.feed(page_html)
    p.close()
    raw = "".join(p.parts)
    raw = html_module.unescape(raw)
    lines = [ln.rstrip() for ln in raw.splitlines()]
    text = "\n".join(lines)
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    text = text.strip()
    return clean_statement_for_terminal(text)


class _ProgrammersMarkdownParser(HTMLParser):
    """`<div class="markdown ...">` 안의 텍스트만 뽑는다(스크립트·스타일·이미지 제외)."""

    def __init__(self) -> None:
        super().__init__()
        self._capture = False
        self._div_depth = 0
        self._skip = 0
        self.parts: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        a = {k: v for k, v in attrs}
        cls = (a.get("class") or "") + " "
        if not self._capture:
            if tag == "div" and "markdown" in cls:
                self._capture = True
                self._div_depth = 1
            return
        if tag in ("script", "style", "textarea", "noscript"):
            self._skip += 1
            return
        if self._skip:
            return
        if tag == "img":
            alt = (a.get("alt") or "").strip()
            if alt:
                self.parts.append(f"[이미지: {alt}]")
            else:
                self.parts.append("[이미지]")
            return
        if tag == "br":
            self.parts.append("\n")
            return
        if tag == "div":
            self._div_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if not self._capture:
            return
        if tag in ("script", "style", "textarea", "noscript") and self._skip:
            self._skip -= 1
            return
        if self._skip:
            return
        if tag == "div":
            self._div_depth -= 1
            if self._div_depth <= 0:
                self._capture = False

    def handle_data(self, data: str) -> None:
        if self._capture and not self._skip:
            self.parts.append(data)


def fetch_programmers_lesson_html(lesson_id: int, retries: int = 4) -> str:
    url = f"https://school.programmers.co.kr/learn/courses/{COURSE_ID_DEFAULT}/lessons/{lesson_id}"
    last: Optional[BaseException] = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers=BOJ_HEADERS, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 502, 503, 504) and attempt + 1 < retries:
                time.sleep(0.6 * (attempt + 1))
                continue
            raise
        except urllib.error.URLError as e:
            last = e
            if attempt + 1 < retries:
                time.sleep(0.6 * (attempt + 1))
                continue
            raise
    assert last is not None
    raise last


def extract_programmers_markdown_text(page_html: str) -> str:
    p = _ProgrammersMarkdownParser()
    p.feed(page_html)
    p.close()
    raw = "".join(p.parts)
    raw = html_module.unescape(raw)
    lines = [ln.rstrip() for ln in raw.splitlines()]
    text = "\n".join(lines)
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    text = text.strip()
    return clean_statement_for_terminal(text)


def http_get_json_programmers(params: Dict[str, Any]) -> Dict[str, Any]:
    q = urllib.parse.urlencode(params, doseq=True)
    url = f"{PROGRAMMERS_SCHOOL_API}?{q}"
    req = urllib.request.Request(url, headers=PROGRAMMERS_HEADERS, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        raise SystemExit(f"HTTP {e.code} programmers challenges: {body}") from e
    except urllib.error.URLError as e:
        raise SystemExit(f"프로그래머스 API 요청 실패: {e}") from e
    return json.loads(raw)


def fetch_all_programmers_challenges(levels: List[int]) -> List[Dict[str, Any]]:
    """선택한 레벨들에 해당하는 챌린지 전부(페이지 순회)."""
    out: List[Dict[str, Any]] = []
    page = 1
    params: Dict[str, Any] = {
        "perPage": 30,
        "page": page,
        "order": "level",
        "levels[]": levels,
    }
    while True:
        params["page"] = page
        data = http_get_json_programmers(params)
        items = data.get("result") or []
        out.extend(items)
        total_pages = int(data.get("totalPages") or 0)
        if page >= total_pages or not items:
            break
        page += 1
    return out


def programmers_part_groups(
    challenges: List[Dict[str, Any]],
    *,
    min_count: int = 1,
) -> List[Dict[str, Any]]:
    """`partTitle` 기준으로 묶어 solved.ac 태그 목록과 비슷한 dict 리스트로 만든다."""
    counts: Dict[str, int] = {}
    for c in challenges:
        pt = str(c.get("partTitle") or "").strip() or "(파트 없음)"
        counts[pt] = counts.get(pt, 0) + 1
    rows: List[Dict[str, Any]] = []
    for title, cnt in counts.items():
        if cnt >= min_count:
            rows.append({"key": title, "partTitle": title, "tierMatchCount": cnt})
    return sort_tags_for_display(rows)


def tag_ko_name_programmers(tag: Dict[str, Any]) -> str:
    return str(tag.get("partTitle") or tag.get("key") or "")


def pick_programmers_challenge(
    challenges: List[Dict[str, Any]],
    tag_mode: str,
    part_tags: List[Dict[str, Any]],
    chosen_tag: Optional[Dict[str, Any]] = None,
    *,
    max_attempts: int = 80,
    seen_keys: Optional[Set[Tuple[str, int]]] = None,
) -> Tuple[Dict[str, Any], Optional[str]]:
    """seen_keys: {('programmers', lesson_id), ...}. 반환: (challenge, part_title 또는 None)."""
    seen = seen_keys if seen_keys is not None else set()
    key_prog = "programmers"

    def not_seen(c: Dict[str, Any]) -> bool:
        lid = int(c.get("id") or 0)
        return (key_prog, lid) not in seen

    if tag_mode == "none":
        pool = [c for c in challenges if not_seen(c)]
        if not pool:
            raise SystemExit(
                "조건에 맞는 새 문제가 없습니다. `--reset-seen` 또는 `--no-skip-seen`을 쓰거나 "
                "다른 티어를 고르세요."
            )
        return random.choice(pool), None

    if tag_mode == "chosen":
        if not chosen_tag:
            raise SystemExit("선택된 유형(파트)이 없습니다.")
        label = tag_ko_name_programmers(chosen_tag)
        pool = [
            c
            for c in challenges
            if str(c.get("partTitle") or "").strip() == label and not_seen(c)
        ]
        if not pool:
            raise SystemExit(
                f"파트 '{label}'에서 아직 안 본 문제가 없습니다. 기록을 비우거나 다른 파트를 고르세요."
            )
        return random.choice(pool), label

    # random: 파트 목록에서 무작위로 고른 뒤 해당 파트에서 무작위
    if not part_tags:
        raise SystemExit("파트 목록이 비어 있어 유형 무작위를 쓸 수 없습니다.")
    shuffled = part_tags[:]
    random.shuffle(shuffled)
    for tag in shuffled[:max_attempts]:
        label = tag_ko_name_programmers(tag)
        pool = [
            c
            for c in challenges
            if str(c.get("partTitle") or "").strip() == label and not_seen(c)
        ]
        if pool:
            return random.choice(pool), label
    raise SystemExit(
        f"{max_attempts}번 시도했지만 아직 안 본 문제가 있는 파트를 찾지 못했습니다. "
        "`--reset-seen`으로 기록을 비우거나 티어를 바꿔 보세요."
    )


def print_programmers_problem(
    ch: Dict[str, Any],
    *,
    part_label: Optional[str],
    show_statement: bool = True,
) -> None:
    lid = int(ch.get("id") or 0)
    title = str(ch.get("title") or "")
    level = int(ch.get("level") or 0)
    part = str(ch.get("partTitle") or "")
    url = f"https://school.programmers.co.kr/learn/courses/{COURSE_ID_DEFAULT}/lessons/{lid}"
    lvl_name = PROGRAMMERS_LEVEL_LABEL_KO.get(level, f"레벨 {level}")

    print()
    print(f"문제 ID(레슨): {lid}")
    print(f"제목: {title}")
    print(f"난이도: {lvl_name} ({level})")
    if part:
        print(f"파트: {part}")
    if part_label:
        print(f"선택된 유형(파트): {part_label}")
    print(f"프로그래머스: {url}")

    if show_statement and lid > 0:
        try:
            page = fetch_programmers_lesson_html(lid)
            body = extract_programmers_markdown_text(page)
        except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError) as e:
            print()
            print("— 문제 본문을 가져오지 못했습니다:", e)
            print("  브라우저에서 위 프로그래머스 링크를 여세요.")
        else:
            if body:
                print()
                print("— 문제 본문 —")
                print(body)
            else:
                print()
                print(
                    "— 본문 블록(.markdown)을 찾지 못했습니다. "
                    "페이지 구조가 바뀌었을 수 있습니다. 위 링크를 확인하세요."
                )
    print()


# solved.ac 문제 난이도(0~30) → 한글 표기
LEVEL_NAMES_KO: Dict[int, str] = {
    0: "Unrated",
    1: "브론즈 V",
    2: "브론즈 IV",
    3: "브론즈 III",
    4: "브론즈 II",
    5: "브론즈 I",
    6: "실버 V",
    7: "실버 IV",
    8: "실버 III",
    9: "실버 II",
    10: "실버 I",
    11: "골드 V",
    12: "골드 IV",
    13: "골드 III",
    14: "골드 II",
    15: "골드 I",
    16: "플래티넘 V",
    17: "플래티넘 IV",
    18: "플래티넘 III",
    19: "플래티넘 II",
    20: "플래티넘 I",
    21: "다이아 V",
    22: "다이아 IV",
    23: "다이아 III",
    24: "다이아 II",
    25: "다이아 I",
    26: "루비 V",
    27: "루비 IV",
    28: "루비 III",
    29: "루비 II",
    30: "루비 I",
}

# 키: argparse / 내부 식별자, 값: solved.ac 검색 쿼리 조각
TIER_SOLVED_QUERY: Dict[str, str] = {
    "bronze": "*b",
    "silver": "*s",
    "gold": "*g",
    "diamond": "*d",
}

TIER_LABEL_KO: Dict[str, str] = {
    "bronze": "브론즈",
    "silver": "실버",
    "gold": "골드",
    "diamond": "다이아",
}

# solved.ac 검색에 붙이는 언어 옵션(표시용)
LANG_SOLVED: Dict[str, str] = {
    "ko": "한국어",
    "en": "영어",
    "all": "전체 (lang 필터 없음)",
}


def tier_search_query(tier: str, lang: str) -> str:
    """티어 검색 접두. `lang`이 all이면 `lang:` 없이 티어만(예: `*s`)."""
    base = TIER_SOLVED_QUERY[tier]
    if lang == "all":
        return base
    return f"{base} lang:{lang}"


def http_get_json(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    q = urllib.parse.urlencode(params or {}, doseq=True)
    url = f"{API_BASE}{path}"
    if q:
        url = f"{url}?{q}"
    req = urllib.request.Request(url, headers=DEFAULT_HEADERS, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        raise SystemExit(f"HTTP {e.code} {path}: {body}") from e
    except urllib.error.URLError as e:
        raise SystemExit(f"요청 실패 {path}: {e}") from e
    return json.loads(raw)


def fetch_all_tags(min_problem_count: int = 5) -> List[Dict[str, Any]]:
    """tag/list 전 페이지를 합쳐 태그 목록을 만든다."""
    tags: List[Dict[str, Any]] = []
    page = 1
    while True:
        data = http_get_json("/tag/list", {"page": page})
        items = data.get("items") or []
        if not items:
            break
        for t in items:
            if int(t.get("problemCount") or 0) >= min_problem_count:
                tags.append(t)
        if len(items) < 30:
            break
        page += 1
    return tags


def sort_tags_for_display(tags: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """한글 표기 기준으로 정렬해 목록 번호가 고정되게 한다."""
    return sorted(tags, key=lambda t: (tag_ko_name(t).lower(), str(t.get("key") or "")))


def fetch_tag_by_key(key: str) -> Dict[str, Any]:
    """`/tag/show`로 단일 태그를 가져온다."""
    return http_get_json("/tag/show", {"key": key})


def tag_ko_name(tag: Dict[str, Any]) -> str:
    for dn in tag.get("displayNames") or []:
        if dn.get("language") == "ko":
            name = dn.get("name") or dn.get("short")
            if name:
                return str(name)
    return str(tag.get("key") or "")


def search_tier_tag_count(tier: str, tag_key: str, lang: str) -> int:
    """`*{티어} [lang:xx] #태그` 검색 결과 개수. lang이 all이면 lang: 없음."""
    query = f"{tier_search_query(tier, lang)} #{tag_key}"
    data = http_get_json(
        "/search/problem",
        {
            "query": query,
            "sort": "id",
            "direction": "asc",
            "page": 1,
        },
    )
    return int(data.get("count") or 0)


def filter_tags_for_tier(
    tier: str,
    sorted_tags: List[Dict[str, Any]],
    lang: str,
    *,
    progress: bool = True,
    max_workers: int = 16,
) -> List[Dict[str, Any]]:
    """전체 태그 목록에서, 티어(+선택 시 lang)와 함께 검색 시 문제가 1개 이상인 태그만 남긴다."""

    def check_one(t: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        key = t.get("key")
        if not key:
            return None
        cnt = search_tier_tag_count(tier, str(key), lang)
        if cnt <= 0:
            return None
        merged = dict(t)
        merged["tierMatchCount"] = cnt
        return merged

    total = len(sorted_tags)
    out: List[Dict[str, Any]] = []
    done = 0
    if total == 0:
        return out

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(check_one, t): t for t in sorted_tags}
        for fut in concurrent.futures.as_completed(futs):
            done += 1
            if progress:
                if done == 1 or done % 25 == 0 or done == total:
                    print(
                        (
                            "\r선택한 티어에서 풀 수 있는 유형만 확인 중... "
                            if lang == "all"
                            else "\r선택한 티어·언어에서 풀 수 있는 유형만 확인 중... "
                        )
                        + f"{done}/{total}",
                        end="",
                        flush=True,
                        file=sys.stderr,
                    )
            r = fut.result()
            if r is not None:
                out.append(r)
    if progress:
        print(file=sys.stderr)
    return sort_tags_for_display(out)


def search_random_problem(query: str) -> Tuple[Dict[str, Any], int]:
    """sort=random으로 한 페이지 검색해 첫 번째 문제를 고른다."""
    data = http_get_json(
        "/search/problem",
        {
            "query": query,
            "sort": "random",
            "direction": "asc",
            "page": 1,
        },
    )
    count = int(data.get("count") or 0)
    items = data.get("items") or []
    if not items:
        return {}, count
    return items[0], count


def default_seen_path() -> Path:
    return Path(__file__).resolve().parent / "seen.json"


def load_seen_entries(path: Path) -> List[Dict[str, Any]]:
    """`problems` 배열. 예전 `problem_ids`만 있으면 최소 필드로 복원."""
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (TypeError, OSError, ValueError, json.JSONDecodeError):
        return []
    if "problems" in data:
        problems = data.get("problems")
        if not isinstance(problems, list):
            return []
        return [
            x
            for x in problems
            if isinstance(x, dict) and x.get("problem_id") is not None
        ]
    raw = data.get("problem_ids")
    if raw is None:
        raw = data.get("ids") or []
    if raw:
        return [
            {
                "source": "boj",
                "problem_id": int(x),
                "type": None,
                "url": f"https://www.acmicpc.net/problem/{int(x)}",
                "level": None,
                "level_label": None,
                "title": None,
            }
            for x in raw
        ]
    return []


def load_seen_ids(path: Path) -> Set[int]:
    """하위 호환: 백준 번호만 집합으로 돌려준다(다른 플랫폼 기록은 무시)."""
    return {
        int(e["problem_id"])
        for e in load_seen_entries(path)
        if str(e.get("source") or "boj") == "boj"
    }


def load_seen_key_set(path: Path) -> Set[Tuple[str, int]]:
    """기록에 있는 (플랫폼, 문제 id) 집합. `source` 없으면 백준으로 간주."""
    out: Set[Tuple[str, int]] = set()
    for e in load_seen_entries(path):
        src = str(e.get("source") or "boj")
        out.add((src, int(e["problem_id"])))
    return out


def save_seen_problems(path: Path, problems: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 2, "problems": problems}
    tmp = path.parent / (path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def append_seen_record(path: Path, record: Dict[str, Any]) -> None:
    pid = int(record["problem_id"])
    src = str(record.get("source") or "boj")
    entries = load_seen_entries(path)
    existing = {
        (str(e.get("source") or "boj"), int(e["problem_id"]))
        for e in entries
        if e.get("problem_id") is not None
    }
    if (src, pid) in existing:
        return
    entries.append(record)
    save_seen_problems(path, entries)


def search_random_problem_avoiding(
    query: str,
    seen_keys: Set[Tuple[str, int]],
    *,
    max_draws: int = 150,
) -> Tuple[Dict[str, Any], int]:
    """무작위 추천을 반복해 (`boj`, 문제번호)가 `seen_keys`에 없는 문제를 고른다."""
    last_cnt = 0
    for _ in range(max_draws):
        prob, last_cnt = search_random_problem(query)
        if not prob:
            return prob, last_cnt
        pid = int(prob.get("problemId") or 0)
        if ("boj", pid) not in seen_keys:
            return prob, last_cnt
    raise SystemExit(
        f"{max_draws}번 무작위 추천했지만 모두 이미 기록된 문제입니다. "
        "`--reset-seen`으로 기록을 비우거나, `--no-skip-seen`으로 제외를 끄세요."
    )


def level_label(level: int) -> str:
    return LEVEL_NAMES_KO.get(level, f"레벨 {level}")


def problem_display_title(prob: Dict[str, Any], lang: str) -> str:
    """표시용 제목. en은 영어 제목, all은 한글 제목 우선."""
    if lang == "en":
        for t in prob.get("titles") or []:
            if t.get("language") == "en" and t.get("title"):
                return str(t["title"])
    if lang == "all":
        tk = prob.get("titleKo")
        if tk:
            return str(tk)
        for t in prob.get("titles") or []:
            if t.get("language") == "en" and t.get("title"):
                return str(t["title"])
        return ""
    return str(prob.get("titleKo") or "")


def seen_record_for_problem(
    prob: Dict[str, Any],
    tag_label: Optional[str],
    *,
    lang: str,
) -> Dict[str, Any]:
    """seen.json 한 건: 플랫폼, 문제 번호, 유형, 백준 URL, 난이도(level·한글), 제목."""
    pid = int(prob.get("problemId") or 0)
    level = int(prob.get("level") or 0)
    return {
        "source": "boj",
        "problem_id": pid,
        "type": tag_label,
        "url": f"https://www.acmicpc.net/problem/{pid}",
        "level": level,
        "level_label": level_label(level),
        "title": problem_display_title(prob, lang),
    }


def seen_record_for_programmers(
    ch: Dict[str, Any],
    part_label: Optional[str],
) -> Dict[str, Any]:
    lid = int(ch.get("id") or 0)
    lv = int(ch.get("level") or 0)
    return {
        "source": "programmers",
        "problem_id": lid,
        "type": part_label,
        "url": f"https://school.programmers.co.kr/learn/courses/{COURSE_ID_DEFAULT}/lessons/{lid}",
        "level": lv,
        "level_label": PROGRAMMERS_LEVEL_LABEL_KO.get(lv, f"레벨 {lv}"),
        "title": str(ch.get("title") or ""),
    }


def pick_problem(
    tier: str,
    tag_mode: str,
    tags: List[Dict[str, Any]],
    chosen_tag: Optional[Dict[str, Any]] = None,
    *,
    lang: str = "ko",
    max_attempts: int = 40,
    seen_keys: Optional[Set[Tuple[str, int]]] = None,
) -> Tuple[Dict[str, Any], str, Optional[str]]:
    """
    tier: bronze|silver|gold|diamond
    lang: ko | en | all (all이면 solved.ac `lang:` 미사용)
    tag_mode: none | random | chosen
    chosen_tag: tag_mode가 chosen일 때만 사용.
    seen_keys: 비어 있지 않으면 무작위 추천에서 (`boj`, 번호)는 제외(재시도).
    반환: (problem, 검색에 쓴 쿼리 문자열, 태그 한글명 또는 None)
    """
    seen = seen_keys if seen_keys is not None else set()

    def _random(q: str) -> Tuple[Dict[str, Any], int]:
        if seen:
            return search_random_problem_avoiding(q, seen)
        return search_random_problem(q)

    base = tier_search_query(tier, lang)
    if tag_mode == "none":
        query = base
        prob, _ = _random(query)
        return prob, query, None

    if not tags and tag_mode == "random":
        raise SystemExit("태그 목록이 비어 있어 유형 무작위를 쓸 수 없습니다.")

    if tag_mode == "chosen":
        if not chosen_tag:
            raise SystemExit("선택된 유형(태그)이 없습니다.")
        key = chosen_tag.get("key")
        if not key:
            raise SystemExit("태그 key가 비어 있습니다.")
        query = f"{base} #{key}"
        prob, cnt = _random(query)
        if not prob or cnt <= 0:
            raise SystemExit(
                f"해당 검색 조건에 문제가 없습니다: {tag_ko_name(chosen_tag)} (#{key})"
            )
        return prob, query, tag_ko_name(chosen_tag)

    # random
    shuffled = tags[:]
    random.shuffle(shuffled)

    for tag in shuffled[:max_attempts]:
        key = tag.get("key")
        if not key:
            continue
        query = f"{base} #{key}"
        prob, cnt = _random(query)
        if prob and cnt > 0:
            return prob, query, tag_ko_name(tag)

    raise SystemExit(
        f"{max_attempts}번 시도했지만 조건에 맞는 문제를 찾지 못했습니다. "
        "네트워크나 solved.ac 검색 결과를 확인해 주세요."
    )


def print_problem(
    prob: Dict[str, Any],
    query: str,
    tag_label: Optional[str],
    *,
    lang: str = "ko",
    show_statement: bool = True,
) -> None:
    pid = prob.get("problemId")
    title = problem_display_title(prob, lang)
    level = int(prob.get("level") or 0)
    boj_url = f"https://www.acmicpc.net/problem/{pid}"
    solved_url = f"https://solved.ac/search?query={urllib.parse.quote(query)}"

    print()
    print(f"문제 번호: {pid}")
    if lang == "all":
        print(
            "문제 언어: 전체 (lang: 미적용 — 본문에 영어가 있어도 여기서 걸러지지 않음)"
        )
    else:
        print(f"문제 언어: {LANG_SOLVED.get(lang, lang)}")
    print(f"제목: {title}")
    print(f"난이도: {level_label(level)} ({level})")
    if tag_label:
        print(f"선택된 유형(태그): {tag_label}")
    print(f"백준: {boj_url}")
    print(f"solved.ac 검색: {solved_url}")

    if show_statement and pid is not None:
        try:
            page = fetch_boj_problem_html(int(pid))
            body = extract_problem_body_text(page)
        except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError) as e:
            print()
            print("— 문제 본문을 가져오지 못했습니다:", e)
            print("  브라우저에서 위 백준 링크를 여세요.")
        else:
            if body:
                print()
                print("— 문제 본문 —")
                print(body)
            else:
                print()
                print(
                    "— 문제 본문 블록(#problem-body)을 찾지 못했습니다. "
                    "백준 페이지 구조가 바뀌었을 수 있습니다. 위 링크를 확인하세요."
                )
    print()


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="티어(브론즈~다이아)와 문제 유형으로 백준 또는 프로그래머스 문제 하나를 추천합니다.",
    )
    p.add_argument(
        "--platform",
        choices=["boj", "programmers"],
        default=None,
        metavar="boj|programmers",
        help="boj=백준(solved.ac), programmers=프로그래머스. 생략 시 터미널이면 묻는다.",
    )
    p.add_argument(
        "--tier",
        choices=list(TIER_SOLVED_QUERY.keys()),
        help="난이도 티어",
    )
    p.add_argument(
        "--lang",
        choices=["ko", "en", "all"],
        default=None,
        metavar="ko|en|all",
        help="문제 언어: ko/en은 solved.ac lang:, all은 필터 없음(한·영 혼합). "
        "생략 시 터미널이면 묻고, 아니면 한국어(ko)",
    )
    tag = p.add_mutually_exclusive_group()
    tag.add_argument(
        "--no-random-tag",
        action="store_true",
        help="유형 필터 없이 해당 티어에서만 무작위 추천",
    )
    tag.add_argument(
        "--tag-random",
        action="store_true",
        help="유형(태그)을 목록에서 무작위로 고른 뒤 그 조건으로 문제 무작위",
    )
    tag.add_argument(
        "--tag-key",
        metavar="KEY",
        help="solved.ac 태그 키(예: implementation, dynamic_programming). /tag/show와 동일",
    )
    tag.add_argument(
        "--tag-index",
        type=int,
        metavar="N",
        help=(
            "유형 목록에서 N번째(1부터). 한글 이름 순이며, "
            "선택한 티어·lang 조건에서 문제가 있는 유형만 포함(--tier 필수)"
        ),
    )
    p.add_argument(
        "--seed",
        type=int,
        default=None,
        help="난수 시드(재현용)",
    )
    p.add_argument(
        "--min-tag-problems",
        type=int,
        default=5,
        metavar="N",
        help="태그 후보에 포함할 최소 문제 수(기본 5)",
    )
    p.add_argument(
        "--no-statement",
        action="store_true",
        help="백준에서 문제 본문 HTML을 가져오지 않고 메타·링크만 출력",
    )
    p.add_argument(
        "--seen-file",
        type=Path,
        metavar="PATH",
        help="이미 본 문제 번호 저장 경로(기본: 이 스크립트와 같은 디렉터리의 seen.json)",
    )
    p.add_argument(
        "--reset-seen",
        action="store_true",
        help="저장된 '이미 본 문제' 기록을 비우고 실행",
    )
    p.set_defaults(skip_seen=True)
    p.add_argument(
        "--no-skip-seen",
        dest="skip_seen",
        action="store_false",
        help="기록에 있어도 동일 조건에서 다시 나올 수 있게 함",
    )
    p.add_argument(
        "--no-record-seen",
        action="store_true",
        help="이번에 고른 문제를 기록 파일에 추가하지 않음",
    )
    return p.parse_args(argv)


def interactive_tier() -> str:
    print("티어를 고르세요.")
    keys = list(TIER_SOLVED_QUERY.keys())
    for i, k in enumerate(keys, 1):
        print(f"  {i}. {TIER_LABEL_KO[k]}")
    while True:
        s = input("번호 (1-4): ").strip()
        if s in ("1", "2", "3", "4"):
            return keys[int(s) - 1]
        print("1에서 4 사이 숫자를 입력하세요.")


def interactive_platform() -> str:
    print("플랫폼을 고르세요.")
    print("  1. 백준 (solved.ac + acmicpc.net)")
    print("  2. 프로그래머스 (school.programmers.co.kr)")
    while True:
        s = input("번호 (1-2): ").strip()
        if s == "1":
            return "boj"
        if s == "2":
            return "programmers"
        print("1 또는 2를 입력하세요.")


def interactive_language() -> str:
    print("문제 언어를 고르세요.")
    print("  1. 한국어 (solved.ac lang:ko)")
    print("  2. 영어 (solved.ac lang:en)")
    print("  3. 전체 — lang 필터 없음 (한·영 섞여 나올 수 있음)")
    while True:
        s = input("번호 (1-3): ").strip()
        if s == "1":
            return "ko"
        if s == "2":
            return "en"
        if s == "3":
            return "all"
        print("1, 2 또는 3을 입력하세요.")


def interactive_tag_selection(
    sorted_tags: List[Dict[str, Any]],
    *,
    kind: str = "태그",
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """0=티어만, r=유형 무작위, 1..N=해당 유형."""
    print()
    print(f"— 문제 유형 ({kind}) —")
    print("  0  티어만       이 난이도 전체에서 유형 필터 없이 무작위")
    print(
        f"  r  무작위 유형  아래 목록에서 {kind} 하나를 무작위로 고른 뒤, 그 조건으로 문제 무작위"
    )
    print("  ([숫자] = 선택한 검색 조건에서 해당 항목 문제 개수)")
    print()
    for i, t in enumerate(sorted_tags, 1):
        ko = tag_ko_name(t)
        key = str(t.get("key") or "")
        cnt = int(t.get("tierMatchCount") or t.get("problemCount") or 0)
        print(f"  {i:3d}.  {ko}  ({key})  [{cnt}]")
    print()
    nmax = len(sorted_tags)
    while True:
        s = input(f"선택 (0 / r / 1~{nmax}): ").strip().lower()
        if s == "0":
            return "none", None
        if s == "r":
            return "random", None
        if s.isdigit():
            idx = int(s)
            if 1 <= idx <= nmax:
                return "chosen", sorted_tags[idx - 1]
        print(f"0, r, 또는 1~{nmax} 사이 숫자를 입력하세요.")


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)

    seen_path = args.seen_file or default_seen_path()
    if args.reset_seen:
        save_seen_problems(seen_path, [])
    seen_keys: Set[Tuple[str, int]] = (
        load_seen_key_set(seen_path) if args.skip_seen else set()
    )

    if args.seed is not None:
        random.seed(args.seed)

    platform = args.platform
    if platform is None:
        if sys.stdin.isatty():
            platform = interactive_platform()
        else:
            platform = "boj"

    if platform == "programmers":
        main_programmers(args, seen_path, seen_keys)
    else:
        main_boj(args, seen_path, seen_keys)


def main_programmers(
    args: argparse.Namespace,
    seen_path: Path,
    seen_keys: Set[Tuple[str, int]],
) -> None:
    explicit_tag = (
        args.no_random_tag
        or args.tag_random
        or (args.tag_key is not None)
        or (args.tag_index is not None)
    )
    if explicit_tag:
        raise SystemExit(
            "프로그래머스 모드에서는 solved.ac 전용 옵션(--no-random-tag / --tag-random / "
            "--tag-key / --tag-index)을 쓸 수 없습니다. 터미널에서 대화형으로 실행하거나 "
            "`--platform boj`로 백준을 선택하세요."
        )

    if args.tier is None:
        tier = interactive_tier()
    else:
        tier = args.tier

    levels = TIER_PROGRAMMERS_LEVELS[tier]

    challenges = fetch_all_programmers_challenges(levels)
    if not challenges:
        raise SystemExit("선택한 티어(레벨)에 해당하는 문제를 API에서 가져오지 못했습니다.")

    part_tags = programmers_part_groups(challenges, min_count=args.min_tag_problems)
    if not part_tags:
        raise SystemExit(
            "파트(`partTitle`)별로 묶인 문제가 없습니다. `--min-tag-problems`를 낮춰 보세요."
        )

    chosen_tag: Optional[Dict[str, Any]] = None
    if sys.stdin.isatty():
        tag_mode, chosen_tag = interactive_tag_selection(
            part_tags,
            kind="파트(partTitle)",
        )
    else:
        if args.tier is None:
            print(
                "참고: 표준 입력이 터미널이 아니어 파트 목록을 띄우지 못합니다. "
                "파트 무작위로 진행합니다.",
                file=sys.stderr,
            )
        tag_mode = "random"

    ch, part_label = pick_programmers_challenge(
        challenges,
        tag_mode,
        part_tags,
        chosen_tag=chosen_tag,
        seen_keys=seen_keys,
    )
    print_programmers_problem(ch, part_label=part_label, show_statement=not args.no_statement)

    if not args.no_record_seen and ch.get("id") is not None:
        append_seen_record(seen_path, seen_record_for_programmers(ch, part_label))


def main_boj(
    args: argparse.Namespace,
    seen_path: Path,
    seen_keys: Set[Tuple[str, int]],
) -> None:
    explicit_tag = (
        args.no_random_tag
        or args.tag_random
        or (args.tag_key is not None)
        or (args.tag_index is not None)
    )

    if args.tier is None:
        if explicit_tag:
            raise SystemExit("--tier 없이 --tag-* / --no-random-tag 옵션은 사용할 수 없습니다.")
        tier = interactive_tier()
    else:
        tier = args.tier

    if args.lang is not None:
        lang = args.lang
    elif sys.stdin.isatty():
        lang = interactive_language()
    else:
        lang = "ko"

    tags: List[Dict[str, Any]] = []
    chosen_tag: Optional[Dict[str, Any]] = None
    tag_mode: str

    if explicit_tag:
        if args.no_random_tag:
            tag_mode = "none"
        elif args.tag_random:
            tag_mode = "random"
            tags = fetch_all_tags(min_problem_count=args.min_tag_problems)
            sorted_tags = sort_tags_for_display(tags)
            tags = filter_tags_for_tier(tier, sorted_tags, lang)
            if not tags:
                raise SystemExit(
                    "선택한 조건에서 유형 태그와 함께 검색되는 문제가 없습니다."
                )
        elif args.tag_key is not None:
            tag_mode = "chosen"
            chosen_tag = fetch_tag_by_key(args.tag_key)
            k = str(chosen_tag.get("key") or "")
            if not k:
                raise SystemExit("--tag-key에 해당하는 태그를 찾지 못했습니다.")
            if search_tier_tag_count(tier, k, lang) <= 0:
                loc = (
                    f"{TIER_LABEL_KO.get(tier, tier)}"
                    if lang == "all"
                    else f"{TIER_LABEL_KO.get(tier, tier)}, {LANG_SOLVED.get(lang, lang)}"
                )
                raise SystemExit(
                    f"태그 '{k}'는 선택한 조건({loc})에서 함께 검색되는 문제가 없습니다."
                )
        elif args.tag_index is not None:
            tags = fetch_all_tags(min_problem_count=args.min_tag_problems)
            sorted_tags = sort_tags_for_display(tags)
            tier_tags = filter_tags_for_tier(tier, sorted_tags, lang)
            if not tier_tags:
                raise SystemExit(
                    "선택한 조건에서 유형 태그와 함께 검색되는 문제가 없습니다."
                )
            if args.tag_index < 1 or args.tag_index > len(tier_tags):
                raise SystemExit(
                    f"--tag-index는 1 이상 {len(tier_tags)} 이하여야 합니다."
                )
            tag_mode = "chosen"
            chosen_tag = tier_tags[args.tag_index - 1]
        else:
            raise AssertionError("explicit_tag인데 태그 옵션 분기가 없습니다.")
    elif sys.stdin.isatty():
        tags = fetch_all_tags(min_problem_count=args.min_tag_problems)
        sorted_tags = sort_tags_for_display(tags)
        tier_tags = filter_tags_for_tier(tier, sorted_tags, lang)
        if not tier_tags:
            raise SystemExit(
                "선택한 조건에서 유형 태그와 함께 검색되는 문제가 없습니다."
            )
        tag_mode, chosen_tag = interactive_tag_selection(tier_tags)
    else:
        if args.tier is None:
            print(
                "참고: 표준 입력이 터미널이 아니어 유형 목록을 띄우지 못합니다. "
                "유형 무작위로 진행합니다. 유형을 고르려면 터미널에서 실행하거나 "
                "--tag-key / --tag-index / --no-random-tag 를 지정하세요.",
                file=sys.stderr,
            )
        tag_mode = "random"
        tags = fetch_all_tags(min_problem_count=args.min_tag_problems)
        sorted_tags = sort_tags_for_display(tags)
        tags = filter_tags_for_tier(tier, sorted_tags, lang)
        if not tags:
            raise SystemExit(
                "선택한 조건에서 유형 태그와 함께 검색되는 문제가 없습니다."
            )

    if tag_mode == "chosen":
        prob, query, tag_label = pick_problem(
            tier,
            "chosen",
            [],
            chosen_tag=chosen_tag,
            lang=lang,
            seen_keys=seen_keys,
        )
    elif tag_mode == "random":
        prob, query, tag_label = pick_problem(
            tier, "random", tags, lang=lang, seen_keys=seen_keys
        )
    else:
        prob, query, tag_label = pick_problem(
            tier, "none", [], lang=lang, seen_keys=seen_keys
        )

    if not prob:
        raise SystemExit("문제를 찾지 못했습니다. 티어·언어·유형 조건을 완화하거나 나중에 다시 시도하세요.")

    print_problem(prob, query, tag_label, lang=lang, show_statement=not args.no_statement)

    if not args.no_record_seen and prob.get("problemId") is not None:
        append_seen_record(
            seen_path,
            seen_record_for_problem(prob, tag_label, lang=lang),
        )


if __name__ == "__main__":
    main()
