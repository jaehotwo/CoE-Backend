import os
import time
from typing import Any, Dict, List, Optional, Tuple

import requests
import streamlit as st

# --- Configuration ---
DEFAULT_BACKEND_BASE = "http://localhost:8000"
DEFAULT_RAG_BASE = "http://localhost:8001"
DEFAULT_DIRECT_BASE = "http://localhost:18001"


def _clean_base(url: Optional[str]) -> Optional[str]:
    """Sanitize base URLs pulled from env vars (handles CRLF)."""

    if not url:
        return None

    cleaned = url.strip()
    if not cleaned:
        return None
    return cleaned.rstrip("/")


BACKEND_BASE_URL = _clean_base(os.getenv("BACKEND_BASE_URL")) or DEFAULT_BACKEND_BASE
RAG_PIPELINE_URL = _clean_base(os.getenv("RAG_PIPELINE_URL")) or DEFAULT_RAG_BASE
_direct_env = os.getenv("RAG_DIRECT_URL") or os.getenv("RAG_DIRECT_PORT_URL")
RAG_DIRECT_URL = _clean_base(_direct_env) or DEFAULT_DIRECT_BASE

if RAG_DIRECT_URL == RAG_PIPELINE_URL:
    # Avoid duplicate lookups when direct and proxy endpoints are identical
    RAG_DIRECT_URL = None

RECOMMENDATION_BACKEND_URL = f"{RAG_PIPELINE_URL}/api/v1/itsd/recommend-assignee"

BACKEND_EMBED_ASYNC_PATHS = [
    "/itsd/embed-requests-async",
    "/tools/itsd/embed-requests-async",
    "/api/v1/itsd/embed-requests-async",
]

BACKEND_STATUS_PATHS = [
    "/itsd/embed-requests-status/{job_id}",
    "/tools/itsd/embed-requests-status/{job_id}",
    "/api/v1/itsd/embed-requests-status/{job_id}",
]

RAG_EMBED_ASYNC_PATH = "/api/v1/itsd/embed-requests-async"
RAG_STATUS_PATH = "/api/v1/itsd/embed-requests-status/{job_id}"

JOB_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
EMBED_STATUS_POLL_INTERVAL = float(os.getenv("EMBED_STATUS_POLL_INTERVAL", "2.0"))
EMBED_STATUS_POLL_TIMEOUT = float(os.getenv("EMBED_STATUS_POLL_TIMEOUT", "600.0"))

CHROMA_INDEX_STATS_PATH = "/api/v1/itsd/debug/index-stats"
CHROMA_SAMPLE_PATH = "/api/v1/itsd/debug/sample"
CHROMA_GROUPS_PATH = "/api/v1/itsd/debug/groups"

SAMPLES_PER_PAGE = 50
PAGES_PER_GROUP = 10


def _build_embed_targets() -> List[Tuple[str, str]]:
    """Return candidate endpoints for queuing embed jobs."""

    targets: List[Tuple[str, str]] = []
    seen: set[str] = set()

    for label, base, paths in [
        ("backend", BACKEND_BASE_URL, BACKEND_EMBED_ASYNC_PATHS),
        ("rag", RAG_PIPELINE_URL, [RAG_EMBED_ASYNC_PATH]),
        ("direct", RAG_DIRECT_URL, [RAG_EMBED_ASYNC_PATH]),
    ]:
        if not base:
            continue
        for path in paths:
            url = f"{base}{path}"
            if url in seen:
                continue
            seen.add(url)
            targets.append((label, url))
    return targets


def _build_status_targets(job_id: str) -> List[Tuple[str, str]]:
    """Return candidate endpoints (in order) for job status lookups."""

    targets: List[Tuple[str, str]] = []
    seen: set[str] = set()

    for label, base, paths in [
        ("backend", BACKEND_BASE_URL, BACKEND_STATUS_PATHS),
        ("rag", RAG_PIPELINE_URL, [RAG_STATUS_PATH]),
        ("direct", RAG_DIRECT_URL, [RAG_STATUS_PATH]),
    ]:
        if not base:
            continue
        for path in paths:
            url = f"{base}{path.format(job_id=job_id)}"
            if url in seen:
                continue
            seen.add(url)
            targets.append((label, url))
    return targets


def _build_rag_debug_targets(path: str) -> List[Tuple[str, str]]:
    """Return candidate endpoints for RAG debug utilities (index stats, samples)."""

    targets: List[Tuple[str, str]] = []
    seen: set[str] = set()
    for label, base in [
        ("rag", RAG_PIPELINE_URL),
        ("direct", RAG_DIRECT_URL),
        ("backend", BACKEND_BASE_URL),
    ]:
        if not base:
            continue
        url = f"{base}{path}"
        if url in seen:
            continue
        seen.add(url)
        targets.append((label, url))
    return targets


