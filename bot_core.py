"""
Uni-VOC 장학금 매칭 챗봇 - 공통 로직 모듈.

CLI(chatbot.py)와 웹 UI(app.py)가 이 모듈을 함께 가져다 쓴다.
LLM 호출 함수들 + 한 세션의 대화 상태를 들고 있는 ChatSession 클래스가 여기 있음.
"""
import os
import sys

from google import genai

from schemas import SlotState, SlotExtraction, Scholarship
from matching import load_db, hard_filter, soft_filter

MODEL = "gemini-3.6-flash"

CHAT_SYSTEM_PROMPT = """너는 대학생 대상 장학금 안내 챗봇이다. 친근하고 간결한 반말/구어체 말투를 쓰되 예의는 지킨다.
- 확인되지 않은 장학금 조건을 지어내지 않는다.
- 한 번에 너무 많은 질문을 던지지 말고, 자연스럽게 1~2개씩 물어본다.
- 거주지역을 물어볼 땐 "혹시 본인이나 부모님이 특정 지역에 오래 거주하셨어?"처럼 부드럽게 접근한다.
"""

REQUIRED_SLOTS = ["grade", "major", "region", "gpa", "income_bracket", "special_conditions_free_text"]

SLOT_QUESTION_HINT = {
    "grade": "학년",
    "major": "학과",
    "region": "본인 또는 부모님 거주 지역 (몇 년 이상 사셨는지 포함)",
    "gpa": "직전 학기 평점 (4.5 만점 기준)",
    "income_bracket": "한국장학재단 학자금지원구간 (모르면 모른다고 해도 됨)",
    "special_conditions_free_text": "기초생활수급자/차상위/한부모/다자녀/장애/국가보훈 등 해당사항 있는지",
}

GREETING = "안녕! 장학금 찾는 거 도와줄게. 몇 학년이야?"


def get_client() -> genai.Client:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("에러: GEMINI_API_KEY 환경변수가 설정되어 있지 않습니다. README.md를 참고하세요.")
        sys.exit(1)
    return genai.Client(api_key=api_key)


def extract_slots(client: genai.Client, history: str, current: SlotState) -> SlotState:
    prompt = f"""아래는 학생과 장학금 안내 챗봇의 대화 이력이다.
대화에서 새로 명시적으로 언급된 정보만 채워라. 언급 안 된 항목은 null로 둬라.
숫자로 물어본 게 아니어도 유추 가능하면 채워도 된다 (예: "3학년이요" -> grade: "3학년").
성적은 학생이 100점 만점이나 등급으로 말하면 4.5 만점 기준으로 대략 환산해서 넣어라.

[이미 알고 있는 정보]
{current.model_dump_json()}

[대화 이력]
{history}
"""
    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config={
            "response_mime_type": "application/json",
            "response_schema": SlotExtraction,
            "temperature": 0,
        },
    )
    extracted = SlotExtraction.model_validate_json(response.text)
    merged = current.model_copy()
    for field in SlotExtraction.model_fields:
        value = getattr(extracted, field)
        if value is not None:
            setattr(merged, field, value)
    return merged


def missing_slots(state: SlotState) -> list[str]:
    return [s for s in REQUIRED_SLOTS if getattr(state, s) in (None, "")]


def generate_followup_question(client: genai.Client, history: str, missing: list[str]) -> str:
    hints = ", ".join(f"{s}({SLOT_QUESTION_HINT[s]})" for s in missing[:2])
    prompt = f"""아래 대화 이력을 보고, 아직 모르는 정보인 [{hints}] 중 1~2개에 대해 자연스럽게 이어서 질문해라.
이미 아는 정보는 다시 묻지 마라. 질문만 짧게 해라.

[대화 이력]
{history}
"""
    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config={"system_instruction": CHAT_SYSTEM_PROMPT, "temperature": 0.5},
    )
    return response.text.strip()


def generate_candidate_presentation(client: genai.Client, matches: list[tuple[Scholarship, str]]) -> str:
    listing = "\n".join(
        f"{i+1}. {sch.program_name} ({sch.amount or '금액 정보 없음'})"
        + (f" - 참고: {note}" if note else "")
        for i, (sch, note) in enumerate(matches)
    )
    prompt = f"""학생에게 아래 매칭된 장학금 후보를 번호로 제시하고, 어떤 걸로 진행할지 물어봐라.
"참고:"가 붙은 항목은 아직 확인이 더 필요하다는 뉘앙스를 자연스럽게 섞어라.

[매칭된 장학금 목록]
{listing}
"""
    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config={"system_instruction": CHAT_SYSTEM_PROMPT, "temperature": 0.4},
    )
    return response.text.strip()


ACTION_GUIDE_PROMPT = """너는 대학 행정 안내 AI다. 학생이 선택한 장학금에 대해
지금 당장 해야 할 첫 액션부터 짚어주는 실행 가이드를 준다.
- 제출서류 중 가장 먼저 준비해야 할 것부터 안내한다.
- 말투는 "~부터 준비합시다!"처럼 행동을 재촉하는 친근한 톤을 쓴다.
- 제공된 정보 안에서만 안내하고, 없는 절차를 지어내지 않는다.
- 마지막에 신청 방법과 마감일(있으면)을 다시 한 줄로 요약해준다.
"""


