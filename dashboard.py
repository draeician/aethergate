"""
AetherGate Mission Control — Streamlit Dashboard
Usage: streamlit run dashboard.py
"""

import streamlit as st
import pandas as pd
import requests as http_requests
import os
from sqlmodel import Session, create_engine, select
from datetime import datetime

# --- Config ---
SYNC_DATABASE_URL = "sqlite:///./aethergate.db"
engine = create_engine(SYNC_DATABASE_URL, echo=False)

API_URL = os.getenv("API_URL", "http://localhost:8000")
MASTER_KEY = os.getenv("MASTER_API_KEY", "sk-admin-master-key")

# Import DB models (registers them with SQLModel.metadata)
from app.models import User, RequestLog, LLMModel, APIKey  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def check_api_health() -> bool:
    try:
        return http_requests.get(f"{API_URL}/health", timeout=3).ok
    except Exception:
        return False


def get_all_users() -> list[User]:
    with Session(engine) as s:
        return list(s.exec(select(User).order_by(User.created_at.desc())).all())


def get_all_models() -> list[LLMModel]:
    with Session(engine) as s:
        return list(s.exec(select(LLMModel)).all())


def get_all_logs(
    limit: int = 50,
    user_id: str | None = None,
    model: str | None = None,
) -> list[RequestLog]:
    with Session(engine) as s:
        stmt = select(RequestLog).order_by(RequestLog.timestamp.desc())
        if user_id:
            stmt = stmt.where(RequestLog.user_id == user_id)
        if model:
            stmt = stmt.where(RequestLog.model_used == model)
        return list(s.exec(stmt.limit(limit)).all())


def get_total_revenue() -> float:
    with Session(engine) as s:
        return sum(l.total_cost for l in s.exec(select(RequestLog)).all())


def get_total_requests() -> int:
    with Session(engine) as s:
        return len(s.exec(select(RequestLog)).all())


def get_active_user_count() -> int:
    with Session(engine) as s:
        return len(s.exec(select(User).where(User.is_active == True)).all())  # noqa: E712


def get_distinct_models_used() -> list[str]:
    with Session(engine) as s:
        return sorted({l.model_used for l in s.exec(select(RequestLog)).all()})


def get_user_map() -> dict[str, str]:
    with Session(engine) as s:
        return {str(u.id): u.username for u in s.exec(select(User)).all()}


def api_post(path: str, payload: dict) -> http_requests.Response:
    return http_requests.post(
        f"{API_URL}{path}",
        json=payload,
        headers={"x-admin-key": MASTER_KEY},
        timeout=10,
    )


# ---------------------------------------------------------------------------
# Page Config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="AetherGate — Mission Control",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

st.sidebar.title("⚡ AetherGate")
st.sidebar.caption("Mission Control")
st.sidebar.divider()

page = st.sidebar.radio(
    "Navigation",
    ["Overview", "Users", "Logs", "Models"],
    label_visibility="collapsed",
)

# --- System Status in sidebar ---
if check_api_health():
    st.sidebar.success("System Status: **Online**")
else:
    st.sidebar.error("System Status: **Offline**")

st.sidebar.divider()
st.sidebar.caption(f"Session: {datetime.now().strftime('%Y-%m-%d %H:%M')}")


# =========================================================================
# Page: Overview
# =========================================================================

if page == "Overview":
    st.title("Overview")
    st.caption("System-wide metrics at a glance")

    col1, col2, col3 = st.columns(3)
    col1.metric("Total Revenue", f"${get_total_revenue():,.6f}")
    col2.metric("Total Requests", f"{get_total_requests():,}")
    col3.metric("Active Users", f"{get_active_user_count()}")


# =========================================================================
# Page: Users
# =========================================================================

