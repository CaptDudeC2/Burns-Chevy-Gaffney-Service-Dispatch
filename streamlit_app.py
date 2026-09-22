"""Burns Chevy Gaffney - Service Dispatch board.

Reads the Dispatch workbook live from its shared Google Sheets link
and renders the dispatch board: master appointments, per-technician
views, carryovers, parts-here work, and the parts-on-shelf call list.

This app is READ ONLY - all editing stays in the Google Sheet.
"""

import pandas as pd
import streamlit as st

SHEET_ID = "1H2cKiSGzrd5gI97G6ZC2iEgVP4kFmspysWC45Hq-LfE"
GID_APPOINTMENTS = "773153683"
GID_PARTS_NO_VEHICLE = "708183503"
TECH_TABS = {
    "Angel Estrada": "321059552",
    "Matt Wilson": "92856338",
    "Brad Lotze": "947740012",
    "Emmett Knapp": "1361272945",
    "Daniel Estrada": "1855565445",
    "Mitchel Lanier": "414320646",
    "Eugene Philpotts": "600425335",
    "Sammy Mosqueda": "209988930",
}


@st.cache_data(ttl=300)
def load_csv(gid):
    """Load one sheet tab as a dataframe of strings."""
    url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={gid}"
    return pd.read_csv(url, dtype=str).fillna("")


def parse_tech_tab(df):
    """Split a technician tab into its APPOINTMENTS / CARRYOVERS / PARTS HERE sections."""
    rows = df.values.tolist()
    markers = {"APPOINTMENTS", "CARRYOVERS", "PARTS HERE - WORK NEEDS COMPLETE"}
    sections = {m: [] for m in markers}
    current = None
    header = None
    for row in rows:
        cells = [str(c).strip() for c in row]
        first = cells[0].upper()
        if first in markers:
            current, header = first, None
            continue
        if current is None:
            continue
        if all(c == "" for c in cells):
            continue
        if header is None:
            header = cells
            continue
        rec = {h: v for h, v in zip(header, cells) if h}
        if rec.get(header[0], "") == "":
            continue
        sections[current].append(rec)

    out = {}
    for name, recs in sections.items():
        if recs and recs[0].get(list(recs[0].keys())[0], "").lower() == "none currently":
            recs = []
        out[name] = pd.DataFrame(recs).fillna("") if recs else pd.DataFrame()
    return out


def sort_by_time(df):
    if df.empty or "Time" not in df.columns:
        return df
    t = pd.to_datetime(df["Time"].str.strip(), format="%I:%M %p", errors="coerce")
    return df.assign(_t=t).sort_values("_t").drop(columns="_t")


st.set_page_config(page_title="Gaffney Service Dispatch", page_icon="🧰", layout="wide")
st.title("🧰 Gaffney Service Dispatch")
st.caption("Live from the Dispatch workbook. Read-only - all edits happen in the Google Sheet.")

top_col, _ = st.columns([1, 6])
if top_col.button("↻ Refresh now"):
    st.cache_data.clear()
    st.rerun()

try:
    appts = load_csv(GID_APPOINTMENTS)
except Exception as exc:
    st.error(f"Couldn't load the Appointments tab from the workbook: {exc}")
    st.stop()

tab_board, tab_techs, tab_parts, tab_about = st.tabs(
    ["📅 Dispatch Board", "🧑‍🔧 Technicians", "📦 Parts Here — Call List", "❓ How It Works"]
)

with tab_board:
    dates = sorted(appts["Date"].unique(), key=lambda d: pd.to_datetime(d, errors="coerce"))
    if not dates:
        st.info("No appointments found in the workbook.")
    else:
        day = st.selectbox("Day", dates, index=0)
        day_df = appts[appts["Date"] == day].copy()

        f1, f2 = st.columns(2)
        techs = sorted(t for t in day_df["Technician"].unique() if t)
        tech_sel = f1.multiselect("Technician", techs, default=techs)
        statuses = sorted(s for s in day_df["Status"].unique() if s)
        status_sel = f2.multiselect("Status", statuses, default=statuses)

        day_df = day_df[day_df["Technician"].isin(tech_sel) & day_df["Status"].isin(status_sel)]
        day_df = sort_by_time(day_df)

        m1, m2, m3 = st.columns(3)
        m1.metric("Appointments", len(day_df))
        m2.metric("Scheduled", int((day_df["Status"] == "Scheduled").sum()))
        m3.metric("Open ROs", int((day_df["Status"] == "Open RO").sum()))

        cols = ["Time", "Customer", "Vehicle", "Technician", "Advisor", "Concern", "Status"]
        st.dataframe(day_df[cols], use_container_width=True, hide_index=True)

with tab_techs:
    tech = st.selectbox("Technician", list(TECH_TABS))
    try:
        raw = load_csv(TECH_TABS[tech])
        sections = parse_tech_tab(raw)
    except Exception as exc:
        st.error(f"Couldn't load {tech}'s tab: {exc}")
        st.stop()

    t_appts = sections["APPOINTMENTS"]
    t_carry = sections["CARRYOVERS"]
    t_parts = sections["PARTS HERE - WORK NEEDS COMPLETE"]

    c1, c2, c3 = st.columns(3)
    c1.metric("Appointments", len(t_appts))
    c2.metric("Carryovers", len(t_carry))
    c3.metric("Parts Here — Work Pending", len(t_parts))

    st.subheader("Appointments")
    if t_appts.empty:
        st.write("None scheduled.")
    else:
        st.dataframe(sort_by_time(t_appts), use_container_width=True, hide_index=True)

    st.subheader("Carryovers")
    if t_carry.empty:
        st.write("None currently.")
    else:
        st.dataframe(t_carry, use_container_width=True, hide_index=True)

    st.subheader("Parts Here — Work Needs Complete")
    if t_parts.empty:
        st.write("None currently.")
    else:
        st.dataframe(t_parts, use_container_width=True, hide_index=True)

with tab_parts:
    st.subheader("📦 Parts Here — No Vehicle")
    st.write(
        "All the parts below are on the shelf. A quick friendly call gets these "
        "customers back in and their vehicles finished."
    )
    try:
        call = load_csv(GID_PARTS_NO_VEHICLE)
    except Exception as exc:
        st.error(f"Couldn't load the parts call list: {exc}")
        st.stop()

    if not call.empty and "Age" in call.columns:
        call = call.assign(_age=pd.to_numeric(call["Age"], errors="coerce")).sort_values(
            "_age", ascending=False
        ).drop(columns="_age")
    st.metric("Customers to call", len(call))
    show_cols = [c for c in ["RO #", "Age", "Customer", "Phone", "Vehicle", "Advisor", "Note",
                             "Last Contact", "Result"] if c in call.columns]
    st.dataframe(call[show_cols], use_container_width=True, hide_index=True)

with tab_about:
    st.subheader("How this works")
    st.markdown(
        """
- The **Appointments**, **Carryovers**, and **Parts On Hand** source tabs are loaded
  daily from the Tekion export.
- Technician tabs fill automatically via filters — **do not type into the filtered rows**.
- **Hours** and **Parts $** on tech tabs are Phase 2 (hours per job with parts) — not active yet.
- This app reads the workbook live through its shared link and refreshes every 5 minutes.
  Use **↻ Refresh now** any time.
- Everything you see here is read-only. Edits still happen in the Google Sheet.
        """
    )
