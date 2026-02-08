"""
AetherGate Mission Control — Read-Only Streamlit Dashboard
Usage: streamlit run dashboard.py
"""

import streamlit as st
import pandas as pd
from sqlmodel import Session, create_engine, select
from sqlalchemy.orm import selectinload
from datetime import datetime

# --- SYNCHRONOUS engine to avoid async loop conflicts with Streamlit ---
SYNC_DATABASE_URL = "sqlite:///./aethergate.db"
engine = create_engine(SYNC_DATABASE_URL, echo=False)

# Import models (they register with SQLModel.metadata on import)
from app.models import User, RequestLog, LLMModel, APIKey  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_all_users() -> list[User]:
    with Session(engine) as session:
        return list(session.exec(select(User).order_by(User.created_at.desc())).all())


def get_all_logs(limit: int = 50, user_id: str | None = None, model: str | None = None) -> list[RequestLog]:
    with Session(engine) as session:
        stmt = select(RequestLog).order_by(RequestLog.timestamp.desc())
        if user_id:
            stmt = stmt.where(RequestLog.user_id == user_id)
        if model:
            stmt = stmt.where(RequestLog.model_used == model)
        stmt = stmt.limit(limit)
        return list(session.exec(stmt).all())


def get_all_models() -> list[LLMModel]:
    with Session(engine) as session:
        return list(session.exec(select(LLMModel)).all())


def get_all_api_keys() -> list[APIKey]:
    with Session(engine) as session:
        return list(session.exec(select(APIKey)).all())


def get_total_revenue() -> float:
    with Session(engine) as session:
        logs = session.exec(select(RequestLog)).all()
        return sum(log.total_cost for log in logs)


def get_total_requests() -> int:
    with Session(engine) as session:
        return len(session.exec(select(RequestLog)).all())


def get_active_user_count() -> int:
    with Session(engine) as session:
        users = session.exec(select(User).where(User.is_active == True)).all()  # noqa: E712
        return len(users)


def get_distinct_models_used() -> list[str]:
    with Session(engine) as session:
        logs = session.exec(select(RequestLog)).all()
        return sorted(set(log.model_used for log in logs))


def get_user_map() -> dict[str, str]:
    """Return {user_id_str: username} for display."""
    with Session(engine) as session:
        users = session.exec(select(User)).all()
        return {str(u.id): u.username for u in users}


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
    ["Overview", "Live Logs", "Models"],
    label_visibility="collapsed",
)

st.sidebar.divider()
st.sidebar.caption(f"Session: {datetime.now().strftime('%Y-%m-%d %H:%M')}")


# ---------------------------------------------------------------------------
# Page 1: Overview
# ---------------------------------------------------------------------------

if page == "Overview":
    st.title("Overview")
    st.caption("System-wide metrics and user accounts")

    # --- Top Row Metrics ---
    col1, col2, col3 = st.columns(3)

    total_revenue = get_total_revenue()
    total_requests = get_total_requests()
    active_users = get_active_user_count()

    col1.metric("Total Revenue", f"${total_revenue:,.6f}")
    col2.metric("Total Requests", f"{total_requests:,}")
    col3.metric("Active Users", f"{active_users}")

    st.divider()

    # --- User Table ---
    st.subheader("User Accounts")

    users = get_all_users()
    if users:
        user_data = []
        for u in users:
            user_data.append({
                "Username": u.username,
                "Balance": u.balance,
                "Active": u.is_active,
                "Organization": u.organization or "—",
                "Created At": u.created_at.strftime("%Y-%m-%d %H:%M") if u.created_at else "—",
            })

        df_users = pd.DataFrame(user_data)

        # Highlight negative balances with red text
        def highlight_balance(val: float) -> str:
            if val < 0:
                return "color: #ff4b4b; font-weight: bold"
            return ""

        styled = df_users.style.applymap(highlight_balance, subset=["Balance"]).format(
            {"Balance": "${:,.6f}"}
        )
        st.dataframe(styled, use_container_width=True, hide_index=True)
    else:
        st.info("No users found. Use `python manage.py add-user <name>` to create one.")


# ---------------------------------------------------------------------------
# Page 2: Live Logs
# ---------------------------------------------------------------------------

elif page == "Live Logs":
    st.title("Live Logs")
    st.caption("Latest request history (newest first)")

    user_map = get_user_map()
    models_used = get_distinct_models_used()

    # --- Filters ---
    filter_col1, filter_col2, filter_col3 = st.columns([2, 2, 1])

    with filter_col1:
        user_options = ["All Users"] + [f"{name} ({uid[:8]}…)" for uid, name in user_map.items()]
        user_ids_list = [""] + list(user_map.keys())
        selected_user_idx = st.selectbox("Filter by User", range(len(user_options)), format_func=lambda i: user_options[i])
        selected_user_id = user_ids_list[selected_user_idx] if selected_user_idx else None

    with filter_col2:
        model_options = ["All Models"] + models_used
        selected_model = st.selectbox("Filter by Model", model_options)
        selected_model = None if selected_model == "All Models" else selected_model

    with filter_col3:
        limit = st.number_input("Rows", min_value=10, max_value=500, value=50, step=10)

    # --- Log Table ---
    logs = get_all_logs(
        limit=limit,
        user_id=selected_user_id if selected_user_id else None,
        model=selected_model,
    )

    if logs:
        log_data = []
        for log in logs:
            username = user_map.get(str(log.user_id), str(log.user_id)[:8] + "…")
            log_data.append({
                "Timestamp": log.timestamp.strftime("%Y-%m-%d %H:%M:%S") if log.timestamp else "—",
                "User": username,
                "Model": log.model_used,
                "Input Tokens": int(log.input_units),
                "Output Tokens": int(log.output_units),
                "Cost ($)": log.total_cost,
            })

        df_logs = pd.DataFrame(log_data)

        styled_logs = df_logs.style.format({"Cost ($)": "${:,.6f}"})
        st.dataframe(styled_logs, use_container_width=True, hide_index=True)

        # --- Summary below table ---
        st.divider()
        sum_col1, sum_col2, sum_col3 = st.columns(3)
        sum_col1.metric("Shown Requests", f"{len(logs)}")
        sum_col2.metric("Total Input Tokens", f"{sum(int(l.input_units) for l in logs):,}")
        sum_col3.metric("Page Cost", f"${sum(l.total_cost for l in logs):,.6f}")
    else:
        st.info("No request logs found matching the current filters.")


# ---------------------------------------------------------------------------
# Page 3: Models
# ---------------------------------------------------------------------------

elif page == "Models":
    st.title("Model Registry")
    st.caption("Configured LLM models and pricing")

    models = get_all_models()

    if models:
        model_data = []
        for m in models:
            model_data.append({
                "Model ID": m.id,
                "LiteLLM Target": m.litellm_name,
                "Capability": m.capability.value if m.capability else "—",
                "Billing Unit": m.billing_unit.value if m.billing_unit else "—",
                "Price In (per unit)": m.price_in,
                "Price Out (per unit)": m.price_out,
                "Active": m.is_active,
                "Fallback": m.fallback_model_id or "—",
            })

        df_models = pd.DataFrame(model_data)

        styled_models = df_models.style.format({
            "Price In (per unit)": "${:,.8f}",
            "Price Out (per unit)": "${:,.8f}",
        })
        st.dataframe(styled_models, use_container_width=True, hide_index=True)
    else:
        st.info("No models configured. Use `python manage.py add-model` to register one.")
