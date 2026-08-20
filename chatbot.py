"""
Uni-VOC 장학금 매칭 챗봇 - CLI(터미널) 버전

실행: python chatbot.py
사전 준비: GEMINI_API_KEY 환경변수 설정 (README.md 참고)

실제 로직(LLM 호출, 매칭, 대화 상태 관리)은 bot_core.py에 있고,
이 파일은 그걸 터미널 입출력에 연결하는 얇은 래퍼일 뿐이다.
(웹 UI 버전은 app.py — bot_core.py를 그대로 재사용함)
"""
from matching import load_db
from bot_core import get_client, ChatSession


def run():
    client = get_client()
    db = load_db()
    print(f"[시스템] scholarship_db.json에서 {len(db)}개 장학금 로드 완료")
    print("[시스템] 대화 중 아무 때나 '목록'(후보 다시 보기), '처음부터'(새로 검색), '종료'를 입력할 수 있어\n")

    session = ChatSession(client, db)
    print(f"AI: {session.greeting()}")

    while True:
        try:
            user_msg = input("학생: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[종료] 다음에 또 보자!")
            break
        if user_msg.lower() in ("종료", "exit", "quit"):
            print("AI: 오케이, 언제든 다시 불러줘. 화이팅!")
            break

        reply = session.handle_message(user_msg)
        print(f"AI: {reply}")


if __name__ == "__main__":
    run()
