"""
AetherGate Mission Control — Streamlit Dashboard
Usage: streamlit run dashboard.py
"""

import json
import streamlit as st
import pandas as pd
import requests as http_requests
import os
from sqlmodel import Session, create_engine, select
from sqlalchemy.orm import selectinload
from datetime import datetime

# --- Config ---
SYNC_DATABASE_URL = (
    os.getenv("DATABASE_URL_SYNC")
    or os.getenv("DATABASE_URL")
    or "sqlite:////app/data/aethergate.db"
)
# Ensure synchronous driver (strip aiosqlite if inherited from the async URL)
SYNC_DATABASE_URL = SYNC_DATABASE_URL.replace("sqlite+aiosqlite", "sqlite")
engine = create_engine(SYNC_DATABASE_URL, echo=False)

API_URL = os.getenv("API_URL", "http://aethergate-api:8000")
MASTER_KEY = os.getenv("MASTER_API_KEY")
if not MASTER_KEY:
    raise RuntimeError(
        "MASTER_API_KEY environment variable is not set. "
        "The dashboard cannot start without it."
    )

from app.models import User, RequestLog, LLMModel, LLMEndpoint, APIKey  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def check_api_health() -> bool:
    try:
        return http_requests.get(f"{API_URL}/health", timeout=3).ok
    except Exception:
        return False


def api_get(path: str) -> http_requests.Response:
    return http_requests.get(
        f"{API_URL}{path}",
        headers={"x-admin-key": MASTER_KEY}, timeout=30,
    )


def api_post(path: str, payload: dict) -> http_requests.Response:
    return http_requests.post(
        f"{API_URL}{path}", json=payload,
        headers={"x-admin-key": MASTER_KEY}, timeout=30,
    )


def api_put(path: str, payload: dict) -> http_requests.Response:
    return http_requests.put(
        f"{API_URL}{path}", json=payload,
        headers={"x-admin-key": MASTER_KEY}, timeout=30,
    )


def get_all_users() -> list[User]:
    with Session(engine) as s:
        return list(s.exec(select(User).order_by(User.created_at.desc())).all())


def get_all_endpoints() -> list[LLMEndpoint]:
    with Session(engine) as s:
        return list(s.exec(select(LLMEndpoint)).all())


def get_all_models() -> list[LLMModel]:
    with Session(engine) as s:
        return list(s.exec(select(LLMModel).options(selectinload(LLMModel.endpoint))).all())


def get_all_logs(limit: int = 50, user_id: str | None = None, model: str | None = None) -> list[RequestLog]:
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


# ---------------------------------------------------------------------------
# Page Config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="AetherGate — Mission Control",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.sidebar.title("⚡ AetherGate")
st.sidebar.caption("Mission Control")
st.sidebar.divider()

