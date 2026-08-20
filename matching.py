"""
scholarship_db.json 로드 + 규칙기반 1차 필터 + LLM 소프트 매칭 2차 필터.
"""
import json
from datetime import date
from pathlib import Path

from google import genai

from schemas import Scholarship, SlotState, SoftEligibilityCheck

DB_PATH = Path(__file__).parent / "scholarship_db.json"

# 기본적으로 "돈을 신규로 받는" 성격의 장학금/근로장학/등록금지원만 매칭 대상으로 삼는다.
# 대출/이자지원/대출상환지원은 "학자금대출 받은 적 있어?"라고 물어봤을 때만 별도로 보여준다.
DEFAULT_BENEFIT_TYPES = {"장학금", "근로장학", "등록금지원"}
LOAN_RELATED_BENEFIT_TYPES = {"대출", "이자지원", "대출상환지원"}


def load_db() -> list[Scholarship]:
    with open(DB_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    return [Scholarship.model_validate(item) for item in raw]


def _is_within_any_window(sch: Scholarship, today: date) -> bool:
    """apply_windows 중 하나라도 오늘 기준 아직 마감 전이면 True.
    apply_end가 없는(상시) 경우도 True로 취급."""
    if not sch.apply_windows:
        return True
    for w in sch.apply_windows:
        if not w.apply_end:
            return True
        try:
            if date.fromisoformat(w.apply_end) >= today:
                return True
        except ValueError:
            continue
    return False


def hard_filter(
    db: list[Scholarship],
    state: SlotState,
    today: date | None = None,
    include_loan_related: bool = False,
) -> list[tuple[Scholarship, list[str]]]:
    """1차 규칙 기반 필터. 명확히 틀린 것만 걸러내고, 애매한 건 통과시키되
    '확인 필요' 사유를 flags에 남긴다. 반환: (장학금, 확인필요_사유_리스트) 튜플 리스트."""
    today = today or date.today()
    allowed_types = DEFAULT_BENEFIT_TYPES | (LOAN_RELATED_BENEFIT_TYPES if include_loan_related else set())

    results = []
    for sch in db:
        if sch.benefit_type not in allowed_types:
            continue
        if not sch.is_open_application:
            continue
        if not _is_within_any_window(sch, today):
            continue

        e = sch.eligibility
        flags: list[str] = []

        # 성적: 조건이 있는데 학생 GPA를 모르면 일단 후보에 넣고 확인 플래그만 남김
        if e.gpa_requirement_percent is not None:
            if state.gpa is None:
                flags.append("성적 정보 필요")
            else:
                student_percent = state.gpa / 4.5 * 100
                if student_percent < e.gpa_requirement_percent - 0.5:  # 소수점 오차 허용
                    continue  # 명백히 미달이면 제외

        # 소득분위: 상한이 있는데 모르면 확인 플래그, 초과가 확실하면 제외
        if e.income_bracket_max is not None:
            if state.income_bracket in (None, "모름"):
                flags.append("학자금지원구간 확인 필요")
            else:
                digits = "".join(ch for ch in state.income_bracket if ch.isdigit())
                if digits:
                    try:
                        if int(digits) > e.income_bracket_max:
                            continue
                    except ValueError:
                        flags.append("학자금지원구간 확인 필요")

        # 학과/지역/학년/특례조건처럼 자유텍스트인 항목은 여기서 걸러내지 않고
        # soft_filter(LLM)로 넘긴다. 대신 "조건이 아예 존재한다"는 사실만 플래그로 남김.
        if e.eligible_majors:
            flags.append("학과 조건 확인 필요")
        if e.region_condition:
            flags.append("거주지역 조건 확인 필요")
        if e.grade_restriction:
            flags.append("학년 조건 확인 필요")
        if e.special_conditions:
            flags.append("특례 조건 확인 필요")

        results.append((sch, flags))

    return results


def soft_filter(
    client: genai.Client,
    candidates: list[tuple[Scholarship, list[str]]],
    state: SlotState,
    model: str = "gemini-3.6-flash",
) -> list[tuple[Scholarship, str]]:
    """flags가 있는(=자유텍스트 조건이 있는) 후보만 LLM한테 한 번 더 확인시킨다.
    반환: (장학금, 참고메모) 리스트. 완전히 부적격이라고 판단되면 제외한다."""
    final: list[tuple[Scholarship, str]] = []

    student_summary_parts = []
    if state.grade:
        student_summary_parts.append(f"학년: {state.grade}")
    if state.major:
        student_summary_parts.append(f"학과: {state.major}")
    if state.region:
        student_summary_parts.append(f"거주지(본인/부모): {state.region}")
    if state.income_bracket:
        student_summary_parts.append(f"학자금지원구간: {state.income_bracket}")
    if state.special_conditions_free_text:
        student_summary_parts.append(f"기타 상황: {state.special_conditions_free_text}")
    student_summary = ", ".join(student_summary_parts) if student_summary_parts else "정보 없음"

    for sch, flags in candidates:
        if not flags:
            # 확인이 필요한 자유텍스트 조건이 아예 없으면 바로 통과
            final.append((sch, ""))
            continue

        e = sch.eligibility
        condition_text = "\n".join(
            filter(None, [
                f"학과 조건: {', '.join(e.eligible_majors)}" if e.eligible_majors else "",
                f"거주지역 조건: {e.region_condition}" if e.region_condition else "",
                f"학년 조건: {e.grade_restriction}" if e.grade_restriction else "",
                f"특례 조건(하나라도 해당하면 충족): {', '.join(e.special_conditions)}" if e.special_conditions else "",
            ])
        )

        prompt = f"""학생 정보와 장학금 자격조건을 비교해서, 이 학생이 지원 가능한지 판단하라.
정보가 부족해서 확실히 판단할 수 없으면 eligible=true로 두되(배제하지 않음),
reason에 "확인 필요: ..."라고 명확히 써라. 명백히 조건에 안 맞는 게 확실할 때만 eligible=false로 하라.

[학생 정보]
{student_summary}

[장학금: {sch.program_name}]
{condition_text}
"""
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "response_schema": SoftEligibilityCheck,
                "temperature": 0,
            },
        )
        check = SoftEligibilityCheck.model_validate_json(response.text)
        if check.eligible:
            final.append((sch, check.reason))

    return final
