"""
scholarship_db.json 구조 및 대화 상태(슬롯)를 정의하는 Pydantic 모델.
DB 필드명은 실제 scholarship_db.json과 1:1로 맞춰져 있음 - 여기 필드명을 바꾸면
DB json도 같이 바꿔야 함.
"""
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel


class ApplyWindow(BaseModel):
    label: Optional[str] = None
    apply_start: Optional[str] = None  # "YYYY-MM-DD" 또는 null
    apply_end: Optional[str] = None    # "YYYY-MM-DD" 또는 null


class Eligibility(BaseModel):
    student_status: list[str] = []
    grade_restriction: Optional[str] = None
    eligible_majors: list[str] = []
    gpa_requirement_raw: Optional[str] = None
    gpa_requirement_percent: Optional[float] = None
    income_bracket_max: Optional[int] = None
    income_bracket_priority_note: Optional[str] = None
    region_condition: Optional[str] = None
    special_conditions: list[str] = []
    other_conditions: Optional[str] = None


class Scholarship(BaseModel):
    id: str
    source_notice: Optional[str] = None
    program_name: str
    benefit_type: str  # "장학금" | "근로장학" | "대출" | "이자지원" | "등록금지원" | "대출상환지원"
    is_open_application: bool
    apply_windows: list[ApplyWindow] = []
    eligibility: Eligibility
    amount: Optional[str] = None
    required_documents: list[str] = []
    how_to_apply: Optional[str] = None
    notes: Optional[str] = None
    source_url: Optional[str] = None


class SlotState(BaseModel):
    """대화하면서 채워지는 학생 정보. 값이 없으면 None(모름)으로 둔다."""
    grade: Optional[str] = None                 # 예: "3학년"
    major: Optional[str] = None                 # 예: "컴퓨터공학과"
    region: Optional[str] = None                 # 예: "대구광역시 서구" (본인 또는 부모 주소지)
    gpa: Optional[float] = None                  # 4.5 만점 기준
    income_bracket: Optional[str] = None          # 예: "3구간" 또는 "모름"
    special_conditions_free_text: Optional[str] = None  # 학생이 자기 상황을 자유롭게 말한 문장 그대로 저장
    has_student_loan: Optional[bool] = None       # 학자금대출 이용 여부 (이자지원류 매칭용)


class SlotExtraction(BaseModel):
    """LLM이 대화에서 슬롯을 추출할 때 쓰는 응답 스키마 (SlotState와 필드가 같음)."""
    grade: Optional[str] = None
    major: Optional[str] = None
    region: Optional[str] = None
    gpa: Optional[float] = None
    income_bracket: Optional[str] = None
    special_conditions_free_text: Optional[str] = None
    has_student_loan: Optional[bool] = None


class SoftEligibilityCheck(BaseModel):
    eligible: bool
    reason: str