page = st.sidebar.radio(
    "Navigation",
    ["Overview", "Users", "Endpoints", "Models", "Logs", "Backup"],
    label_visibility="collapsed",
)

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

    c1, c2, c3 = st.columns(3)
    c1.metric("Total Revenue", f"${get_total_revenue():,.6f}")
    c2.metric("Total Requests", f"{get_total_requests():,}")
    c3.metric("Active Users", f"{get_active_user_count()}")

    c4, c5 = st.columns(2)
    c4.metric("Endpoints", f"{len(get_all_endpoints())}")
    c5.metric("Models", f"{len(get_all_models())}")


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
                "Username": u.username, "Balance": u.balance,
                "Active": u.is_active, "Organization": u.organization or "—",
                "Created": u.created_at.strftime("%Y-%m-%d %H:%M") if u.created_at else "—",
            }
            for u in users
        ]
        df = pd.DataFrame(rows)

        def _hl(val: float) -> str:
            return "color: #ff4b4b; font-weight: bold" if val < 0 else ""

        st.dataframe(
            df.style.applymap(_hl, subset=["Balance"]).format({"Balance": "${:,.6f}"}),
            use_container_width=True, hide_index=True,
        )

        # --- Edit User ---
        st.divider()
        user_names = [u.username for u in users]
        selected_user = st.selectbox("Edit User", ["— Select —"] + user_names)

        if selected_user != "— Select —":
            edit_u = next((u for u in users if u.username == selected_user), None)
            if edit_u:
                st.subheader(f"Edit: {edit_u.username}")
                with st.form(f"edit_user_{edit_u.username}", clear_on_submit=False):
                    eu1, eu2 = st.columns(2)
                    with eu1:
                        eu_balance = st.number_input(
                            "Balance ($)", value=edit_u.balance, step=1.00, format="%.6f",
                        )
                        eu_org = st.text_input(
                            "Organization", value=edit_u.organization or "",
                        )
                    with eu2:
                        eu_email = st.text_input(
                            "Email", value=edit_u.email or "",
                        )
                        eu_active = st.checkbox("Active", value=edit_u.is_active)

                    if st.form_submit_button("Save User Changes"):
                        payload = {
                            "balance": eu_balance,
                            "is_active": eu_active,
                            "organization": eu_org.strip() or None,
                            "email": eu_email.strip() or None,
                        }
                        try:
                            resp = api_put(f"/admin/users/{edit_u.id}", payload)
                            if resp.ok:
                                st.success(f"User **{edit_u.username}** updated.")
                                st.rerun()
                            else:
                                st.error(f"API Error: {resp.json().get('detail', resp.text)}")
                        except Exception as ex:
                            st.error(f"Connection error: {ex}")
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
# Page: Endpoints (Providers)
# =========================================================================

elif page == "Endpoints":
    st.title("Endpoints (Providers)")
    st.caption("Manage API provider connections and global rate limits")

    endpoints = get_all_endpoints()

    if endpoints:
        rows = [
            {
                "ID": ep.id, "Name": ep.name, "Base URL": ep.base_url,
                "Key Set": "Yes" if ep.api_key else "No",
                "RPM Limit": ep.rpm_limit or "—",
                "Daily Limit": ep.day_limit or "—",
                "Active": ep.is_active,
            }
            for ep in endpoints
        ]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        # --- Edit existing endpoint ---
        st.divider()
        ep_labels = [f"{ep.name} (#{ep.id})" for ep in endpoints]
        selected_ep_label = st.selectbox("Edit Endpoint", ["— Select —"] + ep_labels)

        if selected_ep_label != "— Select —":
            ep_idx = ep_labels.index(selected_ep_label)
            edit_ep_obj = endpoints[ep_idx]

            st.subheader(f"Edit: {edit_ep_obj.name}")
            with st.form(f"edit_ep_{edit_ep_obj.id}", clear_on_submit=False):
                ec1, ec2 = st.columns(2)
                with ec1:
                    ep_name = st.text_input("Name", value=edit_ep_obj.name)
                    ep_base = st.text_input("Base URL", value=edit_ep_obj.base_url)
                with ec2:
                    ep_key = st.text_input(
                        "API Key", value=(edit_ep_obj.api_key or ""),
                        type="password", help="Leave empty to clear.",
                    )
                    ep_rpm = st.number_input(
                        "RPM Limit (0 = unlimited)",
                        value=edit_ep_obj.rpm_limit or 0, min_value=0, step=10,
                    )
                    ep_day = st.number_input(
                        "Daily Limit (0 = unlimited)",
                        value=edit_ep_obj.day_limit or 0, min_value=0, step=100,
                    )
                ep_active = st.checkbox("Active", value=edit_ep_obj.is_active)

                if st.form_submit_button("Save Changes"):
                    if not ep_name.strip() or not ep_base.strip():
                        st.error("Name and Base URL are required.")
                    else:
                        payload = {
                            "name": ep_name.strip(),
                            "base_url": ep_base.strip(),
                            "api_key": ep_key.strip() if ep_key.strip() else "",
                            "rpm_limit": ep_rpm,
                            "day_limit": ep_day,
                            "is_active": ep_active,
                        }
                        try:
                            resp = api_put(f"/admin/endpoints/{edit_ep_obj.id}", payload)
                            if resp.ok:
                                st.success(f"Endpoint **{ep_name.strip()}** updated.")
                                st.rerun()
                            else:
                                st.error(f"API Error: {resp.json().get('detail', resp.text)}")
                        except Exception as ex:
                            st.error(f"Connection error: {ex}")
    else:
        st.info("No endpoints configured. Add one below.")

    # --- Add new endpoint ---
    st.divider()
    st.subheader("Add Endpoint")
    with st.form("new_endpoint_form", clear_on_submit=True):
        nc1, nc2 = st.columns(2)
        with nc1:
            new_ep_name = st.text_input("Name", placeholder="OpenAI")
            new_ep_base = st.text_input("Base URL", placeholder="https://api.openai.com/v1")
        with nc2:
            new_ep_key = st.text_input("API Key", placeholder="sk-...", type="password")
            new_ep_rpm = st.number_input("RPM Limit (0 = unlimited)", value=0, min_value=0, step=10)
            new_ep_day = st.number_input("Daily Limit (0 = unlimited)", value=0, min_value=0, step=100)

        if st.form_submit_button("Create Endpoint"):
            if not new_ep_name.strip() or not new_ep_base.strip():
                st.error("Name and Base URL are required.")
            else:
                payload = {
                    "name": new_ep_name.strip(),
                    "base_url": new_ep_base.strip(),
                    "api_key": new_ep_key.strip() or None,
                    "rpm_limit": new_ep_rpm if new_ep_rpm > 0 else None,
                    "day_limit": new_ep_day if new_ep_day > 0 else None,
                }
                try:
                    resp = api_post("/admin/endpoints", payload)
                    if resp.ok:
                        result = resp.json()
                        st.success(f"Endpoint **{result['name']}** created.")
                        st.rerun()
                    else:
                        st.error(f"API Error: {resp.json().get('detail', resp.text)}")
                except Exception as ex:
                    st.error(f"Connection error: {ex}")


