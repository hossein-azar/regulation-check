# area_per_capita_checker.py — 📏 Area per Capita Check (embed-friendly, auto; Library fixed 24)
# - Use inside another app:
#     from area_per_capita_checker import run_area_per_capita_check
#     with tabs[...]:
#         run_area_per_capita_check(ifc)     # no sidebar uploader, uses passed IFC
#
# - Or run standalone:
#     streamlit run area_per_capita_checker.py
#     (will show its own sidebar uploader)

from __future__ import annotations
import re
import tempfile
from io import StringIO
from typing import Optional, Dict, List
import pandas as pd
import streamlit as st

# ---------- Optional deps ----------
try:
    import ifcopenshell
except Exception:
    ifcopenshell = None

try:
    import ifcopenshell.geom as ifcgeom
except Exception:
    ifcgeom = None

try:
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    _HAS_SHAPELY = True
except Exception:
    _HAS_SHAPELY = False

# ---------- Constants ----------
SCHOOL_TYPES = ["ebtedaei dore 1", "ebtedaei dore 2", "motevasete dore 1", "motevasete dore 2"]

COEFFS = {
    "Classroom":     [1.7, 1.85, 1.8, 2.0],
    "Workshop":      [2.5, 2.7, 3.0, 0.0],
    "Labs":          [2.02, 2.02, 3.2, 3.2],
    "Computer Site": [2.02, 2.02, 2.55, 2.55],
    "Praying Room":  [0.8, 0.8, 0.9, 0.9],
    "Library":       [1.6, 1.8, 2.0, 2.0],
}
LIBRARY_FIXED_STUDENTS = 24

STANDARD_TO_RULE = {
    "classroom": "Classroom",
    "workshop": "Workshop",
    "laboratory": "Labs",
    "computer site": "Computer Site",
    "praying room": "Praying Room",
    "library": "Library",
}
RUN_ORDER = ["classroom", "workshop", "laboratory", "computer site", "praying room", "library"]

STANDARD_STUDENT_CHAIR = "student chair"

# ---------- Helpers ----------
NUM_TOKEN_RE = re.compile(r"(?:^|\s)(?:#?\d+)(?=\s|$)")

def strip_numeric_tokens(s: str) -> str:
    s = " ".join(s.strip().split())
    s = NUM_TOKEN_RE.sub(" ", s)
    return " ".join(s.split())

def canonicalize(label: str) -> str:
    if not label:
        return ""
    return strip_numeric_tokens(" ".join(label.strip().split())).lower()

def best_room_label(space) -> str:
    for attr in ("LongName", "Name"):
        v = getattr(space, attr, None)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""

def _si_prefix_scale(prefix):
    m = {"EXA":1e18,"PETA":1e15,"TERA":1e12,"GIGA":1e9,"MEGA":1e6,"KILO":1e3,
         "HECTO":1e2,"DECA":1e1,"DECI":1e-1,"CENTI":1e-2,"MILLI":1e-3,"MICRO":1e-6,
         "NANO":1e-9,"PICO":1e-12,"FEMTO":1e-15,"ATTO":1e-18}
    return m.get((prefix or "").upper(), 1.0)

def get_length_scale_m(ifc):
    try:
        proj = ifc.by_type("IfcProject")[0]
        ua = getattr(proj, "UnitsInContext", None)
        if not ua:
            return 1.0
        for u in ua.Units or []:
            if u.is_a("IfcSIUnit") and getattr(u, "UnitType", None) == "LENGTHUNIT":
                name = getattr(u, "Name", "METRE")
                prefix = getattr(u, "Prefix", None)
                if name == "METRE":
                    return _si_prefix_scale(prefix) if prefix else 1.0
            if u.is_a("IfcConversionBasedUnit") and getattr(u, "UnitType", None) == "LENGTHUNIT":
                mu = u.ConversionFactor
                val = float(getattr(mu, "ValueComponent", 1.0))
                unit = mu.UnitComponent
                if unit.is_a("IfcSIUnit") and getattr(unit, "Name", None) == "METRE":
                    return val
    except Exception:
        pass
    return 1.0

def get_area_scale_m2(ifc):
    s = get_length_scale_m(ifc)
    return s * s

