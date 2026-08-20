"""
Uni-VOC 장학금 매칭 챗봇 - 웹 UI 버전 (FastAPI)

로컬 실행: python app.py
그 다음 브라우저에서 http://localhost:8000 열기.
외부 배포: README.md의 "9. 외부 배포" 섹션 참고 (Render 등).
사전 준비: GEMINI_API_KEY 환경변수 설정 (README.md 참고)

방문자(브라우저)마다 쿠키로 세션을 분리해서 들고 있음 - 여러 학생이 동시에
접속해도 서로 대화 상태가 섞이지 않음. 단, 세션은 서버 메모리에만 있어서
서버 재시작하면 전부 초기화됨 (MVP 단계에서는 이 정도로 충분).
"""
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from matching import load_db
from bot_core import get_client, ChatSession

_client = None
_db = None

SESSION_COOKIE = "uvoc_session"
_sessions: dict[str, ChatSession] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _client, _db
    _client = get_client()
    _db = load_db()
    print(f"[시스템] scholarship_db.json에서 {len(_db)}개 장학금 로드 완료")
    yield


app = FastAPI(title="Uni-VOC 장학금 챗봇", lifespan=lifespan)


def _get_session(request: Request, response: Response) -> ChatSession:
    """쿠키에 담긴 세션 ID로 이 방문자 전용 ChatSession을 찾아오거나 새로 만든다."""
    sid = request.cookies.get(SESSION_COOKIE)
    if not sid or sid not in _sessions:
        sid = uuid.uuid4().hex
        _sessions[sid] = ChatSession(_client, _db)
        response.set_cookie(
            SESSION_COOKIE,
            sid,
            httponly=True,
            samesite="lax",
            max_age=60 * 60 * 24 * 7,  # 7일
        )
    return _sessions[sid]


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str
    stage: str


@app.get("/")
def index():
    # 화면(디자인) 바뀔 때마다 브라우저가 옛날 버전을 캐싱해서 안 바뀐 것처럼 보이는 걸 방지
    return FileResponse("static/index.html", headers={"Cache-Control": "no-store, must-revalidate"})


@app.get("/api/init", response_model=ChatResponse)
def init(request: Request, response: Response):
    """페이지를 새로 열었을 때 인삿말 + 현재 stage를 돌려준다 (이 방문자의 세션은 유지됨)."""
    session = _get_session(request, response)
    if not session.history:
        return ChatResponse(reply=session.greeting(), stage=session.stage)
    # 이미 대화 중이었으면 마지막 AI 메시지를 다시 보여줌
    last_ai = next((m[4:] for m in reversed(session.history) if m.startswith("AI: ")), session.greeting())
    return ChatResponse(reply=last_ai, stage=session.stage)


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest, request: Request, response: Response):
    session = _get_session(request, response)
    user_msg = req.message.strip()
    if user_msg.lower() in ("종료", "exit", "quit"):
        # 웹 UI에서는 "종료"를 서버를 끄는 용도로 쓰지 않고, 그냥 인사만 하고
        # 세션은 유지한다 (브라우저 닫으면 되니까).
        return ChatResponse(reply="오케이, 언제든 다시 물어봐! 화이팅!", stage=session.stage)
    reply = session.handle_message(user_msg)
    return ChatResponse(reply=reply, stage=session.stage)


@app.post("/api/reset", response_model=ChatResponse)
def reset(request: Request, response: Response):
    sid = request.cookies.get(SESSION_COOKIE)
    if not sid:
        sid = uuid.uuid4().hex
        response.set_cookie(SESSION_COOKIE, sid, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 7)
    _sessions[sid] = ChatSession(_client, _db)
    session = _sessions[sid]
    return ChatResponse(reply=session.greeting(), stage=session.stage)


app.mount("/static", StaticFiles(directory="static"), name="static")


if __name__ == "__main__":
    import uvicorn

    # 로컬 실행 시 127.0.0.1:8000. 배포 플랫폼은 PORT 환경변수를 넘겨줌 (README 9번 참고).
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
