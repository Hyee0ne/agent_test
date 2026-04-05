import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

DATA_DIR = Path(__file__).parent.parent / "data" / "sessions"

REQUIRED_FIELDS = [
    "service_description",
    "target_users",
    "target_market",
    "revenue_model",
    "revenue_details",
    "mvp_features",
    "dev_timeline_stack",
    "acquisition_channel",
    "channel_resources",
    "competitors",
]


@dataclass
class Session:
    session_id: str
    messages: list = field(default_factory=list)
    collected_data: dict = field(default_factory=lambda: {k: None for k in REQUIRED_FIELDS})
    is_complete: bool = False
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def update_data(self, field_name: str, value: Any) -> None:
        if field_name in self.collected_data:
            self.collected_data[field_name] = value
            self.updated_at = datetime.now()
            self.is_complete = all(self.collected_data.get(f) for f in REQUIRED_FIELDS)

    def to_collected_data_dict(self) -> dict:
        return dict(self.collected_data)


# 인메모리 세션 저장소
session_store: dict[str, Session] = {}


# ── JSON 직렬화 헬퍼 ──────────────────────────────────────────

def _serialize_content(content: Any) -> Any:
    """message content 안의 ContentBlock 객체를 dict로 변환합니다."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        result = []
        for item in content:
            if isinstance(item, dict):
                result.append(item)
            elif hasattr(item, "model_dump"):
                result.append(item.model_dump())
            else:
                result.append(vars(item))
        return result
    return content


def _serialize_messages(messages: list) -> list:
    return [
        {"role": msg["role"], "content": _serialize_content(msg["content"])}
        for msg in messages
    ]


# ── 저장 / 불러오기 ──────────────────────────────────────────

def save_session(session: Session) -> None:
    """세션을 JSON 파일로 저장합니다."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"{session.session_id}.json"

    data = {
        "session_id": session.session_id,
        "collected_data": session.collected_data,
        "is_complete": session.is_complete,
        "created_at": session.created_at.isoformat(),
        "updated_at": session.updated_at.isoformat(),
        "messages": _serialize_messages(session.messages),
    }

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_session(session_id: str) -> Optional[Session]:
    """JSON 파일에서 세션을 불러옵니다."""
    path = DATA_DIR / f"{session_id}.json"
    if not path.exists():
        return None

    data = json.loads(path.read_text(encoding="utf-8"))
    return Session(
        session_id=data["session_id"],
        messages=data.get("messages", []),
        collected_data=data.get("collected_data", {k: None for k in REQUIRED_FIELDS}),
        is_complete=data.get("is_complete", False),
        created_at=datetime.fromisoformat(data["created_at"]),
        updated_at=datetime.fromisoformat(data["updated_at"]),
    )


def load_all_sessions() -> None:
    """서버 시작 시 data/sessions/ 안의 모든 세션을 메모리에 로드합니다."""
    if not DATA_DIR.exists():
        return
    for path in DATA_DIR.glob("*.json"):
        session_id = path.stem
        session = load_session(session_id)
        if session:
            session_store[session_id] = session


def delete_session_file(session_id: str) -> None:
    path = DATA_DIR / f"{session_id}.json"
    if path.exists():
        path.unlink()
