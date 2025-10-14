# app.py
# Streamlit web UI for Classroom & Laboratory Capacity Checker (IFC + Shapely)
# - IFC upload required (no auto)
# - Sidebar: Student number input
# - Tabs: Classroom & Laboratory
# - Caption line for code above each tab’s report
# - CSV download only
# - Report (GOOD/BAD lists) BEFORE the table, like before
# - No status/progress box under the title

import os
import tempfile
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st

# IFC + geometry
import ifcopenshell
import ifcopenshell.geom as ifcgeom
from shapely.geometry import Polygon, Point
from shapely.ops import unary_union

# -------------------------
# Config
# -------------------------
DEFAULT_MAX_CAPACITY = 24
DEFAULT_Z_TOL_M = 1.0  # fixed (hidden)

STANDARD_CHAIR_NAMES = {
    "student chair": "student chair",
    "laboratory chair": "laboratory chair",
    "drinking tap": "drinking tap",
}

ROOM_TYPES = ["classroom", "laboratory"]
CHAIR_FOR_ROOM = {
    "classroom": STANDARD_CHAIR_NAMES["student chair"],
    "laboratory": STANDARD_CHAIR_NAMES["laboratory chair"],
}

# -------------------------
# Data models
# -------------------------
@dataclass
class RoomRec:
    ifc_id: int
    longname: str
    name: str
    zmin: float
    zmax: float
    elev: float
    footprint: Polygon
    chairs: List[int]

@dataclass
class ChairRec:
    ifc_id: int
    name: Optional[str]
    family: Optional[str]
    type_name: Optional[str]
    x: float
    y: float
    z: float

# -------------------------
# Helpers
# -------------------------
def _norm(s: Optional[str]) -> str:
    return (s or "").strip()

def _lower(s: Optional[str]) -> str:
    return _norm(s).lower()

def get_element_world_point(el) -> Optional[Tuple[float, float, float]]:
    try:
        pl = el.ObjectPlacement
        if not pl:
            return None

        def placement_to_matrix(placement):
            loc = placement.RelativePlacement.Location
            ox, oy, oz = (loc.Coordinates[0], loc.Coordinates[1], loc.Coordinates[2])

            def vec3(v, default):
                return (
                    float(v.DirectionRatios[0]),
                    float(v.DirectionRatios[1]),
                    float(v.DirectionRatios[2]),
                ) if v else default

            ref = getattr(placement.RelativePlacement, "RefDirection", None)
            axis = getattr(placement.RelativePlacement, "Axis", None)
            zaxis = vec3(axis, (0.0, 0.0, 1.0))
            xaxis = vec3(ref,  (1.0, 0.0, 0.0))
            yaxis = (
                zaxis[1]*xaxis[2] - zaxis[2]*xaxis[1],
                zaxis[2]*xaxis[0] - zaxis[0]*xaxis[2],
                zaxis[0]*xaxis[1] - zaxis[1]*xaxis[0],
            )
            return (
                (xaxis[0], yaxis[0], zaxis[0], ox),
                (xaxis[1], yaxis[1], zaxis[1], oy),
                (xaxis[2], yaxis[2], zaxis[2], oz),
                (0.0, 0.0, 0.0, 1.0),
            )

        mats = []
        cur = pl
        while cur:
            mats.append(placement_to_matrix(cur))
            cur = getattr(cur, "PlacementRelTo", None)

        def mm(a, b):
            out = [[0.0]*4 for _ in range(4)]
            for i in range(4):
                for j in range(4):
                    out[i][j] = sum(a[i][k]*b[k][j] for k in range(4))
            return tuple(tuple(row) for row in out)

        M = mats[-1]
        for i in range(len(mats)-2, -1, -1):
            M = mm(M, mats[i])

        return float(M[0][3]), float(M[1][3]), float(M[2][3])
    except Exception:
        return None

def build_space_footprint(space, settings):
    try:
        shape = ifcgeom.create_shape(settings, space)
    except Exception:
        return None
    verts = list(shape.geometry.verts)
    if not verts:
        return None
    xs, ys, zs = verts[0::3], verts[1::3], verts[2::3]
    zmin, zmax, elev = min(zs), max(zs), min(zs)
    faces = list(shape.geometry.faces)
    polys = []
    for i in range(0, len(faces), 3):
        try:
            i1, i2, i3 = faces[i], faces[i+1], faces[i+2]
            tri = Polygon([(xs[i1], ys[i1]), (xs[i2], ys[i2]), (xs[i3], ys[i3])])
            if tri.is_valid and tri.area > 0:
                polys.append(tri)
        except Exception:
            continue
    if not polys:
        return None
    footprint = unary_union(polys)
    if footprint.is_empty:
        return None
    return footprint, float(zmin), float(zmax), float(elev)

