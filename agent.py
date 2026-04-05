import os
import logging
from typing import Optional
import anthropic
from dotenv import load_dotenv
from .session import Session, REQUIRED_FIELDS
from .prompts import build_system_prompt

load_dotenv()

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("shinyou")

client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# ── 툴 정의 ────────────────────────────────────────────────────

SAVE_DATA_TOOL = {
    "name": "save_collected_data",
    "description": (
        "사용자 답변에서 추출한 창업 정보를 세션에 저장합니다. "
        "새로운 정보를 얻을 때마다 즉시 호출하세요."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "field": {
                "type": "string",
                "enum": REQUIRED_FIELDS,
                "description": "저장할 필드명",
            },
            "value": {
                "type": "string",
                "description": "저장할 값 (원문 그대로 자세히 기록)",
            },
        },
        "required": ["field", "value"],
    },
}

WEB_SEARCH_TOOL = {
    "type": "web_search_20260209",
    "name": "web_search",
}

# Q&A: save_collected_data만 (web_search 혼용 시 container_id 오류 발생)
CHAT_TOOLS = [SAVE_DATA_TOOL]
# 초안 생성: web_search 추가
DRAFT_TOOLS = [SAVE_DATA_TOOL, WEB_SEARCH_TOOL]

# 필드별 한국어 설명 (강제 추출 프롬프트용)
FIELD_DESCRIPTIONS = {
    "service_description": "어떤 서비스/제품인지 (한 문장 설명)",
    "target_users": "타겟 사용자가 누구인지",
    "target_market": "국내 / 글로벌 / 국내+글로벌 중 어디를 타겟하는지",
    "revenue_model": "수익 모델 종류 (구독 / 광고 / 수수료 / 건당 중 하나)",
    "revenue_details": (
        "수익 모델의 구체적 수치 "
        "(구독→월구독료, 광고→MAU목표+CPM, 수수료→거래액+수수료율, 건당→건당가격+거래건수)"
    ),
    "mvp_features": "MVP 핵심 기능 (최대 3가지)",
    "dev_timeline_stack": "예상 개발 기간과 기술 스택",
    "acquisition_channel": "첫 고객 획득 채널 (지인/SNS/커뮤니티/콜드아웃리치/광고)",
    "channel_resources": "채널 보유 리소스 (지인수, 팔로워수, 예산 등 구체적 숫자)",
    "competitors": "직접 경쟁사 또는 유사 서비스명",
}


# ── 헬퍼 ───────────────────────────────────────────────────────

def _extract_text(content) -> str:
    """응답 content 블록에서 텍스트만 추출합니다."""
    parts = []
    for block in content:
        if hasattr(block, "type") and block.type == "text":
            parts.append(block.text)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts)


def _handle_tool_uses(content, session: Optional[Session]) -> list:
    """
    tool_use 블록을 순회해 client-side 툴을 실행하고 tool_result 목록을 반환합니다.
    모든 tool_use 블록에 반드시 대응하는 tool_result를 반환해야 API 오류가 없어요.
    """
    tool_results = []
    for block in content:
        if not (hasattr(block, "type") and block.type == "tool_use"):
            continue
        if block.name == "save_collected_data":
            field = block.input["field"]
            value = block.input["value"]
            if session:
                session.update_data(field, value)
                log.info(f"[tool] saved: {field} = {str(value)[:60]}")
            result_text = f"'{field}' 저장 완료"
        else:
            result_text = f"알 수 없는 툴: {block.name}"
        tool_results.append({
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": result_text,
        })
    return tool_results


# ── 핵심: 강제 데이터 추출 ──────────────────────────────────────

