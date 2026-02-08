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
SYNC_DATABASE_URL = "sqlite:///./aethergate.db"
engine = create_engine(SYNC_DATABASE_URL, echo=False)

API_URL = os.getenv("API_URL", "http://localhost:8000")
MASTER_KEY = os.getenv("MASTER_API_KEY", "sk-admin-master-key")

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
    else:
        st.info("No users yet.")

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
    else:
        st.info("No endpoints configured. Add one below.")

    st.divider()

    # --- Load for editing ---
    ep_names = [ep.name for ep in endpoints]
    if ep_names:
        selected_ep = st.selectbox(
            "Load Endpoint for Editing",
            ["— New Endpoint —"] + ep_names,
        )
    else:
        selected_ep = "— New Endpoint —"

    editing_ep = selected_ep != "— New Endpoint —"
    edit_ep_obj: LLMEndpoint | None = None
    if editing_ep:
        edit_ep_obj = next((ep for ep in endpoints if ep.name == selected_ep), None)

    st.subheader("Edit Endpoint" if edit_ep_obj else "Add Endpoint")

    with st.form("endpoint_form", clear_on_submit=False):
        ec1, ec2 = st.columns(2)
        with ec1:
            ep_name = st.text_input(
                "Name", value=edit_ep_obj.name if edit_ep_obj else "",
                placeholder="OpenAI", disabled=bool(edit_ep_obj),
            )
            ep_base = st.text_input(
                "Base URL", value=edit_ep_obj.base_url if edit_ep_obj else "",
                placeholder="https://api.openai.com/v1",
            )
        with ec2:
            ep_key = st.text_input(
                "API Key", value=(edit_ep_obj.api_key or "") if edit_ep_obj else "",
                placeholder="sk-...", type="password",
                help="Provider authentication key. Leave empty if not required.",
            )
            ep_rpm = st.number_input(
                "Global RPM Limit", value=edit_ep_obj.rpm_limit or 0 if edit_ep_obj else 0,
                min_value=0, step=10, help="0 = unlimited",
            )
            ep_day = st.number_input(
                "Global Daily Limit", value=edit_ep_obj.day_limit or 0 if edit_ep_obj else 0,
                min_value=0, step=100, help="0 = unlimited",
            )

        if st.form_submit_button("Save Endpoint"):
            final_name = edit_ep_obj.name if edit_ep_obj else ep_name.strip()
            if not final_name or not ep_base.strip():
                st.error("Name and Base URL are required.")
            else:
                payload = {
                    "name": final_name,
                    "base_url": ep_base.strip(),
                    "api_key": ep_key.strip() or None,
                    "rpm_limit": ep_rpm if ep_rpm > 0 else None,
                    "day_limit": ep_day if ep_day > 0 else None,
                }
                try:
                    resp = api_post("/admin/endpoints", payload)
                    if resp.ok:
                        result = resp.json()
                        st.success(f"Endpoint **{result['name']}** {result['action']}.")
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

    # Build endpoint lookup for the selectbox
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
    else:
        st.info("No models configured. Add one below.")

    st.divider()

    # --- Load for editing ---
    if model_ids:
        selected = st.selectbox(
            "Load Model for Editing",
            ["— New Model —"] + model_ids,
        )
    else:
        selected = "— New Model —"

    editing = selected != "— New Model —"
    edit_obj: LLMModel | None = None
    if editing:
        edit_obj = next((m for m in models if m.id == selected), None)

    st.subheader("Edit Model" if edit_obj else "Configure Model")

    # Determine default index for endpoint selectbox
    default_ep_idx = 0
    if edit_obj and edit_obj.endpoint_id is not None:
        try:
            default_ep_idx = ep_id_list.index(edit_obj.endpoint_id)
        except ValueError:
            default_ep_idx = 0

    with st.form("model_config_form", clear_on_submit=False):
        c1, c2 = st.columns(2)
        with c1:
            fm_id = st.text_input(
                "Public Model ID",
                value=edit_obj.id if edit_obj else "",
                placeholder="gpt-4-turbo", disabled=bool(edit_obj),
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
                format="%.6f", step=0.000001,
            )
            fm_price_out = st.number_input(
                "Price / Output Token ($)",
                value=edit_obj.price_out if edit_obj else 0.000002,
                format="%.6f", step=0.000001,
            )

        # Endpoint selector (replaces old api_base / api_key inputs)
        fm_ep_idx = st.selectbox("Endpoint (Provider)", range(len(ep_options)),
                                  format_func=lambda i: ep_options[i],
                                  index=default_ep_idx)
        fm_endpoint_id = ep_id_list[fm_ep_idx]

        rc1, rc2 = st.columns(2)
        with rc1:
            fm_rpm = st.number_input(
                "RPM Override (0 = inherit from endpoint)",
                value=edit_obj.rpm_limit or 0 if edit_obj else 0,
                min_value=0, step=10,
            )
        with rc2:
            fm_day = st.number_input(
                "Daily Override (0 = inherit from endpoint)",
                value=edit_obj.day_limit or 0 if edit_obj else 0,
                min_value=0, step=100,
            )

        if st.form_submit_button("Save Configuration"):
            final_id = edit_obj.id if edit_obj else fm_id.strip().lower().replace(" ", "-")
            if not final_id or not fm_litellm.strip():
                st.error("Model ID and LiteLLM Name are required.")
            else:
                if not edit_obj and final_id != fm_id.strip():
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
                        st.success(f"Model `{result['model']}` **{result['action']}**.")
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
