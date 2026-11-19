import json
import math
from decimal import Decimal, ROUND_HALF_UP
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# ---------------------------------------------------------
# 1. PAGE SETUP & EXACT MONEY HELPER
# ---------------------------------------------------------
st.set_page_config(
    page_title="ROB-POWER | Robotics Kit & Repair Studio",
    layout="wide",
)

def money(val) -> float:
    """Round cleanly to 2 decimal places using Decimal so cents never drift."""
    return float(Decimal(str(val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


# ---------------------------------------------------------
# 2. DEFAULT STARTING STATE (ROB-2 BASELINE)
# ---------------------------------------------------------
def get_default_state() -> dict:
    return {
        "team_id": "ROB-POWER",
        "pathway_id": "ROB-2",
        "selected_course": "Precalculus",
        "selected_level": "Foundation",
        "state_version": 1,
        "simulation_date": "2027-01-31",
        "random_seed": 2027,
        # Startup balances
        "owner_contribution": 12000.00,
        "equipment_assets": 4000.00,
        "pre_opening_expense": 0.00,
        "opening_cash": 5000.00,
        "current_cash": 5000.00,
        "opening_stock_units": 100,
        "current_stock_units": 100,
        "component_unit_cost": 30.00,
        "debt_balance": 0.00,
        # Operations & Sales defaults
        "base_price": 120.00,
        "D0": 40,
        "staff_hours": 90.0,
        "hours_per_unit": 3.0,
        "defect_rate": 0.0,
        # Variable costs per attempted unit ($65 total)
        "var_components": 30.00,
        "var_assembly_labor": 20.00,
        "var_packaging": 10.00,
        "var_warranty": 5.00,
        # Monthly fixed costs ($600 total)
        "fix_workspace": 250.00,
        "fix_software": 100.00,
        "fix_maintenance": 150.00,
        "fix_admin": 100.00,
        # Records
        "ledger": [],
        "processed_run_ids": [],
    }


if "sim_state" not in st.session_state:
    st.session_state.sim_state = get_default_state()

state = st.session_state.sim_state


# ---------------------------------------------------------
# 3. DETERMINISTIC ROBOTICS ENGINE (CORE ALGORITHM)
# ---------------------------------------------------------
def simulate_month(
    current_state: dict,
    sim_date: str,
    run_id: str,
    requested: int,
    restock_units: int = 0,
    ad_cost: float = 0.0,
    loan_cash: float = 0.0,
    loan_principal_repaid: float = 0.0,
    interest_expense: float = 0.0,
    additional_capex: float = 0.0,
    refund_amount: float = 0.0,
    capacity_override: int | None = None,
) -> tuple[bool, str, dict]:
    """
    Runs one monthly simulation step using the exact ROB package rules.
    Returns (success, message, updated_state).
    """
    if not run_id or not run_id.strip():
        return False, "Missing Run ID. Every run needs a unique ID.", current_state

    if run_id in current_state["processed_run_ids"]:
        return (
            False,
            f"Duplicate Run ID '{run_id}' rejected! This run was already recorded.",
            current_state,
        )

    # 1. Capacity check (Normal mode vs. Fixture-Test override)
    if capacity_override is not None:
        capacity = int(capacity_override)
    else:
        capacity = math.floor(
            current_state["staff_hours"] / current_state["hours_per_unit"]
        )

    # 2. Available inventory & attempted units
    available_stock = current_state["current_stock_units"] + int(restock_units)
    attempted = min(int(requested), capacity, available_stock)
    failed = math.floor(attempted * current_state["defect_rate"])
    sold = attempted - failed
    unserved_demand = max(0, int(requested) - attempted)

    # 3. Revenue & Expenses
    price = current_state["base_price"]
    revenue = money(price * sold - refund_amount)

    # Robotics rule: variable expense is incurred on ALL attempted kits
    comp_cost_per_unit = current_state["var_components"]
    non_comp_var_per_unit = (
        current_state["var_assembly_labor"]
        + current_state["var_packaging"]
        + current_state["var_warranty"]
    )
    total_var_per_unit = comp_cost_per_unit + non_comp_var_per_unit

    variable_expense = money(attempted * total_var_per_unit)
    fixed_expense = money(
        current_state["fix_workspace"]
        + current_state["fix_software"]
        + current_state["fix_maintenance"]
        + current_state["fix_admin"]
    )
    total_operating_expense = money(variable_expense + fixed_expense + ad_cost)
    operating_profit = money(revenue - total_operating_expense)
    net_profit = money(operating_profit - interest_expense)

    # 4. Cash Flow Rule (Components reduce cash on RESTOCK, not consumption)
    restock_cash_outflow = money(int(restock_units) * current_state["component_unit_cost"])
    non_comp_variable_cash_outflow = money(attempted * non_comp_var_per_unit)

    ending_cash = money(
        current_state["current_cash"]
        + revenue
        - restock_cash_outflow
        - non_comp_variable_cash_outflow
        - fixed_expense
        - ad_cost
        - interest_expense
        - loan_principal_repaid
        - additional_capex
        + loan_cash
    )

    ending_stock = available_stock - attempted
    new_version = current_state["state_version"] + 1

    # 5. Record in Ledger
    entry = {
        "run_id": run_id,
        "date": sim_date,
        "state_version": new_version,
        "mode": "Fixture Override" if capacity_override is not None else "Normal",
        "requested": int(requested),
        "capacity": capacity,
        "attempted": attempted,
        "failed": failed,
        "sold": sold,
        "unserved_demand": unserved_demand,
        "revenue": revenue,
        "variable_expense": variable_expense,
        "fixed_expense": fixed_expense,
        "ad_cost": money(ad_cost),
        "total_expense": total_operating_expense,
        "operating_profit": operating_profit,
        "net_profit": net_profit,
        "restock_units": int(restock_units),
        "restock_cash_out": restock_cash_outflow,
        "ending_stock": ending_stock,
        "ending_cash": ending_cash,
    }

    updated = json.loads(json.dumps(current_state))
    updated["current_cash"] = ending_cash
    updated["current_stock_units"] = ending_stock
    updated["debt_balance"] = money(
        updated["debt_balance"] + loan_cash - loan_principal_repaid
    )
    updated["state_version"] = new_version
    updated["simulation_date"] = sim_date
    updated["processed_run_ids"].append(run_id)
    updated["ledger"].append(entry)

    return True, f"Recorded {sim_date} (v{new_version}): Sold {sold} kits, Profit ${operating_profit:,.2f}.", updated


# ---------------------------------------------------------
# 4. SIDEBAR: STATUS, SAVE/LOAD JSON & RESET
# ---------------------------------------------------------
with st.sidebar:
    st.title("ROB-POWER Control")
    st.caption(f"Pathway: **{state['pathway_id']}** | Course: **{state['selected_course']}**")
    st.metric("Current Version", f"v{state['state_version']}")
    st.metric("Spendable Cash", f"${state['current_cash']:,.2f}")
    st.metric("Parts in Stock", f"{state['current_stock_units']} sets")

    if state["current_cash"] < 1000:
        st.warning("Low Cash Alert: Spendable cash is below $1,000!")

    st.divider()
    st.subheader("Save / Load Progress")
    snapshot_json = json.dumps(state, indent=2)
    st.download_button(
        label="Download Save File (JSON)",
        data=snapshot_json,
        file_name=f"{state['team_id']}_v{state['state_version']}.json",
        mime="application/json",
    )

    uploaded_file = st.file_uploader("Restore Save File (JSON)", type=["json"])
    if uploaded_file is not None:
        if st.button("Load Selected Save File"):
            try:
                loaded = json.load(uploaded_file)
                if loaded.get("team_id") != "ROB-POWER":
                    st.error(
                        f"Rejected: Wrong Team ID ('{loaded.get('team_id')}'). Expected 'ROB-POWER'."
                    )
                elif loaded.get("state_version", 0) < state["state_version"]:
                    st.error(
                        f"Rejected: Stale version (v{loaded.get('state_version')}). Current app is at v{state['state_version']}."
                    )
                else:
                    st.session_state.sim_state = loaded
                    st.success(f"Restored v{loaded['state_version']} successfully!")
                    st.rerun()
            except Exception as exc:
                st.error(f"Invalid JSON file: {exc}")

    st.divider()
    if st.button("Reset Business to Day 1"):
        st.session_state.sim_state = get_default_state()
        st.rerun()


# ---------------------------------------------------------
# 5. MAIN NAVIGATION TABS (STAGE 1 ACTIVE, STAGES 2-4 MARKED)
# ---------------------------------------------------------
tabs = st.tabs([
    "1. Overview & Ledger",
    "2. Inputs & Run Month",
    "3. Precalculus Math Lab",
    "4. Fixture Tests (Jan-Mar)",
    "5. Stages 2-4 (Coming Later)",
])


# =========================================================
# TAB 1: OVERVIEW & LEDGER
# =========================================================
with tabs[0]:
    st.header("Overview - Robotics Kit & Repair Studio")
    st.write(
        "Welcome, **ROB-POWER**! Use this dashboard to set your prices and work hours, "
        "explore your Precalculus graphs, and run monthly business rounds."
    )

    c1, c2, c3, c4 = st.columns(4)
    normal_cap = math.floor(state["staff_hours"] / state["hours_per_unit"])
    unit_var_cost = (
        state["var_components"]
        + state["var_assembly_labor"]
        + state["var_packaging"]
        + state["var_warranty"]
    )
    monthly_fixed_cost = (
        state["fix_workspace"]
        + state["fix_software"]
        + state["fix_maintenance"]
        + state["fix_admin"]
    )

    c1.metric("Kit Price (p0)", f"${state['base_price']:,.2f}")
    c2.metric("Monthly Build Limit", f"{normal_cap} kits")
    c3.metric("Cost per Attempted Kit (v)", f"${unit_var_cost:,.2f}")
    c4.metric("Monthly Fixed Bills (F)", f"${monthly_fixed_cost:,.2f}")

    st.subheader("Dated Transaction Ledger (Actual Simulated Runs)")
    if len(state["ledger"]) == 0:
        st.info("No months simulated yet. Go to **Tab 2 (Inputs & Run Month)** or **Tab 4 (Fixture Tests)** to run a month.")
    else:
        ledger_df = pd.DataFrame(state["ledger"])
        st.dataframe(ledger_df, use_container_width=True)
        st.download_button(
            "Export Ledger as CSV",
            data=ledger_df.to_csv(index=False),
            file_name="ROB-POWER_ledger.csv",
            mime="text/csv",
        )


# =========================================================
# TAB 2: INPUTS & RUN ONE MONTH
# =========================================================
with tabs[1]:
    st.header("Business Inputs & Monthly Simulation")
    st.caption("Edit your baseline parameters below, then record a simulated month into the ledger.")

    col_left, col_right = st.columns(2)
    with col_left:
        st.subheader("Pricing, Staffing & Inventory")
        state["base_price"] = st.number_input("Price per Kit ($)", min_value=1.0, value=float(state["base_price"]), step=5.0)
        state["D0"] = st.number_input("Baseline Monthly Demand D0 (kits)", min_value=0, value=int(state["D0"]), step=1)
        state["staff_hours"] = st.number_input("Staff Build Hours / Month", min_value=0.0, value=float(state["staff_hours"]), step=3.0)
        state["hours_per_unit"] = st.number_input("Hours Needed per Kit", min_value=0.5, value=float(state["hours_per_unit"]), step=0.5)
        state["defect_rate"] = st.slider("Defect Rate (Broken Kits)", min_value=0.0, max_value=1.0, value=float(state["defect_rate"]), step=0.05)

    with col_right:
        st.subheader("Editable Cost Lines")
        state["var_components"] = st.number_input("Variable: Components ($/kit)", min_value=0.0, value=float(state["var_components"]), step=1.0)
        state["var_assembly_labor"] = st.number_input("Variable: Assembly Labor ($/kit)", min_value=0.0, value=float(state["var_assembly_labor"]), step=1.0)
        state["var_packaging"] = st.number_input("Variable: Packaging ($/kit)", min_value=0.0, value=float(state["var_packaging"]), step=1.0)
        state["var_warranty"] = st.number_input("Variable: Warranty Supplies ($/kit)", min_value=0.0, value=float(state["var_warranty"]), step=1.0)

        state["fix_workspace"] = st.number_input("Fixed: Workspace ($/mo)", min_value=0.0, value=float(state["fix_workspace"]), step=10.0)
        state["fix_software"] = st.number_input("Fixed: Software ($/mo)", min_value=0.0, value=float(state["fix_software"]), step=10.0)
        state["fix_maintenance"] = st.number_input("Fixed: Equipment Maintenance ($/mo)", min_value=0.0, value=float(state["fix_maintenance"]), step=10.0)
        state["fix_admin"] = st.number_input("Fixed: Admin ($/mo)", min_value=0.0, value=float(state["fix_admin"]), step=10.0)

    st.divider()
    st.subheader("Record a New Simulated Month")
    r1, r2, r3, r4 = st.columns(4)
    sim_date_in = r1.text_input("Simulation Date (YYYY-MM-DD)", value="2027-01-31")
    run_id_in = r2.text_input("Unique Run ID", value=f"RUN-{state['state_version']:03d}")
    req_in = r3.number_input("Customer Requests (kits)", min_value=0, value=int(state["D0"]), step=1)
    restock_in = r4.number_input("Buy Restock Parts (sets)", min_value=0, value=0, step=5)

    r5, r6, r7 = st.columns(3)
    ad_in = r5.number_input("Own Ad Spend ($)", min_value=0.0, value=0.0, step=25.0)
    loan_in = r6.number_input("New Loan Received ($)", min_value=0.0, value=0.0, step=100.0)
    capex_in = r7.number_input("New Equipment Purchase ($)", min_value=0.0, value=0.0, step=100.0)

    if st.button("Approve & Run Month"):
        ok, msg, updated = simulate_month(
            state,
            sim_date=sim_date_in,
            run_id=run_id_in,
            requested=int(req_in),
            restock_units=int(restock_in),
            ad_cost=float(ad_in),
            loan_cash=float(loan_in),
            additional_capex=float(capex_in),
        )
        if ok:
            st.session_state.sim_state = updated
            st.success(msg)
            st.rerun()
        else:
            st.error(msg)


# =========================================================
# TAB 3: PRECALCULUS MATH LAB
# =========================================================
with tabs[2]:
    st.header("Precalculus Math Skills & Challenge Level Panel")

    level = st.radio(
        "Select Teacher-Approved Challenge Level:",
        ["Foundation", "Applied", "Advanced"],
        index=["Foundation", "Applied", "Advanced"].index(state["selected_level"]),
        horizontal=True,
    )
    state["selected_level"] = level

    cap = max(1, math.floor(state["staff_hours"] / state["hours_per_unit"]))
    p0 = state["base_price"]
    D0 = state["D0"]
    v = (
        state["var_components"]
        + state["var_assembly_labor"]
        + state["var_packaging"]
        + state["var_warranty"]
    )
    F = (
        state["fix_workspace"]
        + state["fix_software"]
        + state["fix_maintenance"]
        + state["fix_admin"]
    )

    # ---------------- FOUNDATION: AVERAGE COST ----------------
    if level == "Foundation":
        st.subheader("Foundation Skill: Rational Average Cost Function")
        st.latex(r"A(s) = \frac{F}{s} + v = \frac{" + f"{F:.0f}" + r"}{s} + " + f"{v:.0f}")
        st.markdown(
            f"- **Business Question:** How does the average cost per robot kit change as we build more kits up to our capacity limit?\n"
            f"- **Variables & Units:** $s$ = kits built ($0 < s \\le {cap}$ kits); $A(s)$ = average cost ($/kit); $F = \\${F:.2f}$ fixed bills; $v = \\${v:.2f}$/kit variable cost.\n"
            f"- **Domain & Asymptotes:** Practical domain is $0 < s \\le {cap}$. At $s = 0$, $A(0)$ is **undefined** (vertical asymptote at $s = 0$). As $s$ grows, $A(s)$ approaches the horizontal asymptote $y = {v:.2f}$."
        )

        s_vals = np.arange(1, cap + 1)
        a_vals = F / s_vals + v
        df_found = pd.DataFrame({"Kits Built (s)": s_vals, "Average Cost A(s) ($/kit)": np.round(a_vals, 2)})

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=s_vals, y=a_vals, mode="lines+markers", name="Average Cost A(s)"))
        fig.add_hline(y=v, line_dash="dash", annotation_text=f"Horizontal Asymptote y = ${v:.0f}")
        fig.update_layout(
            title=f"ROB-POWER | Average Cost Curve (0 < s <= {cap})",
            xaxis_title="Kits Built (s)",
            yaxis_title="Average Cost ($/kit)",
        )
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(df_found, use_container_width=True)
        st.download_button(
            "Export Table + Assumptions (CSV)",
            data=df_found.to_csv(index=False),
            file_name="ROB-POWER_foundation_avg_cost.csv",
            mime="text/csv",
        )
        st.info("**Interpretation Prompt:** Why can't we plug in $s = 0$, and what is the lowest possible average cost per kit at our current capacity?")

    # ---------------- APPLIED: COMPOSED PRICE-PROFIT ----------------
    elif level == "Applied":
        st.subheader("Applied Skill: Composed Demand, Revenue, Cost & Profit")
        st.latex(r"q(p) = \max\left(0, 2D_0 - \frac{D_0}{p_0}p\right), \quad s(p) = \min(\text{capacity}, q(p)), \quad P(p) = p \cdot s(p) - (F + v \cdot s(p))")

        breakpoint_p = p0 * (2 - cap / D0) if D0 > 0 else 0
        st.markdown(
            f"- **Business Question:** If we raise or lower our kit price $p$, how do customer demand and our capacity ceiling combine to affect profit?\n"
            f"- **Variables & Units:** $p$ = kit price ($/kit) on domain $0 \\le p \\le {2*p0:.0f}$; $q(p)$ = demanded kits; $s(p)$ = completed sales capped at {cap} kits.\n"
            f"- **Capacity Breakpoint:** At $p = \\${breakpoint_p:.2f}$, demand equals our {cap}-kit build limit. Below $\\${breakpoint_p:.2f}$, sales stay flat at {cap} kits."
        )

        prices = np.linspace(0, 2 * p0, 81)
        q_cont = np.maximum(0, 2 * D0 - (D0 / p0) * prices)
        s_cont = np.minimum(cap, q_cont)
        r_cont = prices * s_cont
        c_cont = F + v * s_cont
        p_cont = r_cont - c_cont

        # Integer-rounded actual transactions
        s_int = np.floor(s_cont)
        p_int = prices * s_int - (F + v * s_int)

        df_app = pd.DataFrame({
            "Price ($)": np.round(prices, 2),
            "Demand q(p)": np.round(q_cont, 2),
            "Capped Sales s(p)": np.round(s_cont, 2),
            "Continuous Profit ($)": np.round(p_cont, 2),
            "Whole-Unit Profit ($)": np.round(p_int, 2),
        })

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=prices, y=p_cont, mode="lines", name="Continuous Profit P(p)"))
        fig.add_trace(go.Scatter(x=prices, y=p_int, mode="lines", line=dict(dash="dot"), name="Whole-Unit Profit (Floored)"))
        fig.add_hline(y=0, line_color="black", annotation_text="Break-Even Zero Line")
        if 0 <= breakpoint_p <= 2 * p0:
            fig.add_vline(x=breakpoint_p, line_dash="dash", annotation_text=f"Capacity Corner p = ${breakpoint_p:.0f}")
        fig.update_layout(
            title=f"ROB-POWER | Composed Profit Function (Capacity = {cap} kits)",
            xaxis_title="Kit Price p ($)",
            yaxis_title="Monthly Profit ($)",
        )
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(df_app, use_container_width=True)
        st.download_button(
            "Export Composed Price Table (CSV)",
            data=df_app.to_csv(index=False),
            file_name="ROB-POWER_applied_composed_profit.csv",
            mime="text/csv",
        )
        st.info("**Interpretation Prompt:** Why does profit drop if we price below the capacity corner ($" + f"{breakpoint_p:.0f}" + ")?")

    # ---------------- ADVANCED: SEASONAL TRIG MODEL ----------------
    else:
        st.subheader("Advanced Skill: Periodic Seasonal Demand Model")
        st.latex(r"D(t) = D_0 \left(1 + 0.20 \sin\left(\frac{2\pi(t - 1)}{12}\right)\right)")
        st.markdown(
            f"- **Business Question:** If robotics kit demand follows a 12-month seasonal wave (peaking in Month 4 and dipping in Month 10), how much potential demand do we lose to our {cap}-kit capacity limit?\n"
            f"- **Assumptions & Limits:** Assumed 20% seasonal swing around $D_0 = {D0}$ kits/month; period = 12 months. This is an instructional seasonal assumption, not guaranteed future sales."
        )

        months = np.arange(1, 13)
        seasonal_demand = D0 * (1 + 0.20 * np.sin(2 * np.pi * (months - 1) / 12))
        realized_sales = np.minimum(cap, np.floor(seasonal_demand))

        df_adv = pd.DataFrame({
            "Month Index (t)": months,
            "Seasonal Demand D(t)": np.round(seasonal_demand, 2),
            "Realized Sales (Capped)": realized_sales.astype(int),
            "Unserved Demand": np.maximum(0, np.floor(seasonal_demand) - realized_sales).astype(int),
        })

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=months, y=seasonal_demand, mode="lines+markers", name="Seasonal Demand D(t)"))
        fig.add_trace(go.Bar(x=months, y=realized_sales, name="Realized Sales (Clipped at Capacity)", opacity=0.6))
        fig.add_hline(y=cap, line_dash="dash", annotation_text=f"Capacity Limit = {cap} kits")
        fig.update_layout(
            title="ROB-POWER | 12-Month Seasonal Demand vs. Capacity-Clipped Sales",
            xaxis_title="Month Index (t = 1..12)",
            yaxis_title="Robot Kits",
        )
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(df_adv, use_container_width=True)
        st.download_button(
            "Export Seasonal Model Table (CSV)",
            data=df_adv.to_csv(index=False),
            file_name="ROB-POWER_advanced_seasonal.csv",
            mime="text/csv",
        )
        st.info("**Interpretation Prompt:** In which months does our capacity limit clip peak demand, and how much revenue is left on the table?")