def submit_embedding_job(file_name: str, file_bytes: bytes, content_type: Optional[str]) -> Dict[str, Any]:
    """Queue an embedding job by probing backend → RAG → direct endpoints."""

    errors: List[str] = []
    for source, url in _build_embed_targets():
        files = {
            "file": (
                file_name,
                file_bytes,
                content_type or "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        }
        try:
            response = requests.post(url, files=files, timeout=60)
        except requests.RequestException as exc:
            errors.append(f"[{source}] {exc}")
            continue

        if response.status_code in {200, 201, 202}:
            try:
                data = response.json()
            except ValueError:
                errors.append(f"[{source}] JSON 응답 파싱 실패 (HTTP {response.status_code})")
                continue

            return {
                "source": source,
                "url": url,
                "data": data,
                "status_code": response.status_code,
            }

        # Accumulate error details for follow-up troubleshooting
        truncated = response.text[:500] if response.text else ""
        errors.append(f"[{source}] HTTP {response.status_code}: {truncated}")

    raise RuntimeError("\n".join(errors) or "임베딩 작업 요청에 실패했습니다.")


def fetch_embed_job_status(job_id: str) -> Dict[str, Any]:
    """Fetch embedding job progress by probing backend → RAG → direct endpoints."""

    errors: List[str] = []
    for source, url in _build_status_targets(job_id):
        try:
            response = requests.get(url, timeout=10)
        except requests.RequestException as exc:
            errors.append(f"[{source}] {exc}")
            continue

        if response.status_code == 200:
            try:
                data = response.json()
            except ValueError:
                errors.append(f"[{source}] JSON 응답 파싱 실패 (HTTP {response.status_code})")
                continue

            return {"source": source, "url": url, "data": data}

        truncated = response.text[:500] if response.text else ""
        errors.append(f"[{source}] HTTP {response.status_code}: {truncated}")

    raise RuntimeError("\n".join(errors) or "임베딩 작업 상태 조회에 실패했습니다.")


def fetch_chroma_index_stats() -> Dict[str, Any]:
    """Fetch ITSD index statistics from Chroma."""

    errors: List[str] = []
    for source, url in _build_rag_debug_targets(CHROMA_INDEX_STATS_PATH):
        try:
            response = requests.get(url, timeout=15)
        except requests.RequestException as exc:
            errors.append(f"[{source}] {exc}")
            continue

        if response.status_code == 200:
            try:
                data = response.json()
            except ValueError:
                errors.append(f"[{source}] JSON 응답 파싱 실패 (HTTP {response.status_code})")
                continue

            return {"source": source, "url": url, "data": data}

        truncated = response.text[:500] if response.text else ""
        errors.append(f"[{source}] HTTP {response.status_code}: {truncated}")

    raise RuntimeError("\n".join(errors) or "Chroma 인덱스 통계 조회에 실패했습니다.")


def fetch_chroma_samples(field: str, limit: int = 3) -> Dict[str, Any]:
    """Fetch sample documents for a specific ITSD field from Chroma."""

    params = {"field": field, "limit": limit}
    errors: List[str] = []
    for source, url in _build_rag_debug_targets(CHROMA_SAMPLE_PATH):
        try:
            response = requests.get(url, params=params, timeout=15)
        except requests.RequestException as exc:
            errors.append(f"[{source}] {exc}")
            continue

        if response.status_code == 200:
            try:
                data = response.json()
            except ValueError:
                errors.append(f"[{source}] JSON 응답 파싱 실패 (HTTP {response.status_code})")
                continue

            return {
                "source": source,
                "url": response.url,
                "data": data,
            }

        truncated = response.text[:500] if response.text else ""
        errors.append(f"[{source}] HTTP {response.status_code}: {truncated}")

    raise RuntimeError("\n".join(errors) or "Chroma 샘플 조회에 실패했습니다.")


def fetch_chroma_groups() -> Dict[str, Any]:
    """Fetch list of Chroma groups via ITSD debug endpoint."""

    errors: List[str] = []
    for source, url in _build_rag_debug_targets(CHROMA_GROUPS_PATH):
        try:
            response = requests.get(url, timeout=15)
        except requests.RequestException as exc:
            errors.append(f"[{source}] {exc}")
            continue

        if response.status_code == 200:
            try:
                data = response.json()
            except ValueError:
                errors.append(f"[{source}] JSON 응답 파싱 실패 (HTTP {response.status_code})")
                continue

            return {"source": source, "url": url, "data": data}

        truncated = response.text[:500] if response.text else ""
        errors.append(f"[{source}] HTTP {response.status_code}: {truncated}")

    raise RuntimeError("\n".join(errors) or "Chroma 그룹 목록 조회에 실패했습니다.")

def _extract_status_value(status_payload: Optional[Dict[str, Any]]) -> str:
    data = status_payload.get("data") if status_payload else None
    if isinstance(data, dict):
        return str(data.get("status", "")).lower()
    return ""


def _is_terminal_status(status_payload: Optional[Dict[str, Any]]) -> bool:
    return _extract_status_value(status_payload) in JOB_TERMINAL_STATUSES


def _poll_embed_status_if_needed(render_callback) -> None:
    job_id = st.session_state.get("last_embed_job_id")
    if not job_id or not st.session_state.get("embed_polling_active"):
        return

    now = time.time()
    started_at = st.session_state.get("embed_poll_started_at")
    if started_at is None:
        started_at = now
        st.session_state["embed_poll_started_at"] = started_at

    while st.session_state.get("embed_polling_active"):
        if time.time() - started_at > EMBED_STATUS_POLL_TIMEOUT:
            error_message = "임베딩 진행 상황 확인 시간이 초과되었습니다. 상태를 수동으로 조회해주세요."
            st.session_state["embed_polling_active"] = False
            st.session_state["embed_poll_started_at"] = None
            st.session_state["last_embed_status"] = None
            st.session_state["last_embed_status_error"] = error_message
            render_callback(None, error_message)
            break

        try:
            status_payload = fetch_embed_job_status(job_id)
        except RuntimeError as exc:
            error_message = str(exc)
            st.session_state["last_embed_status"] = None
            st.session_state["last_embed_status_error"] = error_message
            st.session_state["embed_polling_active"] = False
            st.session_state["embed_poll_started_at"] = None
            render_callback(None, error_message)
            break

        st.session_state["last_embed_status"] = status_payload
        st.session_state["last_embed_status_error"] = None
        render_callback(status_payload, None)

        if _is_terminal_status(status_payload):
            st.session_state["embed_polling_active"] = False
            st.session_state["embed_poll_started_at"] = None
            break

        time.sleep(max(0.1, EMBED_STATUS_POLL_INTERVAL))


st.set_page_config(page_title="ITSD 담당자 추천 AI", page_icon="✨", layout="wide")

st.markdown(
    """
    <style>
        .main .block-container {
            max-width: 100%;
            padding-left: 1.5rem;
            padding-right: 1.5rem;
        }
    </style>
    """,
    unsafe_allow_html=True,
)

# --- Session State Defaults ---
if "last_embed_job_id" not in st.session_state:
    st.session_state["last_embed_job_id"] = ""
if "embed_job_id_input" not in st.session_state:
    st.session_state["embed_job_id_input"] = st.session_state.get("last_embed_job_id", "")
if "last_embed_status" not in st.session_state:
    st.session_state["last_embed_status"] = None
if "last_embed_status_error" not in st.session_state:
    st.session_state["last_embed_status_error"] = None
if "embed_polling_active" not in st.session_state:
    st.session_state["embed_polling_active"] = False
if "embed_poll_started_at" not in st.session_state:
    st.session_state["embed_poll_started_at"] = None
if "chroma_index_stats" not in st.session_state:
    st.session_state["chroma_index_stats"] = None
if "chroma_index_stats_error" not in st.session_state:
    st.session_state["chroma_index_stats_error"] = None
if "chroma_index_stats_initialized" not in st.session_state:
    st.session_state["chroma_index_stats_initialized"] = False
if "chroma_groups" not in st.session_state:
    st.session_state["chroma_groups"] = None
if "chroma_groups_error" not in st.session_state:
    st.session_state["chroma_groups_error"] = None
if "chroma_sample_payload" not in st.session_state:
    st.session_state["chroma_sample_payload"] = None
if "chroma_sample_error" not in st.session_state:
    st.session_state["chroma_sample_error"] = None
if "chroma_sample_payload_info" not in st.session_state:
    st.session_state["chroma_sample_payload_info"] = None
if "chroma_sample_page" not in st.session_state:
    st.session_state["chroma_sample_page"] = 1
if "chroma_sample_page_group_start" not in st.session_state:
    st.session_state["chroma_sample_page_group_start"] = 1
if "chroma_last_selected_field" not in st.session_state:
    st.session_state["chroma_last_selected_field"] = None

# --- Functions ---
def embed_file():
    """Callback function to handle file embedding when a file is uploaded."""
    if 'itsd_file_uploader' in st.session_state and st.session_state.itsd_file_uploader is not None:
        uploaded_file = st.session_state.itsd_file_uploader
        st.info(f"**`{uploaded_file.name}`** 파일 임베딩을 시작합니다.")
        with st.spinner("데이터를 처리하고 벡터 DB에 임베딩하는 중입니다..."):
            file_bytes = uploaded_file.getvalue()
            try:
                enqueue_result = submit_embedding_job(uploaded_file.name, file_bytes, uploaded_file.type)
            except RuntimeError as exc:
                st.error("임베딩 작업을 큐에 등록하지 못했습니다.")
                st.code(str(exc))
                st.warning("CoE-RagPipeline 또는 백엔드 서비스가 실행 중인지 확인해주세요.")
                return

            response_data = enqueue_result["data"]
            st.success("🎉 임베딩 작업이 큐에 등록되었습니다.")
            st.caption(f"사용된 엔드포인트: `{enqueue_result['source']}` → {enqueue_result['url']}")
            st.json(response_data)

            st.session_state["last_embed_status"] = {
                "source": enqueue_result["source"],
                "url": enqueue_result["url"],
                "data": response_data,
            }
            st.session_state["last_embed_status_error"] = None

            job_id = response_data.get("job_id")
            if job_id:
                st.session_state["last_embed_job_id"] = job_id
                st.session_state["embed_job_id_input"] = job_id
                st.session_state["embed_polling_active"] = True
                st.session_state["embed_poll_started_at"] = time.time()
                st.info("사이드바에 job_id가 자동으로 입력되었습니다. 진행 상태를 즉시 확인해보세요.")
            else:
                st.session_state["embed_polling_active"] = False
                st.session_state["embed_poll_started_at"] = None
                st.warning("응답에 job_id가 포함되어 있지 않습니다. 상태 조회 시 직접 입력해주세요.")


def _refresh_index_stats() -> None:
    """Load Chroma index statistics and update session state."""

    try:
        payload = fetch_chroma_index_stats()
    except RuntimeError as exc:
        st.session_state["chroma_index_stats"] = None
        st.session_state["chroma_index_stats_error"] = str(exc)
    else:
        st.session_state["chroma_index_stats"] = payload
        st.session_state["chroma_index_stats_error"] = None


def _refresh_groups() -> None:
    """Load Chroma group list and cache results."""

    try:
        payload = fetch_chroma_groups()
    except RuntimeError as exc:
        st.session_state["chroma_groups"] = None
        st.session_state["chroma_groups_error"] = str(exc)
    else:
        st.session_state["chroma_groups"] = payload
        st.session_state["chroma_groups_error"] = None


def _refresh_samples(
    field: str,
    limit: int,
    page_size: int,
    page_group_start: int,
) -> None:
    """Load Chroma sample documents for the given field and cache metadata."""

    try:
        payload = fetch_chroma_samples(field=field, limit=limit)
    except RuntimeError as exc:
        st.session_state["chroma_sample_payload"] = None
        st.session_state["chroma_sample_error"] = str(exc)
        st.session_state["chroma_sample_payload_info"] = None
    else:
        st.session_state["chroma_sample_payload"] = payload
        st.session_state["chroma_sample_error"] = None
        st.session_state["chroma_sample_payload_info"] = {
            "field": field,
            "limit": limit,
            "page_size": page_size,
            "page_group_start": page_group_start,
        }


def _build_sample_table_rows(
    items: List[Dict[str, Any]],
    start_index: int,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Normalize sample payloads into a concise tabular format and detail view."""

    def _truncate(text: Optional[str], limit: int = 120) -> Optional[str]:
        if not text or not isinstance(text, str):
            return text
        return text if len(text) <= limit else text[: limit - 3] + "..."

    table_rows: List[Dict[str, Any]] = []
    detail_rows: List[Dict[str, Any]] = []

    for offset, item in enumerate(items, start=start_index + 1):
        raw_metadata = item.get("metadata") if isinstance(item, dict) else {}
        metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
        doc_id_raw = item.get("doc_id") if isinstance(item, dict) else None
        doc_id = str(doc_id_raw) if doc_id_raw is not None else None

        content_value = item.get("content") if isinstance(item, dict) else None
        text_value: Optional[str] = None
        if isinstance(content_value, str) and content_value:
            text_value = content_value
        else:
            # Fall back to common text-bearing metadata fields
            for key in ("content", "title", "text", "description"):
                candidate = metadata.get(key)
                if isinstance(candidate, str) and candidate:
                    text_value = candidate
                    break

        preview_text = _truncate(text_value)

        row: Dict[str, Any] = {
            "#": offset,
            "doc_id": doc_id,
            "itsd_field": metadata.get("itsd_field"),
            "chunk": metadata.get("chunk_index"),
            "chunks": metadata.get("total_chunks"),
            "text": preview_text,
        }

        # Promote commonly referenced identifiers for quick scanning
        for key in ("request_id", "requester", "assignee", "applied_system", "request_type"):
            value = metadata.get(key)
            if value is not None:
                row[key] = value

        # Expose full metadata as individual columns with a prefix
        for key, value in metadata.items():
            column_key = f"meta.{key}"
            if column_key in row:
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                row[column_key] = value
            else:
                row[column_key] = str(value)

        table_rows.append(row)

        detail_rows.append(
            {
                "index": offset,
                "doc_id": doc_id,
                "metadata": metadata,
                "preview": preview_text,
                "full_text": text_value if isinstance(text_value, str) else str(text_value or ""),
            }
        )

    return table_rows, detail_rows

# --- Sidebar for Data Embedding ---
with st.sidebar:
    st.title("📚 데이터 학습")
    st.markdown("과거 ITSD 데이터를 AI에게 학습시켜 추천 정확도를 높입니다.")
    st.info("**필수 컬럼**: `request_id`, `title`, `content`, `assignee`, `applied_system` 등")

    uploaded_file = st.file_uploader("ITSD 데이터 Excel(.xlsx) 파일", type=["xlsx"], help="학습시킬 과거 ITSD 요청 데이터가 담긴 Excel 파일을 업로드하세요.", on_change=embed_file, key="itsd_file_uploader")

    st.divider()
    st.subheader("🛰 임베딩 작업 상태")
    st.caption("필요하면 job_id를 수정한 뒤 Enter 키로 즉시 조회할 수 있습니다.")

    def _handle_job_id_change() -> None:
        job_id_value = (st.session_state.get("embed_job_id_input") or "").strip()
        if not job_id_value:
            st.session_state["last_embed_status"] = None
            st.session_state["last_embed_status_error"] = None
            st.session_state["embed_polling_active"] = False
            st.session_state["embed_poll_started_at"] = None
            return

        try:
            status_payload = fetch_embed_job_status(job_id_value)
        except RuntimeError as exc:
            st.session_state["last_embed_status"] = None
            st.session_state["last_embed_status_error"] = str(exc)
            st.session_state["embed_polling_active"] = False
            st.session_state["embed_poll_started_at"] = None
        else:
            st.session_state["last_embed_status"] = status_payload
            st.session_state["last_embed_status_error"] = None
            st.session_state["last_embed_job_id"] = job_id_value
            terminal = _is_terminal_status(status_payload)
            st.session_state["embed_polling_active"] = not terminal
            st.session_state["embed_poll_started_at"] = time.time() if not terminal else None

    job_id_input = st.text_input(
        "최근 임베딩 job_id",
        key="embed_job_id_input",
        placeholder="예: f0f1c2...",
        on_change=_handle_job_id_change,
    )

    status_placeholder = st.empty()

    def render_status(status_payload: Optional[Dict[str, Any]], status_error: Optional[str]) -> None:
        status_placeholder.empty()
        block = status_placeholder.container()

        if status_payload:
            data = status_payload.get("data", {}) if isinstance(status_payload, dict) else {}
            status_text = data.get("status")
            status_value = (str(status_text).lower() if status_text is not None else "")

            if st.session_state.get("embed_polling_active"):
                block.caption("임베딩 진행 상황을 실시간으로 추적 중입니다...")

            if status_value == "completed":
                display_fn = block.success
            elif status_value in {"failed", "error", "cancelled"}:
                display_fn = block.error
            else:
                display_fn = block.info

            display_fn(f"현재 상태: {status_text or '세부 정보 확인' }")
            block.caption(f"응답 엔드포인트: `{status_payload.get('source', '?')}` → {status_payload.get('url', '?')}")

            progress = data.get("progress")
            stage = data.get("stage")
            try:
                progress_value = int(float(progress)) if progress is not None else None
            except (TypeError, ValueError):
                progress_value = None

            if progress_value is not None:
                block.progress(min(max(progress_value, 0), 100))
            if stage:
                block.caption(f"진행 단계: {stage}")
            block.json(data)
        elif status_error:
            block.error("임베딩 상태 조회에 실패했습니다.")
            block.code(status_error)
        else:
            block.caption("임베딩 작업 내역이 없습니다. 새 파일을 업로드해보세요.")

    if st.session_state.get("embed_polling_active"):
        _poll_embed_status_if_needed(render_status)

    render_status(
        st.session_state.get("last_embed_status"),
        st.session_state.get("last_embed_status_error"),
    )

    st.divider()
    st.subheader("🧭 Chroma DB 조회")
    with st.expander("ITSD 벡터 인덱스 살펴보기", expanded=False):
        st.caption("ChromaDB에 저장된 ITSD 임베딩 상태를 점검할 수 있습니다.")

        groups_col, groups_action_col = st.columns([3, 1])
        with groups_action_col:
            if st.button("그룹 목록 조회", key="refresh_groups_btn"):
                _refresh_groups()

        groups_payload = st.session_state.get("chroma_groups")
        groups_error = st.session_state.get("chroma_groups_error")

        with groups_col:
            if groups_payload:
                groups_data = groups_payload.get("data", {}) if isinstance(groups_payload, dict) else {}
                groups_list = groups_data.get("groups", []) if isinstance(groups_data, dict) else []
                st.success("그룹 목록을 불러왔습니다.")
                st.caption(
                    f"응답 엔드포인트: `{groups_payload.get('source', '?')}` → {groups_payload.get('url', '?')}"
                )
                info_bits = []
                if isinstance(groups_data, dict):
                    if groups_data.get("group_count") is not None:
                        info_bits.append(f"그룹 수: {groups_data['group_count']}")
                    if groups_data.get("scanned") is not None:
                        info_bits.append(f"스캔 문서: {groups_data['scanned']}")
                    if groups_data.get("truncated"):
                        info_bits.append("스캔 한도 도달")
                if info_bits:
                    st.caption(" · ".join(info_bits))
                if groups_list:
                    display_rows = []
                    for g in groups_list:
                        if not isinstance(g, dict):
                            continue
                        display_rows.append(
                            {
                                "group_name": g.get("group_name"),
                                "doc_count": g.get("doc_count"),
                                "fields": ", ".join(str(v) for v in g.get("fields", [])),
                            }
                        )
                    if display_rows:
                        st.table(display_rows)
                    else:
                        st.json(groups_data)
                else:
                    st.warning("그룹 데이터를 찾지 못했습니다.")
            elif groups_error:
                st.error("그룹 목록 조회에 실패했습니다.")
                st.code(groups_error)
            else:
                st.info("'그룹 목록 조회' 버튼을 눌러 현재 Chroma 그룹을 확인하세요.")

        st.markdown("---")

        if not st.session_state.get("chroma_index_stats_initialized", False):
            with st.spinner("Chroma 인덱스 통계를 불러오는 중입니다..."):
                _refresh_index_stats()
            st.session_state["chroma_index_stats_initialized"] = True

        stats_payload = st.session_state.get("chroma_index_stats")
        stats_error = st.session_state.get("chroma_index_stats_error")

        with st.container():
            if stats_payload:
                st.success("인덱스 통계를 불러왔습니다.")
                st.caption(
                    f"응답 엔드포인트: `{stats_payload.get('source', '?')}` → {stats_payload.get('url', '?')}"
                )
                payload_data = stats_payload.get("data", {})
                if isinstance(payload_data, dict):
                    counts = payload_data.get("counts")
                    if isinstance(counts, dict):
                        table_row = {
                            "group": payload_data.get("group"),
                            "collection_total": counts.get("collection_total"),
                            "total": counts.get("total"),
                            "title": counts.get("title"),
                            "content": counts.get("content"),
                            "combined": counts.get("combined"),
                        }
                        st.table([table_row])
                    else:
                        st.json(payload_data)
                else:
                    st.json(payload_data)
            elif stats_error:
                st.error("Chroma 인덱스 통계 조회에 실패했습니다.")
                st.code(stats_error)
            else:
                st.info("통계 조회 버튼을 눌러 현재 인덱스 상태를 확인하세요.")

        with st.container():
            if st.button(
                "통계 새로고침",
                key="refresh_index_stats_btn",
                use_container_width=True,
            ):
                with st.spinner("Chroma 인덱스 통계를 불러오는 중입니다..."):
                    _refresh_index_stats()
                st.session_state["chroma_index_stats_initialized"] = True

        st.markdown("---")
        st.subheader("필드별 샘플 문서")

        field_options = {
            "제목 (title)": "title",
            "내용 (content)": "content",
            "결합 (combined)": "combined",
        }
        selected_field_label = st.selectbox(
            "조회할 필드",
            list(field_options.keys()),
            key="chroma_sample_field_select",
        )
        selected_field = field_options[selected_field_label]

        st.caption(
            f"페이지 당 {SAMPLES_PER_PAGE}건 표시, {PAGES_PER_GROUP}페이지 단위로 탐색합니다."
        )

        if st.session_state.get("chroma_last_selected_field") != selected_field:
            st.session_state["chroma_last_selected_field"] = selected_field
            st.session_state["chroma_sample_page_group_start"] = 1
            st.session_state["chroma_sample_page"] = 1
            st.session_state["chroma_sample_payload"] = None
            st.session_state["chroma_sample_error"] = None
            st.session_state["chroma_sample_payload_info"] = None

        page_group_start = st.session_state.get("chroma_sample_page_group_start", 1)
        current_page = st.session_state.get("chroma_sample_page", 1)

        fetch_group_start: Optional[int] = None

        control_cols = st.columns([3, 1])
        with control_cols[1]:
            if st.button("현재 그룹 불러오기", key="fetch_chroma_samples_btn"):
                fetch_group_start = page_group_start

        sample_payload = st.session_state.get("chroma_sample_payload")
        sample_error = st.session_state.get("chroma_sample_error")
        sample_info = st.session_state.get("chroma_sample_payload_info") or {}

        data = sample_payload.get("data", {}) if isinstance(sample_payload, dict) else {}
        has_more = bool(data.get("has_more")) if isinstance(data, dict) else False
        reached_limit = bool(data.get("reached_limit")) if isinstance(data, dict) else False
        total_count_raw = data.get("total_count") if isinstance(data, dict) else None
        sample_count = data.get("sample_count") if isinstance(data, dict) else None
        total_pages: Optional[int] = None
        if isinstance(total_count_raw, int) and total_count_raw >= 0:
            total_pages = (total_count_raw + SAMPLES_PER_PAGE - 1) // SAMPLES_PER_PAGE
        elif isinstance(sample_count, int) and sample_count > 0 and not has_more:
            total_pages = (sample_count + SAMPLES_PER_PAGE - 1) // SAMPLES_PER_PAGE

        has_group_data = (
            sample_payload
            and sample_info.get("field") == selected_field
            and sample_info.get("page_size") == SAMPLES_PER_PAGE
            and sample_info.get("page_group_start") == page_group_start
        )

        if (
            fetch_group_start is None
            and sample_error is None
            and not has_group_data
        ):
            fetch_group_start = page_group_start

        max_group_start: Optional[int] = None
        if total_pages:
            max_group_start = ((total_pages - 1) // PAGES_PER_GROUP) * PAGES_PER_GROUP + 1

        prev_disabled = page_group_start <= 1
        if has_more:
            next_disabled = False
        else:
            next_disabled = False
            if max_group_start is not None:
                next_disabled = page_group_start >= max_group_start
            elif total_pages is not None:
                next_disabled = page_group_start + PAGES_PER_GROUP > total_pages

        nav_prev_col, nav_pages_col, nav_next_col = st.columns([1.5, 6, 1.5])
        with nav_prev_col:
            if st.button("◀ 이전 10페이지", disabled=prev_disabled, key="chroma_prev_group_btn"):
                new_start = max(1, page_group_start - PAGES_PER_GROUP)
                st.session_state["chroma_sample_page_group_start"] = new_start
                st.session_state["chroma_sample_page"] = new_start
                fetch_group_start = new_start

        with nav_next_col:
            if st.button("다음 10페이지 ▶", disabled=next_disabled, key="chroma_next_group_btn"):
                new_start = page_group_start + PAGES_PER_GROUP
                if max_group_start is not None:
                    new_start = min(new_start, max_group_start)
                if total_pages is not None and new_start > total_pages:
                    new_start = max(page_group_start, total_pages)
                st.session_state["chroma_sample_page_group_start"] = new_start
                st.session_state["chroma_sample_page"] = new_start
                fetch_group_start = new_start

        if fetch_group_start is not None:
            group_end_fetch = fetch_group_start + PAGES_PER_GROUP - 1
            limit = group_end_fetch * SAMPLES_PER_PAGE
            _refresh_samples(
                field=selected_field,
                limit=limit,
                page_size=SAMPLES_PER_PAGE,
                page_group_start=fetch_group_start,
            )
            page_group_start = fetch_group_start
            current_page = fetch_group_start
            sample_payload = st.session_state.get("chroma_sample_payload")
            sample_error = st.session_state.get("chroma_sample_error")
            sample_info = st.session_state.get("chroma_sample_payload_info") or {}
            data = sample_payload.get("data", {}) if isinstance(sample_payload, dict) else {}
            has_more = bool(data.get("has_more")) if isinstance(data, dict) else False
            reached_limit = bool(data.get("reached_limit")) if isinstance(data, dict) else False
            total_count_raw = data.get("total_count") if isinstance(data, dict) else None
            sample_count = data.get("sample_count") if isinstance(data, dict) else None
            if isinstance(total_count_raw, int) and total_count_raw >= 0:
                total_pages = (total_count_raw + SAMPLES_PER_PAGE - 1) // SAMPLES_PER_PAGE
            elif isinstance(sample_count, int) and sample_count > 0 and not has_more:
                total_pages = (sample_count + SAMPLES_PER_PAGE - 1) // SAMPLES_PER_PAGE
            else:
                total_pages = None
            has_group_data = (
                sample_payload
                and sample_info.get("field") == selected_field
                and sample_info.get("page_size") == SAMPLES_PER_PAGE
                and sample_info.get("page_group_start") == page_group_start
            )
            if total_pages:
                max_group_start = ((total_pages - 1) // PAGES_PER_GROUP) * PAGES_PER_GROUP + 1
                if page_group_start > max_group_start:
                    page_group_start = max_group_start
                    st.session_state["chroma_sample_page_group_start"] = page_group_start
                    current_page = max(current_page, page_group_start)
                    st.session_state["chroma_sample_page"] = current_page
            else:
                max_group_start = None

        group_end = page_group_start + PAGES_PER_GROUP - 1
        if total_pages is not None:
            group_end = min(group_end, total_pages)

        pages_in_group = list(range(page_group_start, group_end + 1))
        if not pages_in_group:
            pages_in_group = [page_group_start]

        page_cols = nav_pages_col.columns(len(pages_in_group)) if pages_in_group else []
        for idx, page in enumerate(pages_in_group):
            col = page_cols[idx] if idx < len(page_cols) else nav_pages_col
            if page == current_page:
                col.markdown(f"**[{page}]**")
            else:
                if col.button(str(page), key=f"chroma_page_{page}"):
                    st.session_state["chroma_sample_page"] = page
                    current_page = page

        if has_group_data:
            current_page = st.session_state.get("chroma_sample_page", current_page)
            if current_page < page_group_start:
                current_page = page_group_start
                st.session_state["chroma_sample_page"] = current_page
            if current_page > pages_in_group[-1]:
                current_page = pages_in_group[-1]
                st.session_state["chroma_sample_page"] = current_page

            items = data.get("items", []) if isinstance(data, dict) else []
            start_index = (current_page - 1) * SAMPLES_PER_PAGE
            end_index = start_index + SAMPLES_PER_PAGE

            if start_index >= len(items):
                st.warning(
                    "현재 그룹에 선택한 페이지의 항목이 없습니다. '현재 그룹 불러오기'로 갱신해보세요."
                )
            else:
                actual_end = min(end_index, len(items))
                st.success(
                    f"페이지 {current_page} (항목 {start_index + 1}–{actual_end}) 결과를 표시합니다."
                )
                st.caption(
                    f"응답 엔드포인트: `{sample_payload.get('source', '?')}` → {sample_payload.get('url', '?')}"
                )
                if isinstance(sample_count, int):
                    st.caption(f"API 응답 샘플 수: {sample_count}")
                if reached_limit:
                    limit_value = data.get("limit") if isinstance(data, dict) else None
                    if isinstance(limit_value, int):
                        st.caption(
                            f"표시는 최대 {limit_value}건까지만 지원합니다. 추가 탐색이 필요하면 조건을 더 좁혀주세요."
                        )

                page_items = items[start_index:actual_end]
                table_rows, detail_rows = _build_sample_table_rows(page_items, start_index)
                if table_rows:
                    st.dataframe(table_rows, use_container_width=True)

                    detail_indices = [detail["index"] for detail in detail_rows]
                    if detail_indices:
                        label_map = {
                            detail["index"]: " · ".join(
                                [
                                    f"샘플 #{detail['index']}",
                                    f"ID: {detail.get('doc_id') or '없음'}",
                                    detail.get("preview") or "텍스트 없음",
                                ]
                            )
                            for detail in detail_rows
                        }
                        detail_select_key = (
                            f"chroma_sample_detail_{selected_field}_{page_group_start}_{current_page}"
                        )
                        selected_detail_idx = st.selectbox(
                            "전체 텍스트 보기",
                            detail_indices,
                            format_func=lambda idx: label_map.get(idx, f"샘플 #{idx}"),
                            key=detail_select_key,
                        )
                        selected_detail = next(
                            (detail for detail in detail_rows if detail["index"] == selected_detail_idx),
                            None,
                        )
                        if selected_detail:
                            st.caption(f"선택한 문서 ID: {selected_detail.get('doc_id') or '없음'}")
                            st.text_area(
                                "텍스트 전체",
                                value=selected_detail.get("full_text") or "",
                                height=240,
                                disabled=True,
                            )
                            with st.expander("상세 메타데이터", expanded=False):
                                st.json(selected_detail.get("metadata") or {})
                else:
                    st.info("선택된 페이지에 표시할 샘플이 없습니다.")

                if len(items) < sample_info.get("limit", 0):
                    st.caption("⚠️ API에서 반환한 데이터가 제한되어 있을 수 있습니다.")
        elif sample_error:
            st.error("샘플 문서 조회에 실패했습니다.")
            st.code(sample_error)
        else:
            st.caption("'현재 그룹 불러오기' 버튼으로 데이터를 조회하세요.")

# --- Main Area for Assignee Recommendation ---
st.title("✨ ITSD 담당자 추천 AI 에이전트")
st.markdown("새로운 ITSD 요청 내용을 입력하면, AI가 과거 데이터를 기반으로 최적의 담당자를 추천합니다.")

st.divider()

# Input fields
title = st.text_input("요청 제목", placeholder="예: 재택근무 VPN 접속 불가")
description = st.text_area(
    "요청 내용",
    placeholder="예: 오늘 아침부터 VPN 연결 시도 시 '서버에 연결할 수 없음' 오류가 발생합니다.",
    height=150,
)

st.markdown("#### 🔧 검색 결합 옵션")
fusion_choice = st.radio(
    "결합 방식",
    options=("rrf", "weighted"),
    format_func=lambda opt: "RRF (Reciprocal Rank Fusion)" if opt == "rrf" else "가중합 (Weighted Sum)",
    horizontal=True,
)

rrf_k0_value = None
w_title_value = None
w_content_value = None

if fusion_choice == "rrf":
    rrf_k0_value = st.number_input("RRF 결합 상수 k0", min_value=1, max_value=500, value=60, step=1)
else:
    col_title, col_content = st.columns(2)
    w_title_value = col_title.slider(
        "제목 가중치 (w_title)",
        min_value=0.0,
        max_value=1.0,
        value=0.4,
        step=0.05,
    )
    w_content_value = round(1.0 - w_title_value, 4)
    col_content.number_input(
        "내용 가중치 (w_content)",
        min_value=0.0,
        max_value=1.0,
        value=w_content_value,
        step=0.05,
        format="%.2f",
        disabled=True,
        help="제목 가중치에 따라 자동 계산됩니다.",
    )

top_k_each_value = st.number_input(
    "필드별 검색 문서 수 제한 (top_k_each, 0=자동)",
    min_value=0,
    max_value=500,
    value=0,
    step=10,
    help="각 필드(title, content)에서 불러올 최대 문서 수. 0이면 서비스 기본값을 사용합니다.",
)

submitted = st.button("🤖 담당자 추천 요청", type="primary", use_container_width=True)

if submitted:
    if not title or not description:
        st.warning("제목과 내용을 모두 입력해주세요.")
    else:
        fusion_params: Dict[str, str] = {}
        valid_options = True

        if fusion_choice == "rrf":
            fusion_params["use_rrf"] = "true"
            if rrf_k0_value is not None:
                fusion_params["rrf_k0"] = str(int(rrf_k0_value))
        else:
            total_weight = round((w_title_value or 0.0) + (w_content_value or 0.0), 4)
            if abs(total_weight - 1.0) > 1e-4:
                st.error("제목/내용 가중치의 합은 1.0이 되도록 설정됩니다. 다시 시도해주세요.")
                valid_options = False
            fusion_params["use_rrf"] = "false"
            if w_title_value is not None:
                fusion_params["w_title"] = f"{float(w_title_value):.4f}"
            if w_content_value is not None:
                fusion_params["w_content"] = f"{float(w_content_value):.4f}"

        if top_k_each_value and top_k_each_value > 0:
            fusion_params["top_k_each"] = str(int(top_k_each_value))

        if not valid_options:
            st.stop()

        with st.spinner("AI 에이전트가 최적의 담당자를 분석 중입니다..."):
            payload = {"title": title, "description": description}
            try:
                response = requests.post(
                    RECOMMENDATION_BACKEND_URL,
                    json=payload,
                    params=fusion_params,
                    timeout=180,
                )
                if response.status_code == 200:
                    recommendation = response.json()
                    st.success("담당자 추천이 완료되었습니다!")
                    st.caption(f"요청 옵션: {fusion_params}")
                    st.markdown(recommendation)
                else:
                    st.error(f"담당자 추천 실패 (HTTP {response.status_code}):")
                    try:
                        st.json(response.json())
                    except requests.exceptions.JSONDecodeError:
                        st.text(response.text)
            except requests.exceptions.RequestException as e:
                st.error(f"백엔드 서비스({RECOMMENDATION_BACKEND_URL}) 연결에 실패했습니다.")
                st.warning("CoE-RagPipeline 서비스가 실행 중인지 확인해주세요.")
            except Exception as e:
                st.error(f"담당자 추천 중 오류가 발생했습니다: {e}")

st.divider()
st.caption("Powered by CoE Platform")
