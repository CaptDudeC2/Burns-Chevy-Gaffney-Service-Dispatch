"""Burns Chevy Gaffney - Service Dispatch board.

Reads the Dispatch workbook live from its shared Google Sheets link
and renders the dispatch board: master appointments, per-technician
views, carryovers, parts-here work, and the parts-on-shelf call list.

This app is READ ONLY - all editing stays in the Google Sheet.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

SHEET_ID = "1H2cKiSGzrd5gI97G6ZC2iEgVP4kFmspysWC45Hq-LfE"
GID_APPOINTMENTS = "773153683"
GID_PARTS_NO_VEHICLE = "708183503"
TECH_TABS = {
    "Angel Estrada": "321059552",
    "Ta'Jio Reeves": "2073650491",
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


def load_call_list():
    """Load the Parts Here call list, skipping the banner row if present."""
    df = load_csv(GID_PARTS_NO_VEHICLE)
    if "RO #" not in df.columns and len(df):
        df = df.copy()
        df.columns = df.iloc[0]
        df = df.iloc[1:].reset_index(drop=True)
    return df.fillna("")


# ---- Call Outs and Time Off (from the Attendance Calendar sheet) ----
ATT_SHEET_ID = "1PZPpWbfWYGi1SqjvEMtAPpxukRTxnrnlqQWHNSZ4pRI"
# Month tab name -> gid. Tabs are fixed Sept 2026 - Dec 2027.
ATT_TAB_GIDS = {
    "September 2026": "1000",
    "October 2026": "1001",
    "November 2026": "1002",
    "December 2026": "1003",
    "January 2027": "1004",
    "February 2027": "1005",
    "March 2027": "1006",
    "April 2027": "1007",
    "May 2027": "1008",
    "June 2027": "1009",
    "July 2027": "1010",
    "August 2027": "1011",
    "September 2027": "1012",
    "October 2027": "1013",
    "November 2027": "1014",
    "December 2027": "1015",
    "Closed Dates": "1016",
}
# (first name as written in calendar notes, full name, role)
ATT_ROSTER = [
    ("Danny", "Daniel Estrada", "Technician"),
    ("Sammy", "Samuel Mosqueda", "Technician"),
    ("Angel", "Angel Estrada", "Technician"),
    ("Eugene", "Eugene Philpotts", "Technician"),
    ("Emmett", "Emmett Knapp", "Technician"),
    ("Mitchell", "Mitchell Lanier", "Technician"),
    ("Brad", "Bradley Lotze", "Technician"),
    ("Ta'Jio", "Ta'Jio Reeves", "Technician"),
    ("Felicia", "Felicia Foster", "BDC"),
    ("Dylan", "Dylan Patton", "Advisor"),
    ("Jessica", "Jessica Schurz", "Advisor"),
]
# keyword (lowercase) -> display reason, checked in order
ATT_REASONS = [
    ("called out", "Called out"),
    ("call out", "Called out"),
    ("callout", "Called out"),
    ("pto", "PTO"),
    ("vacation", "Vacation"),
    ("sick", "Sick"),
    ("bereavement", "Bereavement"),
    ("funeral", "Bereavement"),
    ("jury duty", "Jury duty"),
    ("day off", "Day off"),
    ("off", "Day off"),
]


@st.cache_data(ttl=300)
def load_att_tab(gid):
    """Load one Attendance Calendar tab as a dataframe of strings (no header)."""
    url = (
        f"https://docs.google.com/spreadsheets/d/{ATT_SHEET_ID}"
        f"/export?format=csv&gid={gid}"
    )
    return pd.read_csv(url, header=None, dtype=str).fillna("")


def att_month_weeks(df):
    """Parse a calendar month tab into weeks of (day_number, [notes]) cells."""
    rows = df.values.tolist()
    weeks = []
    i, n = 0, len(rows)
    while i < n:
        cells = [str(c).strip() for c in rows[i]]
        if sum(c.isdigit() for c in cells) >= 3:
            week = []
            for col in range(7):
                c = cells[col] if col < len(cells) else ""
                if c.isdigit():
                    notes = []
                    for r in rows[i + 1 : i + 5]:
                        if col < len(r):
                            v = str(r[col]).strip()
                            if v:
                                notes.append(v)
                    week.append((int(c), notes))
                else:
                    week.append(None)
            weeks.append(week)
            i += 5
        else:
            i += 1
    return weeks


def att_parse_note(note):
    """Return (full_name, reason) for a calendar note. Name may be None."""
    low = note.lower()
    reason = None
    for key, label in ATT_REASONS:
        if key in low:
            reason = label
            break
    if not reason:
        return None, None
    for first, full, _role in ATT_ROSTER:
        if first.lower() in low or full.lower() in low:
            return full, reason
    return None, reason


def att_closed_map():
    """ISO date -> holiday name from the calendar's Closed Dates tab."""
    try:
        df = load_att_tab(ATT_TAB_GIDS["Closed Dates"])
    except Exception:
        return {}
    out = {}
    for row in df.values.tolist():
        if len(row) >= 1 and str(row[0]).strip():
            name = (
                str(row[1]).strip()
                if len(row) > 1 and str(row[1]).strip()
                else "Closed"
            )
            out[str(row[0]).strip()] = name
    return out


def _section_for(label):
    """Map a section label row to a canonical section name (labels vary across tabs)."""
    up = label.strip().upper()
    if up.startswith("APPOINTMENT"):
        return "appointments"
    if up.startswith("CARRYOVER"):
        return "carryovers"
    if "PARTS" in up:
        return "parts"
    if up.startswith("TOTALS"):
        return None
    return "unknown"


def parse_tech_tab(df):
    """Split a technician tab into appointments / carryovers / parts sections."""
    sections = {"appointments": [], "carryovers": [], "parts": []}
    current = None
    header = None
    for row in df.values.tolist():
        cells = [str(c).strip() for c in row]
        if all(c == "" for c in cells):
            continue
        kind = _section_for(cells[0])
        if kind == "unknown":
            pass
        elif kind is None:
            current, header = None, None
            continue
        else:
            current, header = kind, None
            continue
        if current is None:
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

# ---- Call Outs and Time Off (today, from the Attendance Calendar) ----
st.markdown("### Call Outs and Time Off")
try:
    today = datetime.now(ZoneInfo("America/New_York")).date()
    st.caption(today.strftime("%A, %B %d, %Y"))
    closed_today = att_closed_map().get(today.isoformat())
    if today.weekday() == 6 or closed_today:
        st.warning(f"Shop closed — {closed_today or 'Sunday'}")
    else:
        weeks = att_month_weeks(load_att_tab(ATT_TAB_GIDS[today.strftime("%B %Y")]))
        todays_notes = []
        for week in weeks:
            for cell in week:
                if cell and cell[0] == today.day:
                    todays_notes = cell[1]
        outs, unnamed = {}, []
        for note in todays_notes:
            name, reason = att_parse_note(note)
            if name:
                outs[name] = reason
            elif reason:
                unnamed.append(f"{note} ({reason})")
        if outs or unnamed:
            for _f, full, role in ATT_ROSTER:
                if full in outs:
                    st.write(f"**{full}** — {outs[full]} ({role})")
            for u in unnamed:
                st.write(u)
        else:
            st.write("Everyone is in today.")
except Exception as exc:
    st.caption(f"Couldn't load the Attendance Calendar: {exc}")

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
        if (day_df["Status"] == "").any():
            statuses = statuses + ["(No status)"]
        status_sel = f2.multiselect("Status", statuses, default=statuses)

        status_mask = day_df["Status"].isin(s for s in status_sel if s != "(No status)")
        if "(No status)" in status_sel:
            status_mask = status_mask | (day_df["Status"] == "")
        day_df = day_df[day_df["Technician"].isin(tech_sel) & status_mask]
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

    t_appts = sections["appointments"]
    t_carry = sections["carryovers"]
    t_parts = sections["parts"]

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
        call = load_call_list()
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
