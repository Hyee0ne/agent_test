import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

from .agent import generate_draft, process_message
from .models import ChatRequest, ChatResponse, CollectedData, DraftResponse, SessionInfo
from .session import Session, session_store, save_session, load_all_sessions, delete_session_file


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_all_sessions()  # 서버 시작 시 저장된 세션 모두 로드
    yield


app = FastAPI(
    title="신유 - 창업 컨설턴트 AI",
    description="창업자와 질의응답을 통해 사업계획 피칭 초안을 생성하는 AI 에이전트",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 정적 파일 서빙
_static = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_static)), name="static")


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(str(_static / "index.html"))


def _to_collected_data_model(data: dict) -> CollectedData:
    return CollectedData(**{k: v for k, v in data.items()})


@app.post("/chat", response_model=ChatResponse, summary="대화 메시지 주고받기")
async def chat(request: ChatRequest):
    """
    신유와 대화합니다.
    - session_id 없이 호출하면 새 세션을 생성합니다.
    - 기존 session_id를 전달하면 이전 대화를 이어갑니다.
    - 10개 항목이 모두 수집되면 피칭 초안이 자동 생성됩니다.
    """
    # 세션 조회 또는 생성
    if request.session_id and request.session_id in session_store:
        session = session_store[request.session_id]
    elif request.session_id:
        # 메모리에 없으면 JSON 파일에서 복원 시도
        from .session import load_session
        session = load_session(request.session_id)
        if session:
            session_store[session.session_id] = session
        else:
            # 파일도 없으면 동일 ID로 새 세션 생성
            session = Session(session_id=request.session_id)
            session_store[session.session_id] = session
    else:
        session = Session(session_id=str(uuid.uuid4()))
        session_store[session.session_id] = session

    response_text = await process_message(session, request.message)
    save_session(session)  # 대화마다 JSON 파일에 저장

    return ChatResponse(
        session_id=session.session_id,
        message=response_text,
        is_complete=session.is_complete,
        collected_data=_to_collected_data_model(session.collected_data),
    )


@app.get(
    "/session/{session_id}",
    response_model=SessionInfo,
    summary="세션 상태 조회",
)
async def get_session(session_id: str):
    """현재 세션의 수집 현황과 완료 여부를 반환합니다."""
    if session_id not in session_store:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없어요.")

    session = session_store[session_id]
    return SessionInfo(
        session_id=session.session_id,
        collected_data=_to_collected_data_model(session.collected_data),
        is_complete=session.is_complete,
        message_count=len(session.messages),
    )


@app.post(
    "/session/{session_id}/draft",
    response_model=DraftResponse,
    summary="피칭 초안 생성",
)
async def create_draft(session_id: str):
    """
    수집된 정보를 바탕으로 피칭 초안을 생성합니다.
    - TAM은 웹서치로 자동 계산됩니다.
    - 3개월 예상 고객 수 및 매출이 포함됩니다.
    - 아직 수집되지 않은 항목이 있어도 호출 가능합니다.
    """
    if session_id not in session_store:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없어요.")

    session = session_store[session_id]
    draft = await generate_draft(session)

    return DraftResponse(draft=draft, session_id=session_id)


@app.delete("/session/{session_id}", summary="세션 삭제")
async def delete_session(session_id: str):
    """세션을 삭제합니다."""
    if session_id not in session_store:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없어요.")
    del session_store[session_id]
    delete_session_file(session_id)
    return {"message": "세션이 삭제됐어요."}


@app.get("/health", summary="헬스 체크")
async def health():
    return {"status": "ok", "agent": "신유 v1.0.0"}
