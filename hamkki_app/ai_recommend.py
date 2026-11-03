"""
함끼(Hamkki) - Gemini AI 추천 (3순위)

서버가 '정확한 조건'으로 걸러낸 후보 목록을 Gemini에게 넘기고,
Gemini가 상황과 분위기 문장을 해석해 3곳을 고르고 추천 이유를 만든다.

안전장치
 - AI는 넘겨준 후보 안에서만 고르게 하고, 돌아온 ID가 실제 후보에 있는지 서버가 다시 확인한다.
 - 키가 없거나, 오류가 나거나, 응답 형식이 이상하면 None을 돌려준다.
   → app.py가 기존 후보 목록을 그대로 보여준다(화면이 깨지지 않음).
"""

import json
import os
import ssl

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
# 모델 이름은 바뀔 수 있어서, 앞에서부터 차례로 시도한다.
# .env에 GEMINI_MODEL을 적으면 그 모델을 가장 먼저 시도한다. (Google AI Studio에서 확인)
FALLBACK_MODELS = ["gemini-3.8-flash", "gemini-3.5-flash", "gemini-2.5-flash"]
_env_model = os.environ.get("GEMINI_MODEL", "").strip()
GEMINI_MODELS = ([_env_model] if _env_model else []) + [m for m in FALLBACK_MODELS if m != _env_model]

PICK_COUNT = 3

# 개발 중에는 오류 원인을 화면에 보여준다. 배포할 때 .env에 HAMKKI_DEBUG=0 을 넣으면 숨겨진다.
DEBUG = os.environ.get("HAMKKI_DEBUG", "1") == "1"

SYSTEM_INSTRUCTION = f"""너는 '함끼'라는 식당 추천 서비스의 추천 담당이야.
사용자의 상황과 조건, 그리고 서버가 미리 걸러낸 식당 후보 목록이 주어진다.

규칙:
1. 반드시 후보 목록 안에서만 {PICK_COUNT}곳을 고른다. 목록에 없는 식당을 지어내지 않는다.
2. 상황(데이트, 가족, 친구, 회식, 혼밥)과 인원, 시간대, 예산, 사용자가 적은 분위기 문장을 함께 고려한다.
3. 후보 정보는 이름, 종류, 세부 종류, 지역 중심까지 거리뿐이다. 이 정보로 알 수 없는 것
   (실제 분위기, 영업시간, 가격, 좌석)을 사실처럼 단정하지 않는다.
   추정할 때는 "~일 가능성이 높아요", "~하기 좋은 메뉴예요"처럼 표현한다.
4. reason은 이 상황의 사용자에게 건네는 친근한 존댓말 한두 문장으로 쓴다. 60자 안팎.
5. 투자, 건강 같은 조언은 하지 않는다.
6. 출력은 JSON만 쓴다.

출력 형식:
{{"summary": "추천 전체를 한 문장으로", "picks": [{{"id": "후보 id", "reason": "추천 이유"}}]}}
"""


def _make_ssl_context():
    """
    Gemini 연결에 쓸 인증서 설정.

    Gemini 패키지는 기본으로 자체 인증서 목록(certifi)만 쓴다. 그래서 학교·회사 네트워크처럼
    HTTPS를 중간에서 검사하는 곳에서는 CERTIFICATE_VERIFY_FAILED 오류가 난다.
    운영체제가 믿는 인증서 목록을 쓰도록 바꾸면 해결된다. 보안 검사를 끄는 것이 아니다.
      - truststore가 설치돼 있으면 그것을 쓰고
      - 없으면 파이썬 기본 설정을 쓴다(Windows에서는 Windows 인증서 저장소를 읽는다)
    """
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        return ssl.create_default_context()


def _build_prompt(conditions, candidates):
    lines = []
    for c in candidates:
        detail = f"/{c['detail']}" if c["detail"] else ""
        lines.append(f"- id={c['id']} | {c['name']} | {c['kind']}{detail} | 중심에서 {c['distance']}m")

    wish = conditions["wish"] or "(없음)"
    return f"""[사용자 조건]
지역: {conditions.get('region', '')} 근처
상황: {conditions['situation']}
인원: {conditions['people']}명
시간대: {conditions['time_slot']}
1인 예산: {conditions['budget'] or '상관없음'}
원하는 음식 종류: {conditions['kind'] or '상관없음'}
분위기 요청: {wish}

[후보 목록 {len(candidates)}곳]
{chr(10).join(lines)}
"""


def recommend_with_ai(conditions, candidates):
    """
    성공하면 {"summary": str, "picks": [후보 dict + "reason"]} 를 돌려준다.
    실패하면 (None, 실패 이유) 를 돌려준다.
    """
    if not GEMINI_API_KEY:
        return None, "GEMINI_API_KEY가 .env에 없어요."
    if len(candidates) < PICK_COUNT:
        return None, "후보가 너무 적어요."

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return None, "google-genai 패키지가 설치되지 않았어요. (pip install -U google-genai)"

    ssl_context = _make_ssl_context()
    try:
        client = genai.Client(
            api_key=GEMINI_API_KEY,
            http_options=types.HttpOptions(
                client_args={"verify": ssl_context},
                async_client_args={"verify": ssl_context},
            ),
        )
    except Exception as e:  # 아주 오래된 google-genai는 client_args를 모른다
        print(f"[AI] 인증서 설정을 적용하지 못해 기본 연결을 씁니다: {e}")
        client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = _build_prompt(conditions, candidates)
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        response_mime_type="application/json",
        temperature=0.7,
    )

    data, errors = None, []
    for model in GEMINI_MODELS:  # 모델 하나가 안 되면 다음 모델로
        try:
            response = client.models.generate_content(model=model, contents=prompt, config=config)
            data = json.loads(response.text)
            print(f"[AI] 모델 {model} 로 추천 성공")
            break
        except json.JSONDecodeError:
            errors.append(f"{model}: 응답이 JSON이 아님")
        except Exception as e:  # 네트워크 오류, 쿼터 초과(429), 없는 모델 이름(404) 등
            errors.append(f"{model}: {type(e).__name__}: {str(e)[:200]}")
        print(f"[AI 오류] {errors[-1]}")

    if data is None:
        if DEBUG:  # 개발 중에는 원인을 화면에도 보여준다
            return None, "AI 추천 실패 — " + " / ".join(errors)
        return None, "AI 추천에 실패했어요. 잠시 후 다시 시도해 주세요."

    # ---- 할루시네이션 방지: 돌아온 ID가 실제 후보인지 확인 ----
    by_id = {c["id"]: c for c in candidates}
    picks, seen = [], set()
    for item in data.get("picks", []):
        pid = str(item.get("id", "")).strip()
        if pid in by_id and pid not in seen:
            seen.add(pid)
            picks.append({**by_id[pid], "reason": str(item.get("reason", "")).strip()})
        if len(picks) == PICK_COUNT:
            break

    if not picks:
        return None, "AI가 후보 밖의 식당을 골라서 결과를 쓰지 않았어요."

    return {"summary": str(data.get("summary", "")).strip(), "picks": picks}, None