def area_from_shape_mesh(shape) -> float:
    if not _HAS_SHAPELY or shape is None:
        return 0.0
    try:
        geom = shape.geometry
        verts = geom.verts
        faces = geom.faces
    except Exception:
        return 0.0
    coords3d = [(verts[i], verts[i+1], verts[i+2]) for i in range(0, len(verts), 3)]
    if not coords3d:
        return 0.0
    tris = []
    for i in range(0, len(faces), 3):
        try:
            a = coords3d[faces[i]]; b = coords3d[faces[i+1]]; c = coords3d[faces[i+2]]
        except IndexError:
            continue
        try:
            p = Polygon([(a[0], a[1]), (b[0], b[1]), (c[0], c[1])])
            if p.is_valid and not p.is_empty and p.area > 0:
                tris.append(p)
        except Exception:
            pass
    if not tris:
        return 0.0
    try:
        merged = unary_union(tris)
        return float(merged.area) if merged and not merged.is_empty else 0.0
    except Exception:
        pts = []
        for p in tris:
            pts.extend(list(p.exterior.coords))
        return Polygon(pts).convex_hull.area if pts else 0.0

def total_area_for_standard_key(ifc, std_key: str) -> float:
    """Sum areas of IfcSpace where canonicalized Name/LongName equals std_key."""
    if ifcgeom is None or not _HAS_SHAPELY:
        return 0.0
    target = std_key.strip().lower()
    settings = ifcgeom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    area_scale = get_area_scale_m2(ifc)
    total = 0.0
    for sp in ifc.by_type("IfcSpace"):
        n = best_room_label(sp)
        if not n or canonicalize(n) != target:
            continue
        try:
            shape = ifcgeom.create_shape(settings, sp)
        except Exception:
            continue
        a_model = area_from_shape_mesh(shape)
        if a_model > 0:
            total += a_model * area_scale
    return total

# ---------- Furniture helpers (for student chairs) ----------
def best_furnishing_label(elem, ifc):
    for attr in ("Name", "ObjectType"):
        v = getattr(elem, attr, None)
        if isinstance(v, str) and v.strip():
            return v.strip()
    try:
        for inv in ifc.get_inverse(elem):
            if inv.is_a("IfcRelDefinesByType"):
                t = inv.RelatingType
                if t:
                    for attr in ("Name", "ElementType", "Tag"):
                        tv = getattr(t, attr, None)
                        if isinstance(tv, str) and tv.strip():
                            return tv.strip()
    except Exception:
        pass
    tag = getattr(elem, "Tag", None)
    if isinstance(tag, str) and tag.strip():
        return tag.strip()
    return ""

def collect_furniture_instance_labels(ifc):
    labels = []
    for e in ifc.by_type("IfcFurnishingElement"):
        lab = best_furnishing_label(e, ifc)
        if lab: labels.append(lab)
    try:
        for e in ifc.by_type("IfcFurniture"):
            lab = best_furnishing_label(e, ifc)
            if lab: labels.append(lab)
    except RuntimeError:
        pass
    return labels

def build_furn_map(ifc) -> Dict[str, Dict[str,int]]:
    type_map: Dict[str, Dict[str,int]] = {}
    for lbl in collect_furniture_instance_labels(ifc):
        key = canonicalize(lbl)
        if not key: continue
        disp = strip_numeric_tokens(lbl)
        if key not in type_map:
            type_map[key] = {"display": disp, "count": 0}
        type_map[key]["count"] += 1
    return type_map