# =========================================================================
# Page: Models
# =========================================================================

elif page == "Models":
    st.title("Model Registry")
    st.caption("Configure model routing — models belong to endpoints")

    models = get_all_models()
    endpoints = get_all_endpoints()
    model_ids = [m.id for m in models]

    ep_map: dict[int, str] = {ep.id: ep.name for ep in endpoints}
    ep_options = ["— None (global default) —"] + [f"{ep.name} (#{ep.id})" for ep in endpoints]
    ep_id_list: list[int | None] = [None] + [ep.id for ep in endpoints]

    if models:
        rows = [
            {
                "Model ID": m.id,
                "LiteLLM Name": m.litellm_name,
                "Price In": m.price_in,
                "Price Out": m.price_out,
                "Endpoint": ep_map.get(m.endpoint_id, "— default —") if m.endpoint_id else "— default —",
                "RPM Override": m.rpm_limit or "—",
                "Daily Override": m.day_limit or "—",
                "Active": m.is_active,
            }
            for m in models
        ]
        st.dataframe(
            pd.DataFrame(rows).style.format({"Price In": "${:,.6f}", "Price Out": "${:,.6f}"}),
            use_container_width=True, hide_index=True,
        )

        # --- Edit existing model ---
        st.divider()
        selected_model = st.selectbox("Edit Model", ["— Select —"] + model_ids)

        if selected_model != "— Select —":
            edit_obj = next((m for m in models if m.id == selected_model), None)
            if edit_obj:
                st.subheader(f"Edit: {edit_obj.id}")

                default_ep_idx = 0
                if edit_obj.endpoint_id is not None:
                    try:
                        default_ep_idx = ep_id_list.index(edit_obj.endpoint_id)
                    except ValueError:
                        default_ep_idx = 0

                with st.form(f"edit_model_{edit_obj.id}", clear_on_submit=False):
                    mc1, mc2 = st.columns(2)
                    with mc1:
                        em_litellm = st.text_input(
                            "LiteLLM Internal Name", value=edit_obj.litellm_name,
                        )
                        em_price_in = st.number_input(
                            "Price / Input Token ($)", value=edit_obj.price_in,
                            format="%.6f", step=0.000001,
                        )
                    with mc2:
                        em_price_out = st.number_input(
                            "Price / Output Token ($)", value=edit_obj.price_out,
                            format="%.6f", step=0.000001,
                        )
                        em_active = st.checkbox("Active", value=edit_obj.is_active)

                    em_ep_idx = st.selectbox(
                        "Endpoint (Provider)", range(len(ep_options)),
                        format_func=lambda i: ep_options[i],
                        index=default_ep_idx,
                    )
                    em_endpoint_id = ep_id_list[em_ep_idx]

                    emc1, emc2 = st.columns(2)
                    with emc1:
                        em_rpm = st.number_input(
                            "RPM Override (0 = inherit)",
                            value=edit_obj.rpm_limit or 0, min_value=0, step=10,
                        )
                    with emc2:
                        em_day = st.number_input(
                            "Daily Override (0 = inherit)",
                            value=edit_obj.day_limit or 0, min_value=0, step=100,
                        )

                    if st.form_submit_button("Save Changes"):
                        payload = {
                            "litellm_name": em_litellm.strip(),
                            "price_in": em_price_in,
                            "price_out": em_price_out,
                            "endpoint_id": em_endpoint_id if em_endpoint_id else 0,
                            "rpm_limit": em_rpm,
                            "day_limit": em_day,
                            "is_active": em_active,
                        }
                        try:
                            resp = api_put(f"/admin/models/{edit_obj.id}", payload)
                            if resp.ok:
                                st.success(f"Model `{edit_obj.id}` updated.")
                                st.rerun()
                            else:
                                st.error(f"API Error: {resp.json().get('detail', resp.text)}")
                        except Exception as ex:
                            st.error(f"Connection error: {ex}")
    else:
        st.info("No models configured. Add one below.")

    # --- Add new model ---
    st.divider()
    st.subheader("Add Model")
    with st.form("new_model_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        with c1:
            fm_id = st.text_input("Public Model ID", placeholder="gpt-4-turbo")
            fm_litellm = st.text_input("LiteLLM Internal Name", placeholder="ollama/llama3")
        with c2:
            fm_price_in = st.number_input(
                "Price / Input Token ($)", value=0.000001, format="%.6f", step=0.000001,
            )
            fm_price_out = st.number_input(
                "Price / Output Token ($)", value=0.000002, format="%.6f", step=0.000001,
            )

        fm_ep_idx = st.selectbox(
            "Endpoint (Provider)", range(len(ep_options)),
            format_func=lambda i: ep_options[i],
        )
        fm_endpoint_id = ep_id_list[fm_ep_idx]

        rc1, rc2 = st.columns(2)
        with rc1:
            fm_rpm = st.number_input("RPM Override (0 = inherit)", value=0, min_value=0, step=10)
        with rc2:
            fm_day = st.number_input("Daily Override (0 = inherit)", value=0, min_value=0, step=100)

        if st.form_submit_button("Create Model"):
            final_id = fm_id.strip().lower().replace(" ", "-")
            if not final_id or not fm_litellm.strip():
                st.error("Model ID and LiteLLM Name are required.")
            else:
                if final_id != fm_id.strip():
                    st.info(f"Model ID normalized to `{final_id}`.")
                payload = {
                    "id": final_id,
                    "litellm_name": fm_litellm.strip(),
                    "price_in": fm_price_in,
                    "price_out": fm_price_out,
                    "endpoint_id": fm_endpoint_id,
                    "rpm_limit": fm_rpm if fm_rpm > 0 else None,
                    "day_limit": fm_day if fm_day > 0 else None,
                }
                try:
                    resp = api_post("/admin/models", payload)
                    if resp.ok:
                        result = resp.json()
                        st.success(f"Model `{result['model']}` created.")
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
            use_container_width=True, hide_index=True,
        )

        st.divider()
        s1, s2, s3 = st.columns(3)
        s1.metric("Shown Requests", f"{len(logs)}")
        s2.metric("Total Input Tokens", f"{sum(int(l.input_units) for l in logs):,}")
        s3.metric("Page Cost", f"${sum(l.total_cost for l in logs):,.6f}")
    else:
        st.info("No logs matching the current filters.")


