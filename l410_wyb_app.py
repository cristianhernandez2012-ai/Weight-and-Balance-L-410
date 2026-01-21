"""L-410 Weight & Balance (WYB) — Streamlit app

This script is a direct Python conversion of the provided Excel template
"Copia de WYB LET.xlsx" (aircraft HK 4895), focusing on the math and checks.

Visual UI (Streamlit):
  pip install streamlit matplotlib
  streamlit run l410_wyb_app.py -- --streamlit

Visual UI (launch via Python):
  python l410_wyb_app.py --visual

CLI (for VS Code / scripts):
  python l410_wyb_app.py --cli --use-defaults

You can also import and call `compute(inputs)` for programmatic use.

Notes
- Units: mass in kg, arms in m, moments in kg*m.
- Limits and constants come from the spreadsheet:
  * Max Payload: 1710
  * MZFW: 5840
  * Ramp max: 6420
  * TOW max: 6400
  * Landing max: 6200
- Fuel arms per sheet:
  * Wing tip tank moment factor: 2.674
  * Main tanks moment factor: 3.134
  * Taxi fuel uses main tank arm (3.134)
  * Basic fuel is subtracted from takeoff to landing (arm 3.134)
- Envelope polygon points come from sheet "MATRIZ".
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

# -------- Constants (from the spreadsheet) --------

STATIONS: List[Tuple[str, float]] = [
    ("Forward Baggage", -1.333),
    ("Crew Seat", 0.103),
    ("Seat Row 1", 1.235),
    ("Seat Row 2", 1.995),
    ("Seat Row 3", 2.755),
    ("Seat Row 4", 3.515),
    ("Seat Row 5", 4.275),
    ("Seat Row 6", 5.035),
    ("Seat Row 7", 5.785),
    ("Aft Baggage", 6.65),
]

LIMITS = {
    "MAX_PAYLOAD": 1710.0,
    "MZFW": 5840.0,
    "RAMP_MAX": 6420.0,
    "TOW_MAX": 6400.0,
    "LAND_MAX": 6200.0,
}

# Default empty weight from spreadsheet (cell D23)
DEFAULT_EMPTY_WEIGHT = 4021.096
# Default empty weight CG arm computed by sheet (E23 = D23*2.68605)
DEFAULT_EMPTY_ARM = 2.68605

FUEL_ARMS = {
    "WING_TIP": 2.674,
    "MAIN": 3.134,
}

# Envelope polygon (arm, weight) from sheet MATRIZ (B4:C9)
ENVELOPE_POLYGON = [
    (2.517, 4000),
    (2.517, 4500),
    (2.65, 6200),
    (2.67, 6400),
    (2.728, 6400),
    (2.728, 4000),
]


@dataclass
class Inputs:
    payload: Dict[str, float]
    empty_weight: float = DEFAULT_EMPTY_WEIGHT
    empty_arm: float = DEFAULT_EMPTY_ARM

    wing_tip_fuel: float = 0.0
    main_fuel: float = 700.0

    taxi_fuel: float = -20.0  # negative (burned during taxi)
    basic_fuel: float = 600.0  # consumed from takeoff to landing


def _moment(weight: float, arm: float) -> float:
    return weight * arm


def _status(over: bool) -> str:
    return "WARNING" if over else "OK"


def compute(inputs: Inputs) -> Dict[str, Any]:
    # --- Payload station moments ---
    station_rows = []
    payload_total = 0.0
    payload_moment_total = 0.0

    for name, arm in STATIONS:
        w = float(inputs.payload.get(name, 0.0) or 0.0)
        m = _moment(w, arm)
        station_rows.append({"station": name, "arm_m": arm, "weight_kg": w, "moment_kgm": m})
        payload_total += w
        payload_moment_total += m

    payload_over = payload_total > LIMITS["MAX_PAYLOAD"]

    # --- Empty weight ---
    ew = float(inputs.empty_weight)
    ea = float(inputs.empty_arm)
    ew_moment = _moment(ew, ea)

    # --- ZFW ---
    zfw = ew + payload_total
    zfw_moment = ew_moment + payload_moment_total
    zfw_over = zfw > LIMITS["MZFW"]

    # --- Max allowable fuel (from ramp max in sheet: 6420 - ZFW) ---
    max_allowable_fuel = LIMITS["RAMP_MAX"] - zfw

    # --- Fuel ---
    wing_tip = float(inputs.wing_tip_fuel)
    main = float(inputs.main_fuel)
    fuel_total = wing_tip + main
    fuel_moment = _moment(wing_tip, FUEL_ARMS["WING_TIP"]) + _moment(main, FUEL_ARMS["MAIN"])

    # --- Ramp ---
    ramp_w = zfw + fuel_total
    ramp_m = zfw_moment + fuel_moment
    ramp_over = ramp_w > LIMITS["RAMP_MAX"]

    # --- Taxi (negative) ---
    taxi = float(inputs.taxi_fuel)
    taxi_moment = _moment(taxi, FUEL_ARMS["MAIN"])

    # --- Takeoff ---
    tow_w = ramp_w + taxi
    tow_m = ramp_m + taxi_moment
    tow_over = tow_w > LIMITS["TOW_MAX"]

    # --- Basic fuel (consumed from takeoff to landing)
    basic = float(inputs.basic_fuel)
    # Sheet uses negative moment for basic fuel row: E39 = D39 * -3.134
    basic_moment = _moment(basic, -FUEL_ARMS["MAIN"])

    # --- Landing ---
    land_w = tow_w - basic
    land_m = tow_m + basic_moment
    land_over = land_w > LIMITS["LAND_MAX"]

    # --- CGs ---
    tow_cg = tow_m / tow_w if tow_w else float("nan")
    land_cg = land_m / land_w if land_w else float("nan")

    # Envelope helper: point in polygon (x=arm, y=weight)
    def point_in_poly(x: float, y: float, poly: List[Tuple[float, float]]) -> bool:
        inside = False
        n = len(poly)
        for i in range(n):
            x1, y1 = poly[i]
            x2, y2 = poly[(i + 1) % n]
            # ray casting
            if ((y1 > y) != (y2 > y)) and (x < (x2 - x1) * (y - y1) / (y2 - y1 + 1e-12) + x1):
                inside = not inside
        return inside

    tow_in_env = point_in_poly(tow_cg, tow_w, ENVELOPE_POLYGON)
    land_in_env = point_in_poly(land_cg, land_w, ENVELOPE_POLYGON)

    return {
        "station_rows": station_rows,
        "payload_total": payload_total,
        "payload_moment_total": payload_moment_total,
        "payload_status": _status(payload_over),
        "payload_message": "PAYLOAD.OVERWEIGHT" if payload_over else "OK",

        "empty_weight": ew,
        "empty_arm": ea,
        "empty_moment": ew_moment,

        "zfw": zfw,
        "zfw_moment": zfw_moment,
        "zfw_cg": zfw_moment / zfw if zfw else float("nan"),
        "zfw_status": _status(zfw_over),
        "zfw_message": "M.Z.F.W.OVERWEIGHT" if zfw_over else "OK",

        "max_allowable_fuel": max_allowable_fuel,

        "fuel": {
            "wing_tip": wing_tip,
            "main": main,
            "total": fuel_total,
            "moment": fuel_moment,
        },

        "ramp": {
            "weight": ramp_w,
            "moment": ramp_m,
            "cg": ramp_m / ramp_w if ramp_w else float("nan"),
            "status": _status(ramp_over),
            "message": "R.W.OVERWEIGHT" if ramp_over else "OK",
        },

        "taxi": {"fuel": taxi, "moment": taxi_moment},

        "takeoff": {
            "weight": tow_w,
            "moment": tow_m,
            "cg": tow_cg,
            "status": _status(tow_over),
            "message": "T.O.W.OVERWEIGHT" if tow_over else "OK",
            "in_envelope": tow_in_env,
        },

        "basic_fuel": {"fuel": basic, "moment": basic_moment},

        "landing": {
            "weight": land_w,
            "moment": land_m,
            "cg": land_cg,
            "status": _status(land_over),
            "message": "L.W.OVERWEIGHT" if land_over else "OK",
            "in_envelope": land_in_env,
        },

        "envelope_polygon": ENVELOPE_POLYGON,
    }


# ---------------- Streamlit UI ----------------

def _run_streamlit() -> None:
    import streamlit as st
    import pandas as pd
    import matplotlib.pyplot as plt

    st.set_page_config(page_title="L-410 Weight & Balance", layout="wide")
    st.title("L-410 Weight & Balance (WYB)")
    st.caption("Basado en la plantilla Excel: Copia de WYB LET.xlsx (HK 4895)")

    with st.sidebar:
        st.header("Configuración")
        empty_weight = st.number_input("Empty Weight (kg)", value=float(DEFAULT_EMPTY_WEIGHT), step=1.0)
        empty_arm = st.number_input("Empty CG Arm (m)", value=float(DEFAULT_EMPTY_ARM), step=0.001, format="%.5f")

        st.subheader("Fuel")
        wing_tip_fuel = st.number_input("Wing Tip Tank (kg)", value=0.0, step=10.0)
        main_fuel = st.number_input("Main Tanks (kg)", value=700.0, step=10.0)
        taxi_fuel = st.number_input("Taxi fuel (kg) (negative)", value=-20.0, step=1.0)
        basic_fuel = st.number_input("Basic fuel (kg) (trip burn)", value=600.0, step=10.0)

        st.subheader("Límites (del Excel)")
        st.write(LIMITS)

    st.subheader("Payload")
    cols = st.columns(2)

    # Defaults mirror the values shown in the spreadsheet rows D11:D20
    default_payload = {
        "Forward Baggage": 100.0,
        "Crew Seat": 160.0,
        "Seat Row 1": 240.0,
        "Seat Row 2": 230.0,
        "Seat Row 3": 210.0,
        "Seat Row 4": 210.0,
        "Seat Row 5": 210.0,
        "Seat Row 6": 140.0,
        "Seat Row 7": 140.0,
        "Aft Baggage": 0.0,
    }

    payload_values: Dict[str, float] = {}
    for i, (name, arm) in enumerate(STATIONS):
        col = cols[i % 2]
        payload_values[name] = col.number_input(
            f"{name} (arm {arm} m)",
            value=float(default_payload.get(name, 0.0)),
            step=10.0,
        )

    inputs = Inputs(
        payload=payload_values,
        empty_weight=empty_weight,
        empty_arm=empty_arm,
        wing_tip_fuel=wing_tip_fuel,
        main_fuel=main_fuel,
        taxi_fuel=taxi_fuel,
        basic_fuel=basic_fuel,
    )

    results = compute(inputs)

    # --- Summary boxes ---
    def badge(ok: bool) -> str:
        return "✅" if ok else "⚠️"

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Payload (kg)", f"{results['payload_total']:.1f}", f"{results['payload_message']}")
    c2.metric("ZFW (kg)", f"{results['zfw']:.1f}", f"{results['zfw_message']}")
    c3.metric("Ramp (kg)", f"{results['ramp']['weight']:.1f}", f"{results['ramp']['message']}")
    c4.metric("Takeoff (kg)", f"{results['takeoff']['weight']:.1f}", f"{results['takeoff']['message']}")
    c5.metric("Landing (kg)", f"{results['landing']['weight']:.1f}", f"{results['landing']['message']}")

    st.info(
        f"Maximum allowable fuel (kg): {results['max_allowable_fuel']:.1f}  |  "
        f"Takeoff CG: {results['takeoff']['cg']:.3f} m {badge(results['takeoff']['in_envelope'])}  |  "
        f"Landing CG: {results['landing']['cg']:.3f} m {badge(results['landing']['in_envelope'])}"
    )

    # --- Tables ---
    left, right = st.columns([1.2, 1])

    with left:
        st.markdown("#### Detalle Payload")
        df = pd.DataFrame(results["station_rows"])
        df["moment_kgm"] = df["moment_kgm"].round(2)
        df["weight_kg"] = df["weight_kg"].round(1)
        st.dataframe(df, use_container_width=True)

    with right:
        st.markdown("#### Resumen")
        summary_rows = [
            ("Empty Weight", results["empty_weight"], results["empty_moment"], results["empty_arm"]),
            (
                "Payload Total",
                results["payload_total"],
                results["payload_moment_total"],
                results["payload_moment_total"] / results["payload_total"] if results["payload_total"] else float('nan'),
            ),
            ("ZFW", results["zfw"], results["zfw_moment"], results["zfw_cg"]),
            (
                "Fuel Total",
                results["fuel"]["total"],
                results["fuel"]["moment"],
                results["fuel"]["moment"] / results["fuel"]["total"] if results["fuel"]["total"] else float('nan'),
            ),
            ("Ramp", results["ramp"]["weight"], results["ramp"]["moment"], results["ramp"]["cg"]),
            (
                "Taxi Fuel",
                results["taxi"]["fuel"],
                results["taxi"]["moment"],
                results["taxi"]["moment"] / results["taxi"]["fuel"] if results["taxi"]["fuel"] else float('nan'),
            ),
            ("Takeoff", results["takeoff"]["weight"], results["takeoff"]["moment"], results["takeoff"]["cg"]),
            ("Basic Fuel Burn", results["basic_fuel"]["fuel"], results["basic_fuel"]["moment"], float('nan')),
            ("Landing", results["landing"]["weight"], results["landing"]["moment"], results["landing"]["cg"]),
        ]
        sdf = pd.DataFrame(summary_rows, columns=["Item", "Weight (kg)", "Moment (kg·m)", "CG Arm (m)"])
        sdf["Weight (kg)"] = sdf["Weight (kg)"].round(1)
        sdf["Moment (kg·m)"] = sdf["Moment (kg·m)"].round(2)
        sdf["CG Arm (m)"] = sdf["CG Arm (m)"].round(3)
        st.dataframe(sdf, use_container_width=True)

    # --- Envelope plot ---
    st.markdown("#### CG Envelope")

    fig = plt.figure()
    poly = results["envelope_polygon"]
    xs = [p[0] for p in poly] + [poly[0][0]]
    ys = [p[1] for p in poly] + [poly[0][1]]

    plt.plot(xs, ys)
    plt.scatter([results["takeoff"]["cg"]], [results["takeoff"]["weight"]], label="Takeoff")
    plt.scatter([results["landing"]["cg"]], [results["landing"]["weight"]], label="Landing")
    plt.xlabel("CG Arm (m)")
    plt.ylabel("Weight (kg)")
    plt.legend()
    plt.grid(True)
    st.pyplot(fig, clear_figure=True)

    st.caption("⚠️ Este script replica la lógica del Excel. Verifica siempre con tus procedimientos/limitaciones operacionales.")


def _run_cli() -> None:
    parser = argparse.ArgumentParser(
        description="L-410 Weight & Balance calculator (CLI).",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--cli",
        action="store_true",
        help="Force CLI JSON output mode.",
    )
    mode_group.add_argument(
        "--visual",
        "--streamlit",
        dest="visual",
        action="store_true",
        help="Launch the Streamlit UI (alias: --streamlit).",
    )
    parser.add_argument(
        "--payload",
        type=str,
        default="",
        help="JSON object for payload by station name (e.g. '{\"Crew Seat\": 160}').",
    )
    parser.add_argument("--empty-weight", type=float, default=DEFAULT_EMPTY_WEIGHT)
    parser.add_argument("--empty-arm", type=float, default=DEFAULT_EMPTY_ARM)
    parser.add_argument("--wing-tip-fuel", type=float, default=0.0)
    parser.add_argument("--main-fuel", type=float, default=700.0)
    parser.add_argument("--taxi-fuel", type=float, default=-20.0)
    parser.add_argument("--basic-fuel", type=float, default=600.0)
    parser.add_argument(
        "--use-defaults",
        action="store_true",
        help="Use the spreadsheet default payload values.",
    )
    args = parser.parse_args()

    if args.visual:
        if "--streamlit" in sys.argv:
            _run_streamlit()
            return
        subprocess.run(
            ["streamlit", "run", __file__, "--", "--streamlit"],
            check=False,
        )
        return

    if args.use_defaults:
        payload_values = {
            "Forward Baggage": 100,
            "Crew Seat": 160,
            "Seat Row 1": 240,
            "Seat Row 2": 230,
            "Seat Row 3": 210,
            "Seat Row 4": 210,
            "Seat Row 5": 210,
            "Seat Row 6": 140,
            "Seat Row 7": 140,
            "Aft Baggage": 0,
        }
    elif args.payload:
        payload_values = json.loads(args.payload)
    else:
        payload_values = {name: 0.0 for name, _ in STATIONS}

    inputs = Inputs(
        payload=payload_values,
        empty_weight=args.empty_weight,
        empty_arm=args.empty_arm,
        wing_tip_fuel=args.wing_tip_fuel,
        main_fuel=args.main_fuel,
        taxi_fuel=args.taxi_fuel,
        basic_fuel=args.basic_fuel,
    )
    res = compute(inputs)
    print(json.dumps(res, indent=2, sort_keys=True))


if __name__ == "__main__":
    # If run via `python l410_wyb_app.py`, default to CLI unless --visual/--streamlit is passed.
    _run_cli()