elif page == "Users":
    st.title("Users")
    st.caption("Manage registered user accounts")

    users = get_all_users()

    if users:
        rows = [
            {
                "Username": u.username,
                "Balance": u.balance,
                "Active": u.is_active,
                "Organization": u.organization or "—",
                "Created": u.created_at.strftime("%Y-%m-%d %H:%M") if u.created_at else "—",
            }
            for u in users
        ]
        df = pd.DataFrame(rows)

        def _hl(val: float) -> str:
            return "color: #ff4b4b; font-weight: bold" if val < 0 else ""

        st.dataframe(
            df.style.applymap(_hl, subset=["Balance"]).format({"Balance": "${:,.6f}"}),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No users yet.")

    # --- Add User ---
    st.divider()
    st.subheader("Add User")

    with st.form("add_user_form", clear_on_submit=True):
        uc1, uc2 = st.columns(2)
        with uc1:
            new_username = st.text_input("Username", placeholder="client_name")
        with uc2:
            new_balance = st.number_input("Initial Balance ($)", value=10.00, step=1.00, format="%.2f")

        if st.form_submit_button("Create User"):
            if not new_username.strip():
                st.error("Username is required.")
            else:
                try:
                    resp = api_post("/admin/users", {"username": new_username.strip(), "balance": new_balance})
                    if resp.ok:
                        st.success(f"User **{new_username.strip()}** created.")
                        st.rerun()
                    else:
                        st.error(f"API Error: {resp.json().get('detail', resp.text)}")
                except Exception as ex:
                    st.error(f"Connection error: {ex}")


# =========================================================================
# Page: Logs
# =========================================================================

elif page == "Logs":
    st.title("Logs")
    st.caption("Latest request history (newest first)")

    user_map = get_user_map()
    models_used = get_distinct_models_used()

    fc1, fc2, fc3 = st.columns([2, 2, 1])
    with fc1:
        user_options = ["All Users"] + [f"{name} ({uid[:8]}…)" for uid, name in user_map.items()]
        user_ids = [""] + list(user_map.keys())
        sel_idx = st.selectbox("Filter by User", range(len(user_options)), format_func=lambda i: user_options[i])
        sel_uid = user_ids[sel_idx] if sel_idx else None
    with fc2:
        model_opts = ["All Models"] + models_used
        sel_model = st.selectbox("Filter by Model", model_opts)
        sel_model = None if sel_model == "All Models" else sel_model
    with fc3:
        limit = st.number_input("Rows", min_value=10, max_value=500, value=50, step=10)

    logs = get_all_logs(limit=limit, user_id=sel_uid, model=sel_model)

    if logs:
        rows = [
            {
                "Timestamp": l.timestamp.strftime("%Y-%m-%d %H:%M:%S") if l.timestamp else "—",
                "User": user_map.get(str(l.user_id), str(l.user_id)[:8] + "…"),
                "Model": l.model_used,
                "Input Tokens": int(l.input_units),
                "Output Tokens": int(l.output_units),
                "Cost ($)": l.total_cost,
            }
            for l in logs
        ]
        st.dataframe(
            pd.DataFrame(rows).style.format({"Cost ($)": "${:,.6f}"}),
            use_container_width=True,
            hide_index=True,
        )

        st.divider()
        s1, s2, s3 = st.columns(3)
        s1.metric("Shown Requests", f"{len(logs)}")
        s2.metric("Total Input Tokens", f"{sum(int(l.input_units) for l in logs):,}")
        s3.metric("Page Cost", f"${sum(l.total_cost for l in logs):,.6f}")
    else:
        st.info("No logs matching the current filters.")


# =========================================================================
# Page: Models
# =========================================================================

elif page == "Models":
    st.title("Model Registry")
    st.caption("Configure model routing and per-provider settings")

    models = get_all_models()
    model_ids = [m.id for m in models]

    # --- Existing Models Table ---
    if models:
        rows = [
            {
                "Model ID": m.id,
                "LiteLLM Name": m.litellm_name,
                "Price In": m.price_in,
                "Price Out": m.price_out,
                "API Base": m.api_base or "— (default)",
                "Key Set": "Yes" if m.api_key else "No",
                "Active": m.is_active,
            }
            for m in models
        ]
        st.dataframe(
            pd.DataFrame(rows).style.format({
                "Price In": "${:,.6f}",
                "Price Out": "${:,.6f}",
            }),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No models configured yet. Use the form below to add one.")

    st.divider()

    # --- Load Model for Editing ---
    if model_ids:
        selected = st.selectbox(
            "Load Model for Editing",
            ["— New Model —"] + model_ids,
            help="Pick an existing model to pre-fill the form, or choose '— New Model —' to add a fresh one.",
        )
    else:
        selected = "— New Model —"

    editing = selected != "— New Model —"
    edit_obj: LLMModel | None = None
    if editing:
        edit_obj = next((m for m in models if m.id == selected), None)

    st.subheader("Edit Model" if edit_obj else "Configure Model")

    # --- Form ---
    with st.form("model_config_form", clear_on_submit=False):
        c1, c2 = st.columns(2)
        with c1:
            fm_id = st.text_input(
                "Public Model ID",
                value=edit_obj.id if edit_obj else "",
                placeholder="gpt-4-turbo",
                disabled=bool(edit_obj),
            )
            fm_litellm = st.text_input(
                "LiteLLM Internal Name",
                value=edit_obj.litellm_name if edit_obj else "",
                placeholder="ollama/llama3",
            )
        with c2:
            fm_price_in = st.number_input(
                "Price / Input Token ($)",
                value=edit_obj.price_in if edit_obj else 0.000001,
                format="%.6f",
                step=0.000001,
            )
            fm_price_out = st.number_input(
                "Price / Output Token ($)",
                value=edit_obj.price_out if edit_obj else 0.000002,
                format="%.6f",
                step=0.000001,
            )

        c3, c4 = st.columns(2)
        with c3:
            fm_api_base = st.text_input(
                "API Base URL",
                value=(edit_obj.api_base or "") if edit_obj else "",
                placeholder="https://api.openai.com/v1",
                help="Optional: endpoint URL for this specific provider. Leave empty to use the global default.",
            )
        with c4:
            fm_api_key = st.text_input(
                "Provider API Key",
                value=(edit_obj.api_key or "") if edit_obj else "",
                placeholder="sk-...",
                type="password",
                help="Optional: authentication key for this provider. Leave empty if not required.",
            )

        if st.form_submit_button("Save Configuration"):
            final_id = edit_obj.id if edit_obj else fm_id.strip().lower().replace(" ", "-")
            if not final_id or not fm_litellm.strip():
                st.error("Model ID and LiteLLM Name are required.")
            else:
                if not edit_obj and final_id != fm_id.strip():
                    st.info(f"Model ID normalized to `{final_id}` (lowercase, no spaces).")
                payload = {
                    "id": final_id,
                    "litellm_name": fm_litellm.strip(),
                    "price_in": fm_price_in,
                    "price_out": fm_price_out,
                    "api_base": fm_api_base.strip() or None,
                    "api_key": fm_api_key.strip() or None,
                }
                try:
                    resp = api_post("/admin/models", payload)
                    if resp.ok:
                        result = resp.json()
                        st.success(f"Model `{result['model']}` **{result['action']}** successfully.")
                        st.rerun()
                    else:
                        st.error(f"API Error: {resp.json().get('detail', resp.text)}")
                except Exception as ex:
                    st.error(f"Connection error: {ex}")
