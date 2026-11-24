# 함끼(Hamkki) — Claude Code 작업 안내

소프트웨어학과 2221042 이유빈의 3회차 과제 프로젝트. 상황과 조건을 입력하면 AI가 근처 식당 3곳을 이유와 함께 추천하고, 동행자와 링크 투표로 정하는 모바일 반응형 웹서비스.
슬로건: "AI가 고르고, 함께 정하는 한 끼" / Pick together, eat together.

## 대화 방식

- 사용자는 한국어로 대화한다. 답변, 코드 주석, 화면 문구 모두 한국어로.
- 명령어는 복사해서 바로 쓸 수 있게 주고, 어느 폴더에서 실행하는지 항상 밝힌다.
- 사용자는 "일일이 고치게 하지 말고 코드를 직접 업데이트해 달라"고 했다. 수정은 직접 하되, 무엇을 왜 바꿨는지 짧게 설명한다.
- 과제 규칙상 AI 코드를 그대로 내면 안 되고 "AI 초안 vs 본인 수정" 검증 기록을 제출해야 한다. 의미 있는 수정은 무엇이 문제였고 어떻게 고쳤는지 한두 줄로 남겨 사용자가 기록에 옮길 수 있게 한다.
- 커밋·Push는 사용자가 요청할 때만 한다(사용자가 "올려줘/적용시켜줘"라고 하면 Claude가 커밋·Push, 아니면 한국어 한 줄 커밋 메시지만 제안).
  Push하면 Render가 자동 재배포(약 3분)된다. 올리기 전 `data/`·`.env`가 없는지 확인하고, 올린 뒤 실제 사이트에서 반영을 확인한다.

## 과제 요구사항 (수업 안내문 기준)

- 주제·기술스택 자유, **서비스 안의 AI 기능은 반드시 Gemini API** (Claude API 등 다른 AI를 서비스에 넣으면 안 됨)
- AI 기능 1개 이상, "if문이나 SQL로는 안 되는 일"이어야 함
- 실제 배포, 누구나 접속 가능한 URL 제출 → **https://hamkki.onrender.com** (Render 무료, Blueprint `render.yaml`, main에 Push하면 자동 재배포). **빌드 단계에서 의정부시 전체를 수집**해 서버 이미지에 포함 → 잠들었다 깨어나도 유지. `data/`는 GitHub에 올리지 않음(카카오 데이터 재배포 방지)
- 일정: 8주차 주제 승인(완료) → 9 설계 → 10~11 기본 기능 → 12 Gemini 연동 → 13 배포 → 14 데모데이(직접 시연) — 모두 완료, 남은 건 제출·발표
- Gemini 무료 쿼터가 있으니 데모데이에 처음 테스트하지 말 것
- **최종 일정(11/24 공지): 제출 11/30(월) 22:00 카카오워크 1:1, `학번_이름_3회차.zip`. 데모데이 12/1(화) 13:30~ (순서별 5·6·7교시), 발표 3분 + 질의응답 2~3분**
  - 발표 구성: ① 문제 정의 30초 ② AI 기능·왜 AI가 필요한지 1분 ③ 배포 서비스 실시간 시연 1분 ④ 질의응답(구현 어려움, AI 검증)
  - 평가(40): 문제 정의 10, AI 기능 타당성·완성도 10, 구현 완성도 10, 배포·전달력 10
  - 제출물: GitHub 링크, 배포 URL, AI 기능 설명, 검증 기록, 발표 자료(선택, 시연으로 대체 가능), **포트폴리오 카드 3장**(1회차 셔틀버스, 2회차 캠퍼스 문제, 3회차 함끼. 항목: 프로젝트명/한 줄 소개/개발 기간·역할/기술 스택/핵심 기능 3/AI 활용 방식·검증/스크린샷 1~2/GitHub·배포 URL)
  - 발표 당일: 5~10분 전 배포 URL 접속해 서버 깨우기, 학교 네트워크에서 AI 동작 확인, 쿼터 아끼기

