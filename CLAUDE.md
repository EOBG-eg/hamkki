# 함끼(Hamkki) — Claude Code 작업 안내

소프트웨어학과 2221042 이유빈의 3회차 과제 프로젝트. 상황과 조건을 입력하면 AI가 근처 식당 3곳을 이유와 함께 추천하고, (예정) 동행자와 투표로 정하는 모바일 반응형 웹서비스.
슬로건: "AI가 고르고, 함께 정하는 한 끼" / Pick together, eat together.

## 대화 방식

- 사용자는 한국어로 대화한다. 답변, 코드 주석, 화면 문구 모두 한국어로.
- 사용자는 코딩 초보에 가깝다. 명령어는 복사해서 바로 쓸 수 있게 주고, 어느 폴더에서 실행하는지 항상 밝힌다.
- 사용자는 "일일이 고치게 하지 말고 코드를 직접 업데이트해 달라"고 했다. 수정은 직접 하되, 무엇을 왜 바꿨는지 짧게 설명한다.
- 과제 규칙상 AI 코드를 그대로 내면 안 되고 "AI 초안 vs 본인 수정" 검증 기록을 제출해야 한다. 의미 있는 수정은 무엇이 문제였고 어떻게 고쳤는지 한두 줄로 남겨 사용자가 기록에 옮길 수 있게 한다.
- 커밋과 Push는 사용자가 GitHub Desktop으로 직접 한다. 작업이 끝나면 커밋 메시지(한국어 한 줄)만 제안한다.

## 과제 요구사항 (수업 안내문 기준)

- 주제·기술스택 자유, **서비스 안의 AI 기능은 반드시 Gemini API** (Claude API 등 다른 AI를 서비스에 넣으면 안 됨)
- AI 기능 1개 이상, "if문이나 SQL로는 안 되는 일"이어야 함
- 실제 배포, 누구나 접속 가능한 URL 제출 (Render 예정)
- 일정: 8주차 주제 승인(완료) → 9 설계 → 10~11 기본 기능, 11주차 중간 점검 → 12 Gemini 연동 → 13 배포 → 14 데모데이(직접 시연)
- Gemini 무료 쿼터가 있으니 데모데이에 처음 테스트하지 말 것

## 폴더 구조

```
hamkki/                       ← 이 폴더에서 Claude Code를 연다
├── .env                      ← API 키 (절대 커밋·출력 금지)
├── collect_restaurants.py    ← 식당 수집 (명령줄 + app.py가 import해서 사용)
├── data/                     ← 수집 데이터 (.gitignore, GitHub에 없음)
│   ├── tiles/<칸번호>.json   ← 약 1km × 1km 칸 하나의 식당 목록
│   ├── tiles.json            ← 수집한 칸 목록 (count, collected_at, partial, covered)
│   ├── places.json           ← 검색된 장소 이름·좌표·검색 횟수 ('바로 찾기' 버튼)
│   └── regions/ 등           ← 예전 방식 데이터 (이미 칸으로 이전됨, 지워도 됨)
└── hamkki_app/
    ├── app.py                ← Flask 서버 (실행 파일)
    ├── ai_recommend.py       ← Gemini 추천
    ├── requirements.txt      ← flask, requests, python-dotenv, google-genai, truststore
    ├── templates/            ← base.html, index.html(입력), results.html(결과)
    └── static/style.css
```

## 실행

Windows 명령 프롬프트 기준 (실습실 PC 경로):

```
C:\Users\1809-30\venv\Scripts\activate
cd C:\Users\1809-30\Documents\GitHub\hamkki\hamkki_app
python app.py                → http://localhost:5000
```

수집은 hamkki 폴더에서: `python collect_restaurants.py 의정부역 회룡역 --radius 1500` (`--refresh`로 최신화)

패키지 설치는 항상 `python -m pip install ...` (실행 중인 파이썬에 설치되도록).

## 동작 구조

1. 사용자가 장소를 검색 → `/api/search-place`가 카카오 키워드 검색으로 자동완성 (카카오 키는 서버에만)
2. `/recommend`: 장소 좌표 → 반경 1km를 덮는 칸 확인 → 없는 칸만 그 자리에서 카카오로 수집 (검색당 최대 9칸, 하루 60칸)
3. 반경 1km 식당 → 상황 규칙(`SITUATIONS`의 prefer/avoid)과 음식 종류로 후보 20곳
4. Gemini가 후보 중 3곳 + 추천 이유를 JSON으로 → 서버가 ID가 실제 후보인지 검사 → 결과 카드
5. AI가 실패하면 화면이 깨지지 않고 후보 20곳 목록과 실패 이유를 보여줌

