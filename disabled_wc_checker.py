# disabled_wc_checker.py — ♿ Disabled WC Check (auto, exact-match)
# Usage inside ch2.py:
#   from disabled_wc_checker import render_disabled_wc_check
#   with tabs[5]:
#       render_disabled_wc_check(ifc)

import pandas as pd
import streamlit as st
from typing import Optional

try:
    import ifcopenshell
except Exception:
    ifcopenshell = None

STANDARD_DISABLED_WC_NAME = "wc for disabled"  # exact label (case-insensitive)

def _get_space_name(space) -> str:
    nm = getattr(space, "Name", None) or ""
    ln = getattr(space, "LongName", None) or ""
    return ln.strip() if ln and ln.strip() else nm.strip()

def _count_rooms_exact_casefold(ifc, label: str) -> int:
    """EXACT match using str.casefold() (robust case-insensitive)."""
    if not (ifc and label):
        return 0
    want = label.casefold()
    cnt = 0
    for sp in ifc.by_type("IfcSpace") or []:
        nm = _get_space_name(sp)
        if nm and nm.casefold() == want:
            cnt += 1
    return cnt

def render_disabled_wc_check(ifc: Optional[object]):
    # No set_page_config here (main app handles it)
    st.caption("Code: 2-1-6")
    st.title("♿ Disabled WC — Presence & Count")

    if ifc is None:
        st.info("Upload an IFC file in the main app to continue.")
        st.stop()
    if ifcopenshell is None:
        st.error("Package 'ifcopenshell' is not installed. Install with: pip install ifcopenshell")
        st.stop()

    label = STANDARD_DISABLED_WC_NAME
    count = _count_rooms_exact_casefold(ifc, label)

    st.metric(label="Disabled WC count", value=count)

    if count > 0:
        st.success(f"✅ yes {count} disabled wc exists")
        status = "OK"
    else:
        st.error("❌ no wc for disabled is provided")
        status = "NOT_OK"

    st.markdown("### Results summary")
    df = pd.DataFrame([{"Label": label, "Count": count, "Status": status}])
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.markdown("---")
    st.download_button(
        "⬇️ Download CSV",
        data=df.to_csv(index=False).encode("utf-8"),
        file_name="disabled_wc_check.csv",
        mime="text/csv",
    )