## 폴더 구조

```
hamkki/                       ← 이 폴더에서 Claude Code를 연다
├── .env                      ← API 키 (절대 커밋·출력 금지)
├── collect_restaurants.py    ← 식당 수집 (명령줄 + app.py가 import해서 사용)
├── db.py                     ← Postgres 저장소 (DATABASE_URL 있을 때만. 투표·실행 중 수집 칸·검색 장소)
├── render.yaml               ← Render 배포 설정 (Blueprint)
├── .python-version           ← Render 파이썬 버전
├── data/                     ← 수집 데이터 (.gitignore, GitHub에 없음)
│   ├── tiles/<칸번호>.json   ← 약 1km × 1km 칸 하나의 식당 목록
│   ├── tiles.json            ← 수집한 칸 목록 (count, collected_at, partial, covered)
│   ├── places.json           ← 검색된 장소 이름·좌표·검색 횟수 ('바로 찾기' 버튼)
│   ├── votes/<투표ID>.json   ← 동행자 투표 (후보 3곳 + 표)
│   └── regions/ 등           ← 예전 방식 데이터 (이미 칸으로 이전됨, 지워도 됨)
└── hamkki_app/
    ├── app.py                ← Flask 서버 (실행 파일)
    ├── ai_recommend.py       ← Gemini 추천
    ├── feedback_store.py     ← 추천 만족도 기록·통계
    ├── vote_store.py         ← 투표 저장 (DATABASE_URL 있으면 Postgres, 없으면 data/votes 파일)
    ├── requirements.txt      ← flask, requests, python-dotenv, google-genai, truststore, gunicorn, pg8000
    ├── templates/            ← base.html, index.html(입력), results.html(결과), vote.html(투표), _map.html(지도)
    └── static/style.css
```

## 실행

Windows 명령 프롬프트 기준 (실습실 PC 경로):

```
%USERPROFILE%\venv\Scripts\activate
cd %USERPROFILE%\Documents\GitHub\hamkki\hamkki_app
python app.py                → http://localhost:5000
```

수집은 hamkki 폴더에서: `python collect_restaurants.py 의정부역 회룡역 --radius 1500` (`--refresh`로 최신화)
시 전체: `python collect_restaurants.py --area 의정부시` (범위는 `AREAS` 사각형, 같은 칸 방식. 네트워크 오류 칸은 건너뛰고 재실행 시 그 칸만 수집)

패키지 설치는 항상 `python -m pip install ...` (실행 중인 파이썬에 설치되도록).

### 다른 PC(집)에서 이어서 할 때
- 코드: GitHub Desktop → Clone `EOBG-eg/hamkki`. 이 파일(CLAUDE.md)에 맥락이 다 있으므로 Claude Code로 이 폴더를 열면 이어서 할 수 있다 (이 대화 기록 자체는 학교 PC에만 있음)
- 위 실행 경로(사용자 폴더의 `venv`)는 학교 실습실 PC 기준. 다른 PC는 hamkki 폴더에서 `python -m venv venv` → `venv\Scripts\activate` → `python -m pip install -r hamkki_app/requirements.txt`
- `.env`(API 키)와 `data/`는 GitHub에 없다. 로컬 실행에만 필요하고, 코드를 고쳐 Push만 할 거면 없어도 된다(배포 서버엔 키가 Render 환경변수로 있음). 옮길 때는 USB 등으로 직접, 채팅·공개 장소에 붙이지 않기
- `data/` 복구: 백업 `Documents\hamkki_data_backup_20261110.zip`(학교 PC) 또는 `python collect_restaurants.py --area 의정부시`로 다시 수집
- 학교 PC 전용 사정: HTTPS 검사망(truststore 필요), 응용 프로그램 제어 정책(psycopg DLL 차단 → pg8000), Edge 헤드리스로 화면 캡처 가능

## 동작 구조