def collect_rooms(ifc):
    spaces = list(ifc.by_type("IfcSpace") or [])
    if not spaces:
        return {}
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    rooms = {}
    for sp in spaces:
        built = build_space_footprint(sp, settings)
        if not built:
            continue
        fp, zmin, zmax, elev = built
        longname = _norm(getattr(sp, "LongName", None))
        name = _norm(getattr(sp, "Name", None)) or longname or f"Space_{sp.id()}"
        rooms[str(sp.id())] = RoomRec(sp.id(), longname, name, zmin, zmax, elev, fp, [])
    return rooms

def collect_chairs(ifc):
    elems = list(ifc.by_type("IfcFurnishingElement") or [])
    out = []
    for el in elems:
        pt = get_element_world_point(el)
        if not pt:
            continue
        tname = None
        try:
            if el.IsTypedBy:
                t = el.IsTypedBy[0].RelatingType
                tname = _norm(getattr(t, "Name", None) or getattr(t, "ElementType", None))
        except Exception:
            pass
        out.append(
            ChairRec(
                ifc_id=el.id(),
                name=_norm(getattr(el, "Name", None)),
                family=_norm(getattr(el, "ObjectType", None)),
                type_name=tname,
                x=pt[0], y=pt[1], z=pt[2],
            )
        )
    return out

def chair_matches(ch: ChairRec, query: str):
    q = _lower(query)
    return any(q in _lower(c) for c in [ch.name, ch.family, ch.type_name] if c)

def assign_chairs_to_rooms(rooms: Dict[str, RoomRec], chairs: List[ChairRec], z_tol_m: float):
    for r in rooms.values():
        r.chairs = []
    for ch in chairs:
        p = Point(ch.x, ch.y)
        for r in rooms.values():
            if abs(ch.z - r.elev) > z_tol_m:
                continue
            if r.footprint.contains(p) or r.footprint.touches(p):
                r.chairs.append(ch.ifc_id)
                break

def detect_room_type(room: RoomRec):
    ln, nm = _lower(room.longname), _lower(room.name)
    if ln == "classroom" or nm == "classroom":
        return "classroom"
    if ln == "laboratory" or nm == "laboratory":
        return "laboratory"
    if ln == "room" or nm == "room":
        return "room"  # used for lab fallback
    return None

# -------------------------
# UI
# -------------------------
st.set_page_config(page_title="Classroom & Laboratory Capacity Checker", layout="wide")
st.title("Classroom & Laboratory Capacity Checker")

# Sidebar upload and student number
with st.sidebar:
    st.header("IFC File")
    up = st.file_uploader("Upload IFC (.ifc / .ifczip)", type=["ifc", "ifczip"])
    st.header("Parameters")
    max_cap = st.number_input(
        "Student number (max capacity per room)",
        min_value=1, max_value=10000, value=DEFAULT_MAX_CAPACITY, step=1,
        help="Urban classrooms: 24 (max 30). Rural classrooms: 18. Labs/workshops — Urban: 24, Rural: 18."
    )

if up is None:
    st.info("Upload an IFC file in the sidebar to begin.")
    st.stop()

# Load IFC directly (no status/progress UI)
with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(up.name)[1]) as tmp:
    tmp.write(up.read())
    path = tmp.name

try:
    ifc = ifcopenshell.open(path)
except Exception as e:
    st.error(f"IFC open error for `{up.name}`: {e}")
    st.stop()

rooms = collect_rooms(ifc)
chairs = collect_chairs(ifc)

# Separate rooms by type (with fallback for Laboratory)
classroom_rooms = {k: r for k, r in rooms.items() if detect_room_type(r) == "classroom"}
laboratory_rooms = {k: r for k, r in rooms.items() if detect_room_type(r) == "laboratory"}
if not laboratory_rooms:
    laboratory_rooms = {k: r for k, r in rooms.items() if detect_room_type(r) == "room"}

# Separate chairs by category
classroom_chairs = [c for c in chairs if chair_matches(c, CHAIR_FOR_ROOM["classroom"])]
laboratory_chairs = [c for c in chairs if chair_matches(c, CHAIR_FOR_ROOM["laboratory"])]