# =========================================================================
# Page: Backup & Restore
# =========================================================================

elif page == "Backup":
    st.title("Backup & Restore")
    st.caption("Export or import your full system configuration")

    # --- Section 1: Export / Download ---
    st.subheader("Download Backup")
    st.markdown(
        "Export all **endpoints, models, users, and API keys** as a single JSON file. "
        "Use this to migrate to a new server or recover from a disaster."
    )

    if st.button("Generate Backup"):
        with st.spinner("Exporting configuration..."):
            try:
                resp = api_get("/admin/backup/export")
                if resp.ok:
                    backup_data = resp.json()
                    backup_json = json.dumps(backup_data, indent=2)
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    filename = f"aethergate_backup_{ts}.json"

                    ep_count = len(backup_data["data"].get("endpoints", []))
                    m_count = len(backup_data["data"].get("models", []))
                    u_count = len(backup_data["data"].get("users", []))
                    k_count = len(backup_data["data"].get("api_keys", []))

                    st.success(
                        f"Backup ready: **{ep_count}** endpoints, **{m_count}** models, "
                        f"**{u_count}** users, **{k_count}** API keys."
                    )
                    st.download_button(
                        label="Download JSON",
                        data=backup_json,
                        file_name=filename,
                        mime="application/json",
                    )
                else:
                    st.error(f"Export failed: {resp.json().get('detail', resp.text)}")
            except Exception as ex:
                st.error(f"Connection error: {ex}")

    st.divider()

    # --- Section 2: Import / Restore ---
    st.subheader("Restore from Backup")
    st.markdown(
        "Upload a previously exported JSON backup file. "
        "Existing records are **updated** (smart upsert); new records are created."
    )

    uploaded_file = st.file_uploader(
        "Choose a backup JSON file",
        type=["json"],
        help="Must be a file exported via the backup button above.",
    )

    if uploaded_file is not None:
        try:
            raw = uploaded_file.read().decode("utf-8")
            backup_data = json.loads(raw)

            # Quick validation
            if "version" not in backup_data or "data" not in backup_data:
                st.error("Invalid backup file — missing 'version' or 'data' key.")
            else:
                ep_count = len(backup_data["data"].get("endpoints", []))
                m_count = len(backup_data["data"].get("models", []))
                u_count = len(backup_data["data"].get("users", []))
                k_count = len(backup_data["data"].get("api_keys", []))

                st.info(
                    f"Backup v{backup_data['version']} from **{backup_data.get('timestamp', 'unknown')}** — "
                    f"{ep_count} endpoints, {m_count} models, {u_count} users, {k_count} API keys."
                )

                if st.button("Restore Now"):
                    with st.spinner("Importing configuration..."):
                        try:
                            resp = api_post("/admin/backup/import", backup_data)
                            if resp.ok:
                                result = resp.json()
                                restored = result.get("restored", {})
                                lines = []
                                for entity, counts in restored.items():
                                    c = counts.get("created", 0)
                                    u = counts.get("updated", 0)
                                    if c or u:
                                        parts = []
                                        if c:
                                            parts.append(f"{c} created")
                                        if u:
                                            parts.append(f"{u} updated")
                                        lines.append(f"**{entity}**: {', '.join(parts)}")
                                summary = "  \n".join(lines) if lines else "No changes needed."
                                st.success(f"Restore complete!  \n{summary}")
                                st.rerun()
                            else:
                                st.error(f"Import failed: {resp.json().get('detail', resp.text)}")
                        except Exception as ex:
                            st.error(f"Connection error: {ex}")
        except json.JSONDecodeError:
            st.error("Invalid JSON file. Please upload a valid backup.")