1. 사용자가 장소를 검색 → `/api/search-place`가 자동완성 (동·구 이름이면 주소 검색 결과를 먼저, 그 뒤 키워드 검색. 카카오 키는 서버에만)
2. `/recommend`: 장소 좌표 → 반경 1km를 덮는 칸 확인 → 없는 칸만 그 자리에서 카카오로 수집 (검색당 최대 9칸, 하루 60칸)
3. 반경 1km 식당 → 상황 규칙(`SITUATIONS`의 prefer/avoid)과 음식 종류로 후보 20곳
4. Gemini가 후보 중 3곳 + 추천 이유를 JSON으로 → 서버가 ID가 실제 후보인지 검사 → 결과 카드
5. AI가 실패하면 화면이 깨지지 않고 후보 20곳 목록과 실패 이유를 보여줌
6. 재추천: 결과 화면에서 "더 저렴한 곳으로" 등 조건을 덧붙이면(최대 3번) 처음 조건 + 덧붙인 요청 + 지금까지 보여준 식당(`seen`)을 AI에 넘겨 다시 고름. 보여준 식당은 서버가 AI 후보에서 제외
7. '친구들과 투표로 정하기' → `/vote/create`(추천 내용은 `SECRET_KEY`로 서명해 위조 방지) → `/vote/<id>` 공유 링크. 로그인 없이 이름+투표, 브라우저 쿠키(`hk_voter`)나 같은 이름이면 이전 표를 교체

8. 투표를 만든 브라우저(`owner` = `hk_voter` 쿠키)만 '여기로 결정' → `decided` 저장, 투표 마감, 모두에게 결정 식당·길찾기 배너. 결정 취소(reopen) 가능
   - 자동 마감(11/24): 추천 때 적은 인원(`expected_voters` = conditions.people, 2명 이상) 기준. **남은 사람이 모두 2등에 투표해도 1등을 못 따라잡으면** 자동 결정(`_clinched`, `decided.auto`) — 예: 4명 중 3명 같은 곳이면 바로 마감. 표가 들어올 때뿐 아니라 화면·상태 주소를 열 때도 확인(`settle`). 동점이면 만든 사람에게 고르라고 안내. 만든 사람이 결정을 취소하면 `auto_close=False`로 자동 마감 끔. 화면에 'N명 중 M명 투표' 진행 막대
   - 자동 새로고침(폴링): 투표 화면이 **3초**마다 `/vote/<id>/state`(투표 상태 해시 `vote_signature`)를 물어 바뀐 경우에만 새로고침. 고르거나 이름 치는 중이면 '새 표가 들어왔어요' 버튼만. 숨은 탭이면 묻지 않음
   - 푸시(WebSocket·SSE)를 안 쓴 이유: 연결마다 스레드를 계속 붙잡는데 무료 서버는 동시 처리 4개(gunicorn 워커 1·스레드 4) → 투표 화면 4명이면 추천 등 다른 요청이 막힘. 바꾸려면 비동기 서버 + DB 알림(LISTEN/NOTIFY)이 필요해 발표 직전엔 위험. 투표는 몇 초 늦어도 되는 기능이라 폴링이 적절 (발표 질문 대비)