수집 세부:
- 카카오 카테고리 검색(FD6)은 한 번에 최대 45곳 → 넘치면 구역을 4칸으로 재귀 분할 (최소 0.0005도)
- 간식·식품판매·도시락은 제외 (`EXCLUDE_KINDS`)
- 칸 경계에 걸친 식당은 좌표가 속한 칸에만 저장 → 중복 없음
- `partial` 칸은 `covered` 범위 밖만 추가 수집

AI 세부 (`ai_recommend.py`):
- 패키지는 `google-genai` (안내문의 `google-generativeai`는 예전 패키지)
- 모델 순서: `.env`의 `GEMINI_MODEL` → `gemini-3.5-flash-lite` → `gemini-3.8-flash` → `gemini-3.6-flash` (2.5 Flash는 신규 계정 사용 불가)
- 모델당 20초, 전체 40초 제한. `HAMKKI_DEBUG=1`(기본)이면 실패 원인을 화면에 표시, 배포 시 0

## 꼭 지킬 것

- `.env` 내용을 읽어서 출력하거나 대화에 쓰지 않는다. `.env`, `data/`, `__pycache__`는 커밋 금지.
- **학교 네트워크는 HTTPS를 중간에서 검사한다** → `CERTIFICATE_VERIFY_FAILED`. 해결은 `truststore`(OS 인증서 사용)로 한다. `verify=False` 등 인증서 검사를 끄는 방법은 쓰지 않는다. Gemini 클라이언트에는 `HttpOptions(client_args={"verify": ssl_context})`로 truststore 컨텍스트를 넘긴다.
- 네이버·카카오 웹페이지 크롤링 금지 (약관 위반·차단). 공식 API만 쓴다. 리뷰 원문 저장 금지.
- AI가 없는 식당을 지어내지 않도록, AI는 넘겨준 후보 안에서만 고르고 서버가 검사하는 구조를 유지한다.
- 새 외부 호출에는 항상 timeout을 둔다 (화면이 멈췄던 적이 있음).
- 화면은 모바일 기준(최대 폭 480px), 색은 `style.css`의 `--hk-*` 변수 사용.

## 진행 현황 (2026-11-10 기준)

완료: 카카오 자동 수집(격자 칸), 장소 검색·자동완성·자동 수집, 조건 입력 화면, Gemini AI 추천 + 안전장치
데이터: 의정부역·홍대입구역·강남역 일대 약 6,300곳 + 상우고등학교 주변

다음 할 일 (우선순위 순):
1. 결과 화면에 카카오 지도 (JavaScript SDK, `KAKAO_JS_KEY`, 도메인 등록: `http://localhost:5000`, 배포 시 Render 주소 추가). 앱 미설치 사용자도 되도록 외부 링크는 `https://` 웹 주소만.
2. 공유 링크 + 동행자 투표 (로그인 없음, 이름 입력 또는 브라우저 표시로 중복 투표 방지) ← 서비스의 차별점
3. 조건 덧붙여 재추천 ("더 저렴한 곳으로")
4. 식당 분위기 AI 태깅 (정해진 태그 목록 안에서만, 근거 없으면 "정보 없음"), 이후 방문자 태그로 보정
5. Render 배포: 무료 서버는 파일이 재시작 때 사라지므로 칸·투표 데이터를 Postgres로 이전 (Render 무료 Postgres는 30일 만료 주의), `gunicorn`, 환경변수 등록, `HAMKKI_DEBUG=0`

지역 미리 수집 우선순위: 의정부시 전체 → 노원·도봉·수유·창동 → 서울 약속 장소(건대·성수·잠실·신촌·혜화 등) → 경기·인천 중심가 → 지방 대도시. 전국 일괄 수집은 하지 않는다(호출량·용량·약관). 나머지는 검색 시 자동 수집에 맡긴다.

## 알려진 한계

- 카카오 API에는 분위기·리뷰·영업시간이 없다 → AI는 이름·종류·거리로만 추정 (프롬프트에서 단정 금지)
- 폐업 식당 미처리 (추천 직전 최종 3곳 재확인, 공공데이터 일반음식점 인허가 대조 검토 중)
- 카카오 로컬 API 데이터를 서버에 저장해도 되는 범위는 카카오 데브톡 확인 필요
- 웹 자동 수집 하루 한도는 메모리에만 있어 앱을 재시작하면 초기화됨