# Assign chairs to rooms
assign_chairs_to_rooms(classroom_rooms, classroom_chairs, z_tol_m=DEFAULT_Z_TOL_M)
assign_chairs_to_rooms(laboratory_rooms, laboratory_chairs, z_tol_m=DEFAULT_Z_TOL_M)

# -------------------------
# Results rendering
# -------------------------
def render_results(title, rooms_filtered, max_cap, out_csv_name, code_text):
    # Simple caption for your code line
    st.caption(f"Code: {code_text}")

    st.subheader(title)

    if not rooms_filtered:
        st.info("No matching rooms found.")
        return

    results = []
    good, bad = [], []
    ordered = sorted(rooms_filtered.values(), key=lambda r: (r.longname or r.name, r.ifc_id))
    for r in ordered:
        cnt = len(r.chairs)

        # room_number from Name if purely numeric
        room_number = None
        if r.name.isdigit():
            try:
                room_number = int(r.name)
            except Exception:
                room_number = None

        status_ok = (cnt <= max_cap)
        rec = {
            "room_name": r.longname or r.name,
            "room_number": room_number,
            "chair_count": cnt,
            "status": "OK" if status_ok else "NOT OK",
        }
        results.append(rec)
        (good if status_ok else bad).append(rec)

    # ---------- Summary ----------
    gcount, bcount = len(good), len(bad)
    st.markdown(f"**✅ GOOD rooms:** {gcount} &nbsp;&nbsp; | &nbsp;&nbsp; **❌ BAD rooms:** {bcount}")

    # Detailed lists BEFORE the table (restored)
    def line_fmt(i, rec, ok: bool) -> str:
        rn = rec["room_name"]
        num = f" {rec['room_number']}" if rec["room_number"] is not None else ""
        if ok:
            return f"{i}. {rn}{num} — {rec['chair_count']} chair(s) — OK (≤ {max_cap})"
        else:
            return f"{i}. {rn}{num} — {rec['chair_count']} chair(s) — NOT OK  Over (≥ {max_cap})"

    colA, colB = st.columns(2)
    with colA:
        st.markdown(f"### ✅ GOOD ({gcount})")
        if gcount:
            lines = [line_fmt(i, r, True) for i, r in enumerate(good, 1)]
            st.write("\n".join(lines))
        else:
            st.write("_None_")
    with colB:
        st.markdown(f"### ❌ BAD ({bcount})")
        if bcount:
            lines = [line_fmt(i, r, False) for i, r in enumerate(bad, 1)]
            st.write("\n".join(lines))
        else:
            st.write("_None_")

    # ---------- Table ----------
    df = pd.DataFrame(results, columns=["room_name", "room_number", "chair_count", "status"])
    st.markdown("### Table")
    styled = (
        df.style
          .set_properties(**{"text-align": "center"})
          .set_table_styles([{"selector": "th", "props": [("text-align", "center")]}])
    )
    st.table(styled)

    # ---------- Download (CSV only) ----------
    csv_bytes = df.to_csv(index=False).encode("utf-8")
    st.download_button("⬇️ Download CSV", data=csv_bytes, file_name=out_csv_name, mime="text/csv")

# -------------------------
# Tabs
# -------------------------
from praying_room_checker import render_praying_room_area_check  # <— add this import at top
from wc_number_checker import render_wc_number_check
from staff_wc_checker import render_staff_wc_check
from disabled_wc_checker import render_disabled_wc_check
from meeting_room_checker import render_meeting_room_seats_check
from area_per_capita_app import run_area_per_capita_app

# Tabs section
tabs = st.tabs(["Classroom", "Laboratory", "Praying Room", "Meeting Room", "WC Check", "Staff WC", "Disabled WC", "Area per Capita"])

with tabs[0]:
    render_results(
        "Classroom Capacity Check",
        classroom_rooms,
        max_cap,
        "classroom_capacity_report.csv",
        "2-1-1",
    )

with tabs[1]:
    render_results(
        "Laboratory Capacity Check",
        laboratory_rooms,
        max_cap,
        "laboratory_capacity_report.csv",
        "2-1-2",
    )

with tabs[2]:
    render_praying_room_area_check(ifc)

with tabs[3]:
    render_meeting_room_seats_check(ifc)

with tabs[4]:
    render_wc_number_check(ifc)

with tabs[5]:
    render_staff_wc_check(ifc)    

with tabs[6]:
    render_disabled_wc_check(ifc)

with tabs[7]:  
    run_area_per_capita_app()