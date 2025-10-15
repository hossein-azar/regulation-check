# wc_number_checker.py — 🚻 WC Number Check (auto, exact-match)
# Usage inside your main Streamlit app (as a tab):
#
#   from wc_number_checker import render_wc_number_check
#   with tabs[3]:
#       render_wc_number_check(ifc)
#
# Notes:
# - No uploader here; relies on an IFC object you pass from the main app.
# - Fixed labels: "classroom" and "wc".
# - EXACT match (case-insensitive). "wc" matches only "wc" — not "wc-1" or "staff wc".
# - Auto-check on load; shows message + CSV export.

import os
import re
import json
from typing import Optional, List
import pandas as pd
import streamlit as st

try:
    import ifcopenshell
except ImportError:
    ifcopenshell = None

# ----------- Matching / config -----------
DEFAULT_CONFIG = {
    "matching": {
        "mode": "exact",             # <— force EXACT matching
        "case_sensitive": False,     # <— case-insensitive ("WC" == "wc")
        "ignore_numeric_names": True,
        "ignore_patterns": ["^tmp", "^test"],
    }
}

STANDARD_CLASSROOM_NAME = "classroom"
STANDARD_WC_NAME = "wc"

def load_config(path: str = "config.school.json"):
    """
    Optional external overrides. We then enforce exact, case-insensitive.
    """
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if "matching" in data and isinstance(data["matching"], dict):
                cfg["matching"].update(data["matching"])
        except Exception:
            pass

    # Enforce EXACT + case-insensitive regardless of external file
    cfg["matching"]["mode"] = "exact"
    cfg["matching"]["case_sensitive"] = False
    return cfg

# ----------- Helpers -----------
def get_space_name(space) -> str:
    nm = getattr(space, "Name", None) or ""
    ln = getattr(space, "LongName", None) or ""
    return (ln or nm).strip()

def collect_unique_room_names(ifc, cfg) -> List[str]:
    spaces = ifc.by_type("IfcSpace") if ifc else []
    names = []

    ignore_re = None
    pats = cfg["matching"].get("ignore_patterns") or []
    if pats:
        try:
            ignore_re = re.compile("|".join(pats), flags=re.IGNORECASE)
        except Exception:
            ignore_re = None

    for sp in spaces:
        nm = get_space_name(sp)
        if not nm:
            continue
        if cfg["matching"].get("ignore_numeric_names") and nm.isdigit():
            continue
        if ignore_re and ignore_re.match(nm):
            continue
        names.append(nm)

    seen = set()
    out = []
    cs = cfg["matching"].get("case_sensitive", False)
    for n in names:
        key = n if cs else n.lower()
        if key not in seen:
            seen.add(key)
            out.append(n)
    out.sort(key=lambda s: s.lower())
    return out

def count_rooms_by_label(ifc, label: str, cfg) -> int:
    """
    EXACT match (case-insensitive): only labels equal to `label` are counted.
    """
    if not label:
        return 0
    spaces = ifc.by_type("IfcSpace") if ifc else []
    cs = cfg["matching"].get("case_sensitive", False)

    label_cmp = label if cs else label.lower()
    count = 0
    for sp in spaces:
        nm = get_space_name(sp)
        if not nm:
            continue
        cmp_nm = nm if cs else nm.lower()
        if cmp_nm == label_cmp:   # <-- EXACT only
            count += 1
    return count

# ----------- Public UI entry -----------
def render_wc_number_check(ifc: Optional[object]):
    """
    Render the WC number check in a tab. Uses fixed labels:
    - Classroom: "classroom"
    - WC: "wc"
    Performs EXACT (case-insensitive) match and shows CSV export.
    """
    st.caption("Code: 2-1-4")
    st.title("🚻 WC Number Check")

    if ifc is None:
        st.info("Upload an IFC file in the main app to continue.")
        st.stop()

    if ifcopenshell is None:
        st.error("Python package 'ifcopenshell' is not installed.\nInstall via: pip install ifcopenshell")
        st.stop()

    # Config (enforces exact + case-insensitive)
    cfg = load_config()

    # Informational only
    unique_names = collect_unique_room_names(ifc, cfg)
    st.caption(f"Detected unique space labels: **{len(unique_names)}**")

    # Fixed labels, EXACT matching
    class_label = STANDARD_CLASSROOM_NAME
    wc_label = STANDARD_WC_NAME
    cls_n = count_rooms_by_label(ifc, class_label, cfg)
    wc_n = count_rooms_by_label(ifc, wc_label, cfg)

    # Counter row under title (3 metrics)
    cols = st.columns(3)
    cols[0].metric("Classrooms", cls_n)
    cols[1].metric("WCs", wc_n)
    cols[2].metric("Required", cls_n)


    # Result message (auto, no button)
    if wc_n >= cls_n:
        st.success(f"✅ Number of WCs is good.\nWCs: {wc_n}  |  Classrooms: {cls_n}")
        status = "OK"
        deficit = 0
    else:
        deficit = cls_n - wc_n
        st.warning(f"⚠️ Not enough WCs.\nWCs: {wc_n}  |  Classrooms: {cls_n}\nNeeds {deficit} more.")
        status = "NOT_OK"

    # Results summary table
    st.markdown("### Results summary")
    summary_df = pd.DataFrame(
        [
            {"Type": "Classroom", "Label": class_label, "Count": cls_n},
            {"Type": "WC",        "Label": wc_label,    "Count": wc_n},
        ]
    )
    st.dataframe(summary_df, use_container_width=True)

    # CSV export (single file with both summary & headline)
    st.markdown("---")
    export_df = pd.DataFrame([{
        "class_label": class_label,
        "class_count": cls_n,
        "wc_label": wc_label,
        "wc_count": wc_n,
        "status": status,
        "deficit": deficit,
    }])

    csv_bytes = export_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇️ Download CSV",
        data=csv_bytes,
        file_name="wc_number_check.csv",
        mime="text/csv",
    )