# =========================================================
# TAB 4: FIXTURE TESTS (JAN - MAR VERIFICATION)
# =========================================================
with tabs[3]:
    st.header("Stage 1 Acceptance Tests (ROB-2 Fixture)")
    st.warning(
        "**Fixture-Test Mode:** Replays the exact 3-month test table from the handbook using `capacity_override` to verify your ledger math."
    )

    expected_df = pd.DataFrame([
        {"Month": "2027-01", "Requested": 40, "Cap Override": 30, "Ad Cost": 0, "Expected Sold": 30, "Expected Rev": 3600, "Expected Exp": 2550, "Expected Profit": 1050, "Expected Cash": 6950, "Expected Stock": 70},
        {"Month": "2027-02", "Requested": 44, "Cap Override": 30, "Ad Cost": 100, "Expected Sold": 30, "Expected Rev": 3600, "Expected Exp": 2650, "Expected Profit": 950, "Expected Cash": 8800, "Expected Stock": 40},
        {"Month": "2027-03", "Requested": 36, "Cap Override": 22, "Ad Cost": 0, "Expected Sold": 22, "Expected Rev": 2640, "Expected Exp": 2030, "Expected Profit": 610, "Expected Cash": 10070, "Expected Stock": 18},
    ])
    st.subheader("Expected Handbook Results")
    st.dataframe(expected_df, use_container_width=True)

    if st.button("Reset & Replay 3-Month Fixture Test"):
        test_state = get_default_state()
        fixture_rows = [
            ("2027-01-31", "FIX-2027-01", 40, 30, 0.0),
            ("2027-02-28", "FIX-2027-02", 44, 30, 100.0),
            ("2027-03-31", "FIX-2027-03", 36, 22, 0.0),
        ]
        for dt, rid, req, cap_ov, ad in fixture_rows:
            _, _, test_state = simulate_month(
                test_state,
                sim_date=dt,
                run_id=rid,
                requested=req,
                ad_cost=ad,
                capacity_override=cap_ov,
            )
        st.session_state.sim_state = test_state
        st.success("Fixture replay complete! Compare the ledger below with the expected table above.")
        st.rerun()

    if len(state["ledger"]) > 0:
        st.subheader("Actual App Results")
        st.dataframe(pd.DataFrame(state["ledger"]), use_container_width=True)


# =========================================================
# TAB 5: STAGES 2-4 PLACEHOLDER
# =========================================================
with tabs[4]:
    st.header("Stages 2-4 (Coming in Later Stages)")
    st.info(
        "These modules will be added after Stage 1 passes all checks:\n"
        "- **Stage 2:** AI Department Recommendations (Finance, Operations, Marketing, Reviewer), Full Report Center (PDF/Income/Cash Flow), and Costed Marketing Studio.\n"
        "- **Stage 3:** Snapshot-based Precalculus Practice Assessments & Cross-Course Connections.\n"
        "- **Stage 4 (Optional):** Demonstration Storefront (no real payments)."
    )