async def _extract_and_save(session: Session, user_message: str) -> None:
    """
    사용자 답변에서 미수집 필드를 **별도 API 호출로 강제 추출**해 저장합니다.
    메인 대화 루프와 분리해 데이터 저장을 Claude 재량에 맡기지 않아요.
    """
    uncollected = [f for f in REQUIRED_FIELDS if not session.collected_data.get(f)]
    if not uncollected:
        return

    # 현재 수집 대상 (최대 2개 - revenue_model/revenue_details는 함께 올 수 있음)
    targets = uncollected[:2]
    field_info = "\n".join(f"- {f}: {FIELD_DESCRIPTIONS[f]}" for f in targets)

    log.info(f"[extract] target fields: {targets}, user: {user_message[:60]}")

    try:
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            system=f"""사용자 답변에서 아래 필드 정보를 추출해 save_collected_data 툴로 저장하세요.

추출 대상 필드:
{field_info}

규칙:
- 정보가 명확히 있으면 반드시 툴을 호출하세요
- 여러 필드 정보가 동시에 있으면 각각 저장하세요
- 정보가 없거나 불명확하면 저장하지 마세요""",
            messages=[{"role": "user", "content": user_message}],
            tools=[SAVE_DATA_TOOL],
            tool_choice={"type": "auto"},
        )

        for block in response.content:
            if hasattr(block, "type") and block.type == "tool_use" and block.name == "save_collected_data":
                field = block.input["field"]
                value = block.input["value"]
                session.update_data(field, value)
                log.info(f"[extract] saved: {field} = {str(value)[:60]}")

    except Exception as e:
        log.warning(f"[extract] failed: {e}")


# ── 메인 루프 ──────────────────────────────────────────────────

async def _run_loop(
    messages: list,
    tools: list,
    max_tokens: int,
    session: Optional[Session],
    max_iterations: int = 15,
) -> str:
    """
    Claude API 대화 루프.
    매 반복마다 시스템 프롬프트를 재빌드해 최신 collected_data를 반영합니다.
    """
    last_response = None

    for i in range(max_iterations):
        system = build_system_prompt(session.collected_data) if session else ""

        response = await client.messages.create(
            model="claude-opus-4-6",
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            tools=tools,
        )
        last_response = response
        messages.append({"role": "assistant", "content": response.content})

        log.info(f"[loop iter={i}] stop_reason={response.stop_reason} | collected={[k for k,v in (session.collected_data if session else {}).items() if v]}")

        if response.stop_reason == "end_turn":
            break

        if response.stop_reason == "pause_turn":
            continue

        if response.stop_reason == "tool_use":
            tool_results = _handle_tool_uses(response.content, session)
            if tool_results:
                messages.append({"role": "user", "content": tool_results})
            else:
                break

    if last_response is None:
        return "죄송해요, 응답 생성에 실패했어요."

    return _extract_text(last_response.content) or "죄송해요, 응답을 생성하지 못했어요."


# ── 공개 인터페이스 ────────────────────────────────────────────

async def process_message(session: Session, user_message: str) -> str:
    """사용자 메시지를 처리하고 신유의 응답을 반환합니다."""

    was_complete = session.is_complete

    # 1단계: 이전 대화가 있으면 사용자 답변에서 데이터 강제 추출
    #        (메인 루프에서 Claude가 툴을 안 써도 여기서 저장됨)
    if session.messages:
        await _extract_and_save(session, user_message)

    # 2단계: 사용자 메시지를 히스토리에 추가
    session.messages.append({"role": "user", "content": user_message})

    log.info(f"[process] session={session.session_id[:8]} | collected={session.collected_data}")

    # 3단계: 이번 메시지로 10개 수집이 완료됐으면 피칭 초안 자동 생성 (web_search 포함)
    if not was_complete and session.is_complete:
        log.info(f"[process] all fields collected — auto generating draft")
        return await generate_draft(session)

    # 4단계: 일반 대화 루프
    return await _run_loop(
        messages=session.messages,
        tools=CHAT_TOOLS,
        max_tokens=4096,
        session=session,
    )


async def generate_draft(session: Session) -> str:
    """수집된 데이터로 피칭 초안을 생성합니다. web_search로 TAM을 계산해요."""
    draft_prompt = (
        "지금까지 수집된 모든 정보를 바탕으로 피칭 초안을 생성해주세요. "
        "TAM 계산을 위해 web_search 툴로 시장 규모를 검색한 후 "
        "3개월 예상 고객 수와 매출을 계산해 완성된 피칭 초안을 작성해주세요."
    )

    messages = list(session.messages) + [{"role": "user", "content": draft_prompt}]

    return await _run_loop(
        messages=messages,
        tools=DRAFT_TOOLS,
        max_tokens=8192,
        session=session,
        max_iterations=20,
    )