9. Gemini 쿼터 보호(`ai_limit_reason`): IP당 10분 6회, 서버 전체 하루 `AI_DAILY_LIMIT`(기본 300). 넘으면 AI 없이 후보 목록. 메모리 기록이라 재시작 시 초기화
10. 공유 미리보기(메신저 공통): base.html Open Graph + X(twitter:card) 태그 + `static/og.png`(1200×630, Pillow로 만든 이미지). 투표 화면은 지역·후보 이름/결정 식당으로 제목·설명 변경
11. 현재 위치로 찾기: 브라우저 GPS → `/api/where`(카카오 coord2regioncode, 법정동 이름) → '현재 위치 · ○○동'. `place_source=gps`면 좌표를 소수 셋째 자리(약 100m)로 흐리고 '바로 찾기'(places.json)에 기록 안 함. https/localhost에서만 동작
12. 시간대: 첫 화면 JS가 휴대폰 시각으로 미리 선택 (5~15시 점심, 15~22시 저녁, 그 외 늦은 밤). 서버(Render)는 UTC라 서버에서 정하면 틀림
13. 추천 만족도(`feedback_store.py`): AI 추천마다 rec_id 한 줄(상황·재추천 횟수·분위기 문장 유무·GPS 여부만, 장소·문장 저장 안 함) → 결과 화면 👍👎(+👎 이유 5개 중 선택) → 투표 만들면 vote ID 연결. DB `feedback` 표 / 파일 `data/feedback.json`
14. 관리용 통계 `/admin/stats?key=ADMIN_KEY` (환경변수 ADMIN_KEY 없으면 404): 👍 비율, 재추천 단계별·상황별, 👎 이유, 투표로 이어진 비율, 투표 참여·결정 비율 → 보고서·발표 근거
15. 디자인(11/17): 로고 = 두 공기 + 하트 김(base.html SVG, '짠' 애니메이션), 인트로(탭마다 1회, sessionStorage), 기다리는 화면(data-loading 폼), 선 아이콘 세트 `icons.py`(`{{ icon("pin") }}`, 이모지 대신), 앱 아이콘·manifest(홈 화면 추가), 시간대는 선택 사항(기본 '상관없음'), 첫 화면 인사말은 휴대폰 시각 기준. 아이콘 PNG·og.png는 SVG를 Edge 헤드리스로 렌더링해 만듦
16. 넓은 지역(`BROAD_AREAS`, 17개 시·도 + '전남광주통합특별시'): '제주도'·'부산' 등 시·도 이름이면 기준점이 산·관광지 한 점이라 식당 0곳 → 대표 번화가 버튼을 보여주고 처음 조건 그대로 다시 추천. 대표 장소 이름은 카카오 검색 1순위가 맞는지 확인해 둠(동 이름은 같은 이름이 여러 도시에 있어 도시명을 붙임)
17. 근처 식당 0곳이면(산·바다·외곽) `nearest_restaurant`(카테고리 검색 반경 20km, 거리순 1곳)로 가장 가까운 식당가 동네로 기준점을 옮기고 안내
18. 혼밥: 인원 1명 고정(화면 잠금 + 서버에서도 1), 결과 화면 투표 버튼 숨김
19. 자유 문장 칸('이외에도 원하는 게 있나요?', 예전 '원하는 분위기'): 문장(+덧붙인 요청)이 있으면 Gemini가 먼저 **근처 메뉴판(세부 종류 목록) 안에서** 음식 종류·음식 이름·뺄 종류를 고름(`interpret_request`, 서버가 메뉴판 밖 이름 제거, 같은 문장은 캐시) → 그 음식점을 후보 20곳 앞에(이름 일치 > 구체 종류 > 넓은 종류 순) → 2단계 추천. 근처에 없으면 AI가 솔직히 말함. AI 사용 제한에 걸리면 메뉴판 단어 일치로만. 사용 제한은 추천 1번 = 1회(해석 포함)

수집 세부:
- 칸은 동시에 4개씩 수집 (`COLLECT_WORKERS`, 의정부역 1km 빈 상태 기준 50초 → 19초)
- 카카오 카테고리 검색(FD6)은 한 번에 최대 45곳 → 넘치면 구역을 4칸으로 재귀 분할 (최소 0.0005도)
- 간식·식품판매·도시락은 제외 (`EXCLUDE_KINDS`)
- 칸 경계에 걸친 식당은 좌표가 속한 칸에만 저장 → 중복 없음
- `partial` 칸은 `covered` 범위 밖만 추가 수집