def generate_action_guide(client: genai.Client, sch: Scholarship) -> str:
    docs = ", ".join(sch.required_documents) if sch.required_documents else "별도 제출서류 없음"
    windows = "; ".join(
        f"{w.label or ''} {w.apply_start or '상시'}~{w.apply_end or '상시'}".strip()
        for w in sch.apply_windows
    ) or "정보 없음"
    prompt = f"""[선택된 장학금]
이름: {sch.program_name}
지원금액: {sch.amount}
필요서류: {docs}
신청방법: {sch.how_to_apply}
신청기간: {windows}
참고사항: {sch.notes or '없음'}
"""
    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config={"system_instruction": ACTION_GUIDE_PROMPT, "temperature": 0.3},
    )
    return response.text.strip()


ASSIST_SYSTEM_PROMPT = """너는 학생이 장학금 신청을 끝까지 무사히 마치도록 옆에서 계속 도와주는 조수다.
- 이미 안내한 서류 준비, 신청 절차, 마감일 관련 질문에 계속 답해준다.
- 지어내지 말고, 모르는 건 "장학팀에 직접 문의해봐"라고 솔직히 안내한다.
- 학생이 힘들어하거나 막막해하면 다음에 뭘 하면 되는지 구체적으로 짚어주며 격려한다.
- 친근한 반말/구어체를 쓰되 예의는 지킨다.
"""


def generate_assist_reply(client: genai.Client, history: str, selected: Scholarship | None) -> str:
    context = ""
    if selected:
        docs = ", ".join(selected.required_documents) if selected.required_documents else "별도 제출서류 없음"
        context = f"""[학생이 지금 준비 중인 장학금]
이름: {selected.program_name}
지원금액: {selected.amount}
필요서류: {docs}
신청방법: {selected.how_to_apply}
참고사항: {selected.notes or '없음'}

"""
    prompt = f"""{context}[대화 이력]
{history}

위 대화에서 학생의 마지막 메시지에 자연스럽게 이어서 답해라.
"""
    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config={"system_instruction": ASSIST_SYSTEM_PROMPT, "temperature": 0.5},
    )
    return response.text.strip()


class ChatSession:
    """대화 한 건(한 학생)의 상태를 들고 있는 세션. CLI/웹 UI가 공통으로 사용."""

    def __init__(self, client: genai.Client, db: list[Scholarship]):
        self.client = client
        self.db = db
        self.state = SlotState()
        self.history: list[str] = []
        self.matches: list[tuple[Scholarship, str]] = []
        self.selected: Scholarship | None = None
        self.stage = "slot_filling"

    def greeting(self) -> str:
        return GREETING

    def handle_message(self, user_msg: str) -> str:
        """학생 메시지 하나를 받아서 AI 응답 텍스트를 리턴한다.
        내부적으로 self.state/self.stage/self.matches/self.selected를 갱신한다."""
        user_msg = user_msg.strip()
        self.history.append(f"학생: {user_msg}")
        convo = "\n".join(self.history)

        # 전역 명령어: 어느 단계에서든 처음부터 다시 검색 가능
        if user_msg.replace(" ", "") in ("처음부터", "다시검색", "리셋"):
            self.state = SlotState()
            self.matches = []
            self.selected = None
            self.stage = "slot_filling"
            reply = "좋아, 처음부터 다시 해보자! 몇 학년이야?"
            self.history.append(f"AI: {reply}")
            return reply

        if self.stage == "slot_filling":
            self.state = extract_slots(self.client, convo, self.state)
            missing = missing_slots(self.state)
            if missing:
                reply = generate_followup_question(self.client, convo, missing)
            else:
                candidates = hard_filter(self.db, self.state)
                self.matches = soft_filter(self.client, candidates, self.state)
                if not self.matches:
                    reply = (
                        "지금 조건에 딱 맞는 장학금을 DB에서 못 찾았어. 그래도 계속 물어봐도 돼 — "
                        "장학팀에 뭘 문의하면 좋을지 같이 생각해볼 수도 있고, "
                        "'처음부터'라고 치면 정보 다시 입력해서 재검색할 수도 있어."
                    )
                    self.stage = "assist"
                else:
                    reply = generate_candidate_presentation(self.client, self.matches)
                    self.stage = "matched"
            self.history.append(f"AI: {reply}")
            return reply

        if self.stage == "matched":
            if user_msg.replace(" ", "") in ("목록", "리스트", "list"):
                reply = generate_candidate_presentation(self.client, self.matches)
                self.history.append(f"AI: {reply}")
                return reply
            digits = "".join(ch for ch in user_msg if ch.isdigit())
            if not digits or not (1 <= int(digits) <= len(self.matches)):
                reply = "번호로 골라주면 바로 도와줄게! (예: 1)"
                self.history.append(f"AI: {reply}")
                return reply
            idx = int(digits) - 1
            self.selected, _ = self.matches[idx]
            guide = generate_action_guide(self.client, self.selected)
            reply = guide + "\n\n[시스템] 이제부터는 서류 준비하면서 궁금한 거 편하게 물어봐도 돼."
            self.history.append(f"AI: {guide}")
            self.stage = "assist"
            return reply

        # stage == "assist"
        if user_msg.replace(" ", "") in ("목록", "리스트", "list") and self.matches:
            reply = generate_candidate_presentation(self.client, self.matches)
            self.history.append(f"AI: {reply}")
            self.stage = "matched"
            return reply
        reply = generate_assist_reply(self.client, convo, self.selected)
        self.history.append(f"AI: {reply}")
        return reply
