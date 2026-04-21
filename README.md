# bbaek.py

**백준**과 **프로그래머스** 중 플랫폼을 고른 뒤, **브론즈·실버·골드·다이아** 티어와 유형을 정하고 문제 하나를 무작위로 추천하는 스크립트입니다.

- **백준**: [solved.ac](https://solved.ac) API로 태그·티어 검색을 하고, [백준](https://www.acmicpc.net) 문제 페이지에서 **문제 본문**(`#problem-body`)을 가져와 터미널에 텍스트로 보여 줍니다.
- **프로그래머스**: [school.programmers.co.kr](https://school.programmers.co.kr) API로 난이도(레벨)별 목록을 만든 뒤, **파트(`partTitle`)** 단위로 고르고, 문제 페이지의 **`.markdown`** 블록에서 본문을 가져와 터미널에 보여 줍니다.

## 필요 환경

- **Python 3** (표준 라이브러리만 사용: `urllib`, `argparse`, `json`, `html.parser` 등)
- 인터넷 연결 (solved.ac API, acmicpc.net HTML, 프로그래머스 모드일 때 school.programmers.co.kr API·HTML)

## 실행

```bash
python3 bbaek.py
```

프로그래머스만 비대화형으로 돌릴 때 예:

```bash
python3 bbaek.py --platform programmers --tier silver
```

인자를 거의 주지 않으면 **플랫폼(1=백준, 2=프로그래머스)**을 먼저 묻습니다.

- **백준**을 고르면 **티어**와 **문제 언어**를 묻고, **유형(태그)**은 목록에서 고르거나(0=티어만, r=유형 무작위, 숫자=해당 유형) 선택합니다.
- **프로그래머스**를 고르면 **티어**만 묻고(solved.ac `lang:` 필터는 사용하지 않음), **파트(`partTitle`)** 목록에서 같은 방식으로 고릅니다(0 / r / 번호).

비대화형(표준 입력이 터미널이 아님)으로 실행하면 플랫폼은 기본값 **백준**이며, `--platform programmers`로 프로그래머스를 지정할 수 있습니다. 유형(백준의 태그 / 프로그래머스의 파트)은 **무작위**로 진행되며, 안내 메시지가 표준 오류로 출력될 수 있습니다.

## 주요 옵션

| 옵션 | 설명 |
|------|------|
| `--platform boj\|programmers` | `boj`=백준, `programmers`=프로그래머스. 생략 시 터미널이면 질문, 아니면 백준 |
| `--tier {bronze,silver,gold,diamond}` | 난이도 티어. 생략 시 대화형으로 선택 |
| `--lang ko\|en\|all` | **백준 전용.** `ko`/`en`은 solved.ac 검색에 `lang:` 적용, `all`은 언어 필터 없음. 생략 시 터미널이면 질문, 아니면 `ko` |
| `--no-random-tag` | 유형 없이 해당 티어에서만 무작위 추천 (**백준 전용**) |
| `--tag-random` | 유형 목록에서 태그 하나를 무작위로 고른 뒤 그 조건으로 문제 무작위 (**백준 전용**) |
| `--tag-key KEY` | solved.ac 태그 키(예: `implementation`, `dynamic_programming`)로 유형 지정 (**백준 전용**) |
| `--tag-index N` | 유형 목록의 N번째(1부터, 한글 이름 순). **`--tier` 필수** (**백준 전용**) |
| `--seed N` | 난수 시드(재현용) |
| `--min-tag-problems N` | 태그(백준) 또는 파트(프로그래머스) 후보에 넣을 최소 문제 수(기본 5) |
| `--no-statement` | 문제 페이지 HTML을 받지 않고 메타·링크만 출력 |

프로그래머스 모드(`--platform programmers`)에서는 solved.ac 전용 옵션(`--no-random-tag`, `--tag-random`, `--tag-key`, `--tag-index`)을 사용할 수 없습니다. 비대화형으로 쓰려면 `--tier`와 함께 실행하면 파트가 **무작위**로 정해집니다.

### 프로그래머스: 티어와 API 레벨

| 티어 | 프로그래머스 `levels[]` |
|------|-------------------------|
| 브론즈 | 0, 1 |
| 실버 | 2 |
| 골드 | 3 |
| 다이아 | 4, 5 |

유형은 solved.ac 태그가 아니라 API에 붙은 **파트 제목(`partTitle`)**으로 묶습니다(예: 기출·주제·SQL 파트 등).

### 이미 본 문제 기록 (`seen.json`)

한 번 추천된 문제는 기본적으로 **스크립트와 같은 디렉터리**의 `seen.json`에 저장되며, 다음 실행부터 같은 무작위 추천에서 **플랫폼(`source`)과 문제 번호** 조합으로 제외됩니다(백준 번호와 프로그래머스 레슨 ID가 같아도 서로 다른 기록으로 취급).

| 옵션 | 설명 |
|------|------|
| `--seen-file PATH` | 기록 파일 경로 변경(기본: `./seen.json`) |
| `--reset-seen` | 기록을 비운 뒤 실행 |
| `--no-skip-seen` | 기록을 읽지 않아 이미 본 문제도 다시 나올 수 있음 |
| `--no-record-seen` | 이번에 고른 문제를 파일에 추가하지 않음 |

기록이 많아 같은 조건에서 새 문제를 찾지 못하면 안내 메시지와 함께 종료될 수 있습니다. 그때는 `--reset-seen`으로 비우거나 조건을 넓히면 됩니다.

## `seen.json` 형식

기본은 **version 2**이며, `problems` 배열에 항목이 쌓입니다.

```json
{
  "version": 2,
  "problems": [
    {
      "source": "boj",
      "problem_id": 1000,
      "type": "구현",
      "url": "https://www.acmicpc.net/problem/1000",
      "level": 3,
      "level_label": "브론즈 IV",
      "title": "A+B"
    },
    {
      "source": "programmers",
      "problem_id": 42842,
      "type": "연습문제",
      "url": "https://school.programmers.co.kr/learn/courses/30/lessons/42842",
      "level": 2,
      "level_label": "레벨 2",
      "title": "카펫"
    }
  ]
}
```

- **`source`**: `"boj"` 또는 `"programmers"`. 생략된 예전 항목은 백준으로 간주합니다.
- **`type`**: 백준은 선택한 유형(태그) 한글명, 프로그래머스는 파트(`partTitle`) 문자열. 티어만 고른 경우 `null`일 수 있습니다.
- 예전에 **`problem_ids`만 있는 파일**도 읽을 수 있으며, ID만으로 건너뛰기에 사용됩니다. 이후 저장 시 새 형식으로 갱신됩니다.

## 동작 요약

### 백준

1. **티어**는 solved.ac 검색 쇼트핸드 `*b`, `*s`, `*g`, `*d`로 표현됩니다.
2. **문제 언어**가 `ko`/`en`이면 검색 쿼리에 `lang:ko` / `lang:en`이 붙습니다.
3. 문제 하나는 solved.ac **`sort=random`** 검색으로 고릅니다. 이미 본 조합(`boj`, 번호)은 같은 쿼리로 여러 번 재시도해 피합니다.
4. **본문**은 `https://www.acmicpc.net/problem/{번호}` HTML에서 `#problem-body`만 파싱합니다.

### 프로그래머스

1. 선택한 티어에 해당하는 **레벨**로 `school.programmers.co.kr/api/v2/school/challenges/`에서 목록을 모읍니다.
2. **파트(`partTitle`)**별로 묶어 최소 문제 수(`--min-tag-problems`) 이상인 것만 유형 목록에 넣습니다.
3. 후보 중에서 무작위로 하나 고르며, 이미 본 조합(`programmers`, 레슨 ID)은 제외합니다.
4. **본문**은 `https://school.programmers.co.kr/learn/courses/30/lessons/{레슨ID}` HTML에서 `.markdown` 블록을 파싱합니다.

## 참고

- 백준 검색 문법은 [solved.ac](https://solved.ac) 웹 검색과 같은 계열입니다.
- 백준·프로그래머스 페이지 구조나 API 정책이 바뀌면 본문 추출·요청이 실패할 수 있습니다.

전체 플래그는 다음으로 확인할 수 있습니다.

```bash
python3 bbaek.py --help
```