# ---------- Public UI entry ----------
def run_area_per_capita_check(ifc: Optional[object]):
    """Render Area-per-Capita check. No uploader here; pass an IFC object."""
    st.caption("Code: 2-2-1")

    # Dependency checks
    if ifcopenshell is None:
        st.error("Package **ifcopenshell** is not installed. `pip install ifcopenshell`")
        st.stop()
    if ifc is None and st.session_state.get("ifc") is not None:
        ifc = st.session_state.ifc
    if ifc is None:
        st.info("Upload an IFC file in the main app to continue.")
        st.stop()

    # Cache furniture map (for student chair auto-count)
    if "apc_furn_map" not in st.session_state:
        st.session_state.apc_furn_map = build_furn_map(ifc)

    # 1) School type
    st.subheader("1) School Type")
    school_choice = st.selectbox("Select school type", SCHOOL_TYPES, index=0)

    # 2) Students — AUTOMATIC via furniture label "student chair"
    st.subheader("2) Number of Students")
    auto_students = sum(
        v["count"] for v in st.session_state.apc_furn_map.values()
        if STANDARD_STUDENT_CHAIR.lower() in v["display"].lower()
    )
    st.session_state.apc_students = int(auto_students)

    cols = st.columns(2)
    cols[0].metric("Detected student chairs", st.session_state.apc_students)
    st.markdown("---")

    # 3) Results (auto)
    st.subheader("3) Results for Standard Room Names")
    st.caption("Library always uses **24 students** regardless of detected students.")

    results: List[Dict[str, str | int | float]] = []
    idx_school = SCHOOL_TYPES.index(school_choice)
    students_detected = int(st.session_state.apc_students or 0)

    # Quick guard for geometry deps
    if ifcgeom is None or not _HAS_SHAPELY:
        st.error("Geometry dependencies missing: need `ifcopenshell-geom` and `shapely` for area checks.")
        st.stop()

    for std_key in RUN_ORDER:
        available_area = total_area_for_standard_key(ifc, std_key) or 0.0
        rule_group = STANDARD_TO_RULE.get(std_key, None)

        if rule_group is None or rule_group not in COEFFS:
            if available_area <= 0:
                st.warning(f"There is no room for **{std_key}** in the model.")
            else:
                st.info(f"{std_key}: area found = {available_area:.2f} m² (no per-capita rule).")
            results.append({
                "Standard Name": std_key, "Rule Group": "-",
                "Students Used": "-", "Per-capita (m²/stud)": "-",
                "Required (m²)": "-", "Available (m²)": f"{available_area:.2f}",
                "Status": "NOT REQUIRED", "Shortfall (m²)": "-",
            })
            continue

        per_capita = float(COEFFS[rule_group][idx_school])

        if per_capita == 0.0:
            if available_area <= 0:
                st.warning(f"There is no room for **{std_key}** in the model.")
            else:
                st.info(f"{rule_group} ({std_key}) → not required for this school type.")
            results.append({
                "Standard Name": std_key, "Rule Group": rule_group,
                "Students Used": "-", "Per-capita (m²/stud)": f"{per_capita:.2f}",
                "Required (m²)": "-", "Available (m²)": f"{available_area:.2f}",
                "Status": "NOT REQUIRED", "Shortfall (m²)": "-",
            })
            continue

        students_used = LIBRARY_FIXED_STUDENTS if rule_group == "Library" else students_detected

        if available_area <= 0:
            req_area_if_none = students_used * per_capita
            st.warning(f"There is no room for **{rule_group}** (standard: {std_key}).")
            results.append({
                "Standard Name": std_key, "Rule Group": rule_group,
                "Students Used": students_used, "Per-capita (m²/stud)": f"{per_capita:.2f}",
                "Required (m²)": f"{req_area_if_none:.2f}",
                "Available (m²)": f"{available_area:.2f}",
                "Status": "NO ROOM", "Shortfall (m²)": f"{req_area_if_none:.2f}",
            })
            continue

        required_area = students_used * per_capita
        ok = available_area >= required_area
        shortfall = 0.0 if ok else (required_area - available_area)

        if ok:
            st.success(
                f"✅ {rule_group} ({std_key}): enough.\n\n"
                f"Students used: {students_used}\n\n"
                f"Required: {required_area:.2f} m²\n\nAvailable: {available_area:.2f} m²"
            )
        else:
            st.error(
                f"❌ {rule_group} ({std_key}): small.\n\n"
                f"Students used: {students_used}\n\n"
                f"Required: {required_area:.2f} m²\n\nAvailable: {available_area:.2f} m²\n\n"
                f"Short by: {shortfall:.2f} m²"
            )

        results.append({
            "Standard Name": std_key,
            "Rule Group": rule_group,
            "Students Used": students_used,
            "Per-capita (m²/stud)": f"{per_capita:.2f}",
            "Required (m²)": f"{required_area:.2f}",
            "Available (m²)": f"{available_area:.2f}",
            "Status": "OK" if ok else "NOT OK",
            "Shortfall (m²)": f"{shortfall:.2f}" if not ok else "0.00",
        })

    # ---- Summary + CSV ----
    st.markdown("### Summary")
    df = pd.DataFrame(results)
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.markdown("---")
    csv_buf = StringIO()
    df.to_csv(csv_buf, index=False)
    st.download_button(
        "⬇️ Download CSV",
        data=csv_buf.getvalue(),
        file_name="area_per_capita_summary.csv",
        mime="text/csv",
    )

# ---------- Standalone mode ----------
def _standalone():
    st.set_page_config(page_title="📏 Area per Capita (Standalone)", layout="centered")
    st.title("📏 Area per Capita Check — Standalone")

    if ifcopenshell is None:
        st.error("Package **ifcopenshell** is not installed. `pip install ifcopenshell`")
        st.stop()

    with st.sidebar:
        st.header("IFC")
        up = st.file_uploader("Upload IFC (.ifc / .ifczip)", type=["ifc", "ifczip"])

    if up is None:
        st.info("Upload an IFC file in the sidebar to begin.")
        st.stop()

    suffix = ".ifczip" if up.name.lower().endswith(".ifczip") else ".ifc"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(up.read())
        tmp_path = tmp.name

    try:
        ifc = ifcopenshell.open(tmp_path)
    except Exception as e:
        st.error(f"IFC open error: {e}")
        st.stop()

    run_area_per_capita_check(ifc)

if __name__ == "__main__":
    _standalone()