AI 세부 (`ai_recommend.py`):
- 패키지는 `google-genai` (안내문의 `google-generativeai`는 예전 패키지)
- 모델 순서: `.env`의 `GEMINI_MODEL` → `gemini-3.5-flash-lite` → `gemini-3.8-flash` → `gemini-3.6-flash` (2.5 Flash는 신규 계정 사용 불가)
- 모델당 20초, 전체 40초 제한. `HAMKKI_DEBUG=1`(기본)이면 실패 원인을 화면에 표시, 배포 시 0

저장소 (`db.py`):
- `DATABASE_URL`이 있으면 Postgres(Render 무료 `hamkki-db`, render.yaml `databases:`), 없으면 파일. 코드는 어느 쪽이든 같음
- 칸: 빌드 때 모은 칸은 이미지 안 파일, 실행 중 모은 칸은 DB. 읽을 때 합치고 DB 우선. DB 장애 시 칸은 파일로 대신, 투표는 503 안내
- 빌드 수집은 `DATABASE_URL=`로 비워서 파일에 저장 (이미지 포함)
- 드라이버는 pg8000(순수 파이썬). psycopg는 학교 PC 응용 프로그램 제어 정책이 DLL을 막음
- 무료 DB는 생성 30일 뒤 만료 → 학기 후에도 쓰려면 Neon 등으로 `DATABASE_URL`만 교체

## 꼭 지킬 것

- 앱은 반드시 venv 파이썬으로 실행 (다른 파이썬은 truststore가 없어 Gemini가 인증서 오류. 대비책으로 `VERIFY_X509_STRICT`만 해제)
- 시험은 실제 `data/`를 오염시킬 수 있다 (`remember_place`가 장소를 기록함). 시험 후 `data/places.json` 확인
- `.env` 내용을 읽어서 출력하거나 대화에 쓰지 않는다. `.env`, `data/`, `__pycache__`는 커밋 금지.
- **학교 네트워크는 HTTPS를 중간에서 검사한다** → `CERTIFICATE_VERIFY_FAILED`. 해결은 `truststore`(OS 인증서 사용)로 한다. `verify=False` 등 인증서 검사를 끄는 방법은 쓰지 않는다. Gemini 클라이언트에는 `HttpOptions(client_args={"verify": ssl_context})`로 truststore 컨텍스트를 넘긴다.
- 네이버·카카오 웹페이지 크롤링 금지 (약관 위반·차단). 공식 API만 쓴다. 리뷰 원문 저장 금지.
- AI가 없는 식당을 지어내지 않도록, AI는 넘겨준 후보 안에서만 고르고 서버가 검사하는 구조를 유지한다.
- 새 외부 호출에는 항상 timeout을 둔다 (화면이 멈췄던 적이 있음).
- 화면은 모바일 기준(최대 폭 480px), 색은 `style.css`의 `--hk-*` 변수 사용.

## 진행 현황 (2026-11-24 기준) — 기능 개발 끝, 제출 파일 완성, 12/1 발표 준비 단계

완료 (모두 배포 서버에 반영):
- 기능: 칸 단위 자동 수집, 장소 검색·현재 위치·넓은 지역(대표 번화가)·식당 없는 곳(가까운 식당가), 2단계 Gemini 추천(문장 해석 → 3곳), 지도, 재추천, 링크 투표(자동 마감·3초 자동 반영·만든 사람 결정), 만족도 👍👎 + 관리용 통계, AI 사용 제한, 혼밥 처리, 공유 미리보기(메신저 공통), 디자인(로고·인트로·선 아이콘·앱 아이콘)
- 배포: Render 웹 서비스 + **Render 무료 Postgres `hamkki-db`(11/24 생성, 12/24쯤 만료)**. 서버 재시작 뒤 투표 유지 확인함
- GitHub 저장소 공개: `EOBG-eg/hamkki`, 1회차 `EOBG-eg/shuttle-bus-reservation_lyb`, 2회차 `EOBG-eg/lab-seat-reservation_lyb` (1·2회차는 수업 제출본에서 DB 제외·세션 비밀값 환경변수화·README 추가만 한 별도 저장소. hamkki와 섞지 말 것)
- 문서 (claude.ai 문서, 같은 계정이면 어디서든 열림):
  - 결과 보고서(2회차 보고서와 같은 구성, AI 기능 설명·검증 기록 24건·디자인 Before/After): https://claude.ai/artifact/SLmKmtDXFHJHUG1vki1Nm4
  - 데모데이 시연 대본(3분 대본·준비 체크리스트·예상 질문·직접 찾고 고친 것·문제 대처): https://claude.ai/artifact/LKShfWPgwFN1LpsE3TMeJP
  - 포트폴리오 카드 1·2·3회차(1회차 AI 도구=Gemini, 2·3회차=Claude): https://claude.ai/artifact/JDirBBNGGpnyTtGrqDxWPo
- 저장소 문서: `docs/검증기록.md`(전체 37건), `docs/AI기능설명.md`, README
- **제출 파일**: 학교 PC 문서 폴더의 `2221042_이유빈_3회차.zip` (00_설명서.txt, 01_결과보고서.docx, 02_AI기능설명.md, 03_검증기록_전체.md, 04_포트폴리오카드.docx, 05_소스코드/). 시연 대본은 제출물에서 뺌(발표는 시연으로). 문서를 고치면 claude.ai 문서를 docx로 내보내 zip을 다시 만들어야 함

데이터(로컬): 칸 173개, 식당 8,756곳(의정부시 전체 + 홍대·강남·상우고 주변). 배포 서버는 빌드 때 의정부시를 미리 수집 + 실행 중 모은 칸은 Postgres

남은 일:
1. **제출: 11/30(월) 22:00까지 카카오워크 1:1로 zip 업로드** (사용자). 올리기 전 01_결과보고서.docx를 Word로 열어 그림·표 확인. 한글 표지는 선택
2. **12/1(화) 데모데이**: 대본으로 2~3번 연습(3분 이내), 예상 질문 답변 연습, 핵심 코드 3곳(후보 고르기 `find_candidates`, AI 결과 검사 `recommend_with_ai`, 문장 해석 `interpret_request`) 설명 연습, 당일 10분 전 서버 깨우기·일반 창+시크릿 창·핫스팟·예비 영상
3. 14주차부터 새 기능 넣지 않기 (발표 직전 위험). 버그만 고침
4. 선택: 실사용 통계(과제 요구사항 아님), 학기 후 서비스 유지 시 Postgres를 Neon 등 만료 없는 DB로(`DATABASE_URL`만 교체)

발표 질의응답 대비: AI 검증 과정은 직접 써 보고 찾아 고친 사례(초밥 후보 0→14곳, 제주도 검색, 투표 자동 마감)로 설명한다 (시연 대본의 '직접 찾고 고친 것')

지역 미리 수집 우선순위: 의정부시 전체 → 노원·도봉·수유·창동 → 서울 약속 장소(건대·성수·잠실·신촌·혜화 등) → 경기·인천 중심가 → 지방 대도시. 전국 일괄 수집은 하지 않는다(호출량·용량·약관). 나머지는 검색 시 자동 수집에 맡긴다.

## 알려진 한계

- 카카오 API에는 분위기·리뷰·영업시간이 없다 → AI는 이름·종류·거리로만 추정 (프롬프트에서 단정 금지)
- 폐업 식당 미처리 (추천 직전 최종 3곳 재확인, 공공데이터 일반음식점 인허가 대조 검토 중)
- 카카오 로컬 API 데이터를 서버에 저장해도 되는 범위는 카카오 데브톡 확인 필요
- 투표는 같은 이름을 적으면 다른 사람 표를 바꿀 수 있다 (로그인 없는 구조의 한계, 친구끼리 쓰는 용도라 허용)
- 웹 자동 수집 하루 한도는 메모리에만 있어 앱을 재시작하면 초기화됨
