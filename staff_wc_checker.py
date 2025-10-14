# staff_wc_checker.py — 🚻 Staff WC Number Check (auto, exact-match)
# Usage inside ch2.py:
#   from staff_wc_checker import render_staff_wc_check
#   ...
#   with tabs[4]:
#       render_staff_wc_check(ifc)

import json, os, re, math
from typing import Optional, List
import pandas as pd
import streamlit as st

try:
    import ifcopenshell
except Exception:
    ifcopenshell = None

# ----------- Fixed labels (EXACT match, case-insensitive) -----------
STANDARD_CLASSROOM_NAME = "classroom"
STANDARD_STAFF_WC_NAME = "staff wc"

# (kept for optional overrides; we enforce exact+case-insensitive anyway)
DEFAULT_CONFIG = {
    "matching": {
        "mode": "exact",          # enforced to exact
        "case_sensitive": False,  # enforced to False
        "ignore_numeric_names": True,
        "ignore_patterns": ["^tmp", "^test"],
    }
}

def load_config(path: str = "config.school.json"):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if "matching" in data and isinstance(data["matching"], dict):
                cfg["matching"].update(data["matching"])
        except Exception:
            pass
    # Enforce exact + case-insensitive regardless of file
    cfg["matching"]["mode"] = "exact"
    cfg["matching"]["case_sensitive"] = False
    return cfg

def get_space_name(space) -> str:
    nm = getattr(space, "Name", None) or ""
    ln = getattr(space, "LongName", None) or ""
    return ln.strip() if ln and ln.strip() else nm.strip()

def collect_unique_room_names(ifc, cfg) -> List[str]:
    spaces = ifc.by_type("IfcSpace") if ifc else []
    names = []
    ignore_re = None
    pats = cfg["matching"].get("ignore_patterns") or []
    if pats:
        try:
            ignore_re = re.compile("|".join(pats), flags=re.IGNORECASE)
        except re.error:
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
    seen, out = set(), []
    cs = cfg["matching"].get("case_sensitive", False)
    for n in names:
        key = n if cs else n.lower()
        if key not in seen:
            seen.add(key); out.append(n)
    out.sort(key=lambda s: s.lower())
    return out

def count_rooms_exact(ifc, label: str) -> int:
    """EXACT match (case-insensitive) on Name/LongName."""
    if not label:
        return 0
    spaces = ifc.by_type("IfcSpace") if ifc else []
    lab = label.lower()
    cnt = 0
    for sp in spaces:
        nm = get_space_name(sp)
        if not nm:
            continue
        if nm.lower() == lab:
            cnt += 1
    return cnt

# ----------- Public UI entry -----------
def render_staff_wc_check(ifc: Optional[object]):
    # No set_page_config here (handled by main app)
    st.caption("Code: 2-1-5")
    st.title("🚻 Staff WC Number Check")

    if ifc is None:
        st.info("Upload an IFC file in the main app to continue.")
        st.stop()

    if ifcopenshell is None:
        st.error("Python package 'ifcopenshell' is not installed.\nInstall via: pip install ifcopenshell")
        st.stop()

    cfg = load_config()
    names = collect_unique_room_names(ifc, cfg)
    st.caption(f"Detected unique space labels: **{len(names)}**")

    # Fixed labels (exact, case-insensitive)
    class_label = STANDARD_CLASSROOM_NAME
    staffwc_label = STANDARD_STAFF_WC_NAME

    # Auto counts
    cls_n = count_rooms_exact(ifc, class_label)
    staffwc_n = count_rooms_exact(ifc, staffwc_label)
    required = math.ceil(cls_n / 6) if cls_n > 0 else 0

    # Headline status
    cols = st.columns(3)
    cols[0].metric("Classrooms", cls_n)
    cols[1].metric("Staff WCs", staffwc_n)
    cols[2].metric("Required (1 per 6)", required)

    if staffwc_n >= required:
        st.success(
            f"✅ Staff WC count is sufficient.\n"
            f"**Classrooms:** {cls_n}\n**Required:** {required}\n**Provided:** {staffwc_n}"
        )
        status = "OK"
        deficit = 0
    else:
        deficit = max(0, required - staffwc_n)
        st.warning(
            f"⚠️ Not enough Staff WCs.\n"
            f"**Classrooms:** {cls_n}\n**Required:** {required}\n**Provided:** {staffwc_n}\n"
            f"**Needs {deficit} more.**"
        )
        status = "NOT_OK"

    # Summary table
    st.markdown("### Results summary")
    summary_df = pd.DataFrame(
        [
            {"Type": "Classroom", "Label": class_label,  "Count": cls_n},
            {"Type": "Staff WC",  "Label": staffwc_label, "Count": staffwc_n},
        ]
    )
    st.dataframe(summary_df, use_container_width=True, hide_index=True)

    # CSV export
    st.markdown("---")
    export_df = pd.DataFrame([{
        "class_label": class_label,
        "class_count": cls_n,
        "staff_wc_label": staffwc_label,
        "staff_wc_count": staffwc_n,
        "required": required,
        "status": status,
        "deficit": deficit,
    }])
    st.download_button(
        "⬇️ Download CSV",
        data=export_df.to_csv(index=False).encode("utf-8"),
        file_name="staff_wc_number_check.csv",
        mime="text/csv",
    )
