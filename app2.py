# app.py
# Streamlit "Explain What I Did" Visual App
# - Shows a visual pipeline (flowchart)
# - Shows extracted inputs (Median Year-1 sales + Median growth rates)
# - Shows archetype-specific Yield Factor + Conversion Rate assumptions
# - Shows Sales curve build (YoY compounding) + Inventory simulation
# - Shows outputs as charts + table
#
# Run:
#   streamlit run app.py
#
# Note:
#   This app expects your Excel to have sheets:
#   - "Conversion rates" with columns: Parent0, totalConversionRate
#   - "Production yields" with columns: Parent0, Planned yield (bu/ac), Actual yield
#   - "Product parameters" with columns: Parent0, Archetype
#   - "Sales volume parameters" report-style sheet (loaded header=None)

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go

st.set_page_config(page_title="Lifecycle Model – What I Did (Visual)", layout="wide")

# =============================
# Visual helpers
# =============================
def show_pipeline_flow():
    st.subheader("1) Visual pipeline (what I built)")
    st.caption("This is the end-to-end flow of the cell — from Excel sheets → extracted inputs → simulation → outputs.")

    # Simple flow diagram using Mermaid-like style via markdown
    # (Streamlit doesn't natively render Mermaid; we use a clean ASCII + sections.)
    col1, col2 = st.columns([1, 1.3], gap="large")
    with col1:
        st.markdown(
            """
**Inputs (Excel tabs)**
- Product parameters → `Parent0 → Archetype`
- Production yields → `Yield_Factor`
- Conversion rates → `totalConversionRate`
- Sales volume parameters → Median **Year-1 Sales** + Median **YoY Growth Rates**

**Core transformations**
- Clean keys (`Parent0`) and numeric types
- Merge archetype onto yield + conversion
- Parse report-style sales sheet into clean tables
- Build 10-year sales curve (compounding YoY)
- Simulate inventory lifecycle with losses

**Outputs**
- Lifecycle table (Year 1…10)
- Sales vs Remaining Inventory plot
            """
        )

    with col2:
        # A more "visual" pipeline using Plotly shapes (looks like a flowchart)
        fig = go.Figure()
        fig.update_layout(
            height=320,
            margin=dict(l=10, r=10, t=10, b=10),
            xaxis=dict(visible=False),
            yaxis=dict(visible=False),
        )

        boxes = [
            ("Excel Sheets", 0.05, 0.65),
            ("Clean + Merge\n(Parent0→Archetype)", 0.27, 0.65),
            ("Extract Sales Inputs\n(Year1 + YoY 2..10)", 0.49, 0.65),
            ("Build Sales Curve\n(compound YoY)", 0.71, 0.65),
            ("Inventory Simulation\n(losses + carryover)", 0.82, 0.25),
            ("Outputs\n(table + plots)", 0.49, 0.25),
        ]

        for text, x, y in boxes:
            fig.add_shape(
                type="rect",
                x0=x, y0=y, x1=x + 0.18, y1=y + 0.22,
                line=dict(width=1),
                fillcolor="rgba(200,200,200,0.15)",
            )
            fig.add_annotation(x=x + 0.09, y=y + 0.11, text=text, showarrow=False, font=dict(size=12))

        arrows = [
            ((0.23, 0.76), (0.27, 0.76)),
            ((0.45, 0.76), (0.49, 0.76)),
            ((0.67, 0.76), (0.71, 0.76)),
            ((0.80, 0.65), (0.86, 0.47)),
            ((0.71, 0.25), (0.67, 0.25)),
            ((0.49, 0.47), (0.49, 0.40)),
        ]
        for (x0, y0), (x1, y1) in arrows:
            fig.add_annotation(x=x1, y=y1, ax=x0, ay=y0, xref="paper", yref="paper",
                               axref="paper", ayref="paper", showarrow=True, arrowhead=3)

        st.plotly_chart(fig, use_container_width=True)


# =============================
# Parsing helpers (Sales volume parameters)
# =============================
def norm_txt(x):
    if pd.isna(x):
        return ""
    return str(x).strip().lower()

def find_cell(sales_raw: pd.DataFrame, text: str):
    target = text.strip().lower()
    for r in range(sales_raw.shape[0]):
        for c in range(sales_raw.shape[1]):
            if target in norm_txt(sales_raw.iat[r, c]):
                return r, c
    return None

def scan_row_until_blank(sales_raw: pd.DataFrame, r: int, start_c: int):
    end = start_c
    while end < sales_raw.shape[1] and not pd.isna(sales_raw.iat[r, end]) and str(sales_raw.iat[r, end]).strip() != "":
        end += 1
    return end

def clean_series(s: pd.Series):
    s = s.astype(str).str.strip()
    s = s.replace({"nan": np.nan, "None": np.nan, "": np.nan})
    return s

def is_bad_archetype(x):
    t = norm_txt(x)
    if t in ["", "archetype", "maturity"]:
        return True
    bad = [
        "median first year sales volumes",
        "average first year sales volumes",
        "median growth rates",
        "average growth rates",
        "relative sales year",
    ]
    return any(b in t for b in bad)

def normalize_numeric_cols(cols):
    m = {}
    for c in cols:
        try:
            m[int(float(str(c).strip()))] = c
        except Exception:
            pass
    return m

def to_rate(x):
    # Convert growth cell to decimal rate.
    if pd.isna(x):
        return 0.0
    if isinstance(x, str):
        s = x.strip()
        if s.endswith("%"):
            s = s[:-1].strip()
            v = pd.to_numeric(s, errors="coerce")
            return 0.0 if pd.isna(v) else float(v) / 100.0
        v = pd.to_numeric(s, errors="coerce")
        if pd.isna(v):
            return 0.0
        v = float(v)
    else:
        v = float(x)
    return v / 100.0 if abs(v) > 2 else v

def extract_sales_inputs(sales_raw: pd.DataFrame):
    # --- Median first-year sales ---
    anchor = find_cell(sales_raw, "Median first year sales volumes")
    if anchor is None:
        raise ValueError("Couldn't find 'Median first year sales volumes' block.")
    anchor_r, _ = anchor

    median_header_r = None
    for r in range(anchor_r, min(anchor_r + 40, sales_raw.shape[0])):
        if norm_txt(sales_raw.iat[r, 0]) == "archetype":
            median_header_r = r
            break
    if median_header_r is None:
        raise ValueError("Couldn't find 'Archetype' header for Median first year sales block.")

    median_start_c = 0
    median_end_c = scan_row_until_blank(sales_raw, median_header_r, median_start_c)

    median_df = sales_raw.iloc[median_header_r + 1 :, median_start_c:median_end_c].copy()
    median_df.columns = sales_raw.iloc[median_header_r, median_start_c:median_end_c].values

    if "Archetype" not in median_df.columns:
        raise ValueError("Median sales parse failed: 'Archetype' column not found.")

    median_df = median_df.dropna(subset=["Archetype"])
    median_df["Archetype"] = clean_series(median_df["Archetype"])
    median_df = median_df[~median_df["Archetype"].apply(is_bad_archetype)]

    maturity_cols_map = {}
    for col in median_df.columns:
        if norm_txt(col) == "archetype":
            continue
        try:
            maturity_cols_map[int(float(str(col).strip()))] = col
        except Exception:
            pass

    needed_maturities = [85, 95, 105, 115]
    missing = [m for m in needed_maturities if m not in maturity_cols_map]
    if missing:
        raise ValueError(f"Missing maturity cols {missing} in median sales block. Found: {sorted(maturity_cols_map.keys())}")

    median_sales_df = median_df[["Archetype"] + [maturity_cols_map[m] for m in needed_maturities]].copy()
    median_sales_df.columns = ["Archetype"] + needed_maturities
    for m in needed_maturities:
        median_sales_df[m] = pd.to_numeric(median_sales_df[m], errors="coerce")
    median_sales_df = median_sales_df.dropna(subset=needed_maturities, how="all")

    # --- Median growth rates ---
    growth_anchor = find_cell(sales_raw, "Median growth rates")
    if growth_anchor is None:
        raise ValueError("Couldn't find 'Median growth rates' block.")
    ga_r, _ = growth_anchor

    growth_header_r, growth_start_c = None, None
    for r in range(ga_r, min(ga_r + 60, sales_raw.shape[0])):
        for c in range(sales_raw.shape[1] - 1):
            if norm_txt(sales_raw.iat[r, c]) == "archetype" and norm_txt(sales_raw.iat[r, c + 1]) == "maturity":
                growth_header_r = r
                growth_start_c = c
                break
        if growth_header_r is not None:
            break
    if growth_header_r is None:
        raise ValueError("Couldn't find Archetype+Maturity header in Median growth rates block.")

    growth_end_c = scan_row_until_blank(sales_raw, growth_header_r, growth_start_c)

    growth_df = sales_raw.iloc[growth_header_r + 1 :, growth_start_c:growth_end_c].copy()
    growth_df.columns = sales_raw.iloc[growth_header_r, growth_start_c:growth_end_c].values

    if "Archetype" not in growth_df.columns or "Maturity" not in growth_df.columns:
        raise ValueError("Growth rates parse failed: 'Archetype'/'Maturity' not found.")

    growth_df = growth_df.dropna(subset=["Archetype", "Maturity"])
    growth_df["Archetype"] = clean_series(growth_df["Archetype"])
    growth_df["Maturity"] = pd.to_numeric(growth_df["Maturity"], errors="coerce")
    growth_df = growth_df[~growth_df["Archetype"].apply(is_bad_archetype)]
    growth_df = growth_df.dropna(subset=["Archetype", "Maturity"])

    year_map = normalize_numeric_cols([c for c in growth_df.columns if norm_txt(c) not in ["archetype", "maturity"]])

    years_needed = list(range(2, 11))
    missing_years = [y for y in years_needed if y not in year_map]
    if missing_years:
        raise ValueError(f"Missing growth year columns {missing_years}. Found: {sorted(year_map.keys())}")

    return median_sales_df, growth_df, year_map


# =============================
# Model builders
# =============================
def build_sales_curve(y1: float, yoy_rates: list[float]):
    sales = [float(y1)]
    for r in yoy_rates:  # Year2..Year10
        nxt = sales[-1] * (1 + float(r))
        if nxt < 0:
            nxt = 0.0
        sales.append(nxt)
    return sales  # len 10

def simulate_inventory(sales, y_mean, c_mean, prod_loss_rate=0.02, carry_loss_rate=0.10):
    carryover = 0.0
    rows = []
    for yr in range(10):
        planned_prod = sales[yr + 1] if yr < 9 else 0.0  # planned = next year's sales
        new_prod = planned_prod * y_mean * c_mean
        prod_loss = new_prod * prod_loss_rate
        carry_loss = carryover * carry_loss_rate
        total_saleable = (carryover - carry_loss) + (new_prod - prod_loss)
        remaining = total_saleable - sales[yr]
        rows.append([carryover, -carry_loss, planned_prod, new_prod, -prod_loss, total_saleable, sales[yr], remaining])
        carryover = remaining

    cols = [f"Year {i}" for i in range(1, 11)]
    idx = [
        "Carryover inventory from prior year",
        "Carryover quality loss",
        "Planned production (= next yr sales)",
        "New production (after yield & conv.)",
        "Production quality loss",
        "Total saleable inventory",
        "Sales",
        "Remaining inventory (carryover out)"
    ]
    lifecycle_df = pd.DataFrame(np.array(rows).T, columns=cols, index=idx)
    return lifecycle_df

def chart_sales_and_inventory(sales, remaining, title):
    cols = [f"Year {i}" for i in range(1, 11)]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=cols, y=sales, mode="lines+markers", name="Sales"))
    fig.add_trace(go.Scatter(x=cols, y=remaining, mode="lines+markers", name="Remaining inventory"))
    fig.update_layout(title=title, xaxis_title="Year", yaxis_title="Volume", height=450)
    return fig

def chart_sales_build(sales, yoy_rates):
    # Visualize YoY compounding effect (bar for rates + line for sales)
    years = [f"Y{i}" for i in range(1, 11)]
    rates = [None] + yoy_rates  # align: Year1 has no growth rate
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=years, y=sales, mode="lines+markers", name="Sales"))
    fig.add_trace(go.Bar(x=years[1:], y=yoy_rates, name="YoY rate (decimal)", opacity=0.5))
    fig.update_layout(
        title="How the Sales curve was built (Year 1 baseline + YoY compounding)",
        xaxis_title="Year",
        yaxis_title="Sales / YoY rate",
        height=450,
        barmode="overlay"
    )
    return fig

def chart_inventory_waterfall(year_label, carry_in, carry_loss, new_prod, prod_loss, sales):
    # A waterfall-like bar chart explaining one year’s mechanics
    # Note: Plotly waterfall exists; we keep it simple and reliable.
    steps = [
        ("Carryover in", carry_in),
        ("Carryover loss", carry_loss),   # negative
        ("New production", new_prod),
        ("Production loss", prod_loss),   # negative
        ("Sales", -sales),                # negative
    ]
    x = [s[0] for s in steps]
    y = [s[1] for s in steps]
    fig = go.Figure(go.Bar(x=x, y=y))
    fig.update_layout(
        title=f"Inventory math breakdown – {year_label}",
        xaxis_title="Components",
        yaxis_title="Volume impact (+/-)",
        height=360
    )
    return fig


# =============================
# Caching loader
# =============================
@st.cache_data(show_spinner=False)
def load_excel(file_bytes: bytes):
    conv_tab = pd.read_excel(file_bytes, sheet_name="Conversion rates")
    yield_tab = pd.read_excel(file_bytes, sheet_name="Production yields")
    params_tab = pd.read_excel(file_bytes, sheet_name="Product parameters")
    sales_raw = pd.read_excel(file_bytes, sheet_name="Sales volume parameters", header=None)

    for df_ in [conv_tab, yield_tab, params_tab]:
        df_.columns = df_.columns.astype(str).str.strip()

    return conv_tab, yield_tab, params_tab, sales_raw


# =============================
# App UI
# =============================
st.title("🎥 Visual Explanation: What I Did in That Cell")
st.caption("This Streamlit page is designed to *explain* your cell with visuals — not just show results.")

uploaded = st.sidebar.file_uploader("Upload your Excel (.xlsx)", type=["xlsx"])
if uploaded is None:
    st.info("Upload your Excel file to start.")
    show_pipeline_flow()
    st.stop()

conv_tab, yield_tab, params_tab, sales_raw = load_excel(uploaded.getvalue())

show_pipeline_flow()

st.divider()

# =============================
# 2) Data sanity visuals
# =============================
st.subheader("2) Data sanity-check (quick previews)")
c1, c2, c3 = st.columns(3)
with c1:
    st.markdown("**Conversion rates** (head)")
    st.dataframe(conv_tab.head(), use_container_width=True)
with c2:
    st.markdown("**Production yields** (head)")
    st.dataframe(yield_tab.head(), use_container_width=True)
with c3:
    st.markdown("**Product parameters** (head)")
    st.dataframe(params_tab.head(), use_container_width=True)

with st.expander("Show Sales volume parameters (raw grid preview)", expanded=False):
    st.dataframe(sales_raw.head(25), use_container_width=True)

st.divider()

# =============================
# 3) Prep: Parent0 -> Archetype, Yield_Factor, Conversion, and parsing sales inputs
# =============================
st.subheader("3) Core transformations (visual + simple)")

# Validate params
if not {"Parent0", "Archetype"}.issubset(set(params_tab.columns)):
    st.error("Product parameters must contain columns: Parent0, Archetype")
    st.stop()

params_tab["Parent0"] = params_tab["Parent0"].astype(str).str.strip()
params_tab["Archetype"] = params_tab["Archetype"].astype(str).str.strip()
parent_to_arch = params_tab[["Parent0", "Archetype"]].dropna()

# Validate yield
need_yield = {"Parent0", "Planned yield (bu/ac)", "Actual yield"}
if not need_yield.issubset(set(yield_tab.columns)):
    st.error("Production yields sheet missing one of: Parent0, Planned yield (bu/ac), Actual yield")
    st.stop()

yield_tab["Parent0"] = yield_tab["Parent0"].astype(str).str.strip()
yield_tab["Planned yield (bu/ac)"] = pd.to_numeric(yield_tab["Planned yield (bu/ac)"], errors="coerce")
yield_tab["Actual yield"] = pd.to_numeric(yield_tab["Actual yield"], errors="coerce")
yield_tab["Yield_Factor"] = yield_tab["Actual yield"] / yield_tab["Planned yield (bu/ac)"]
yield_tab["Yield_Factor"] = yield_tab["Yield_Factor"].replace([np.inf, -np.inf], np.nan)

yield_w_arch = yield_tab.merge(parent_to_arch, on="Parent0", how="left")

# Validate conversion
need_conv = {"Parent0", "totalConversionRate"}
if not need_conv.issubset(set(conv_tab.columns)):
    st.error("Conversion rates sheet missing one of: Parent0, totalConversionRate")
    st.stop()

conv_tab["Parent0"] = conv_tab["Parent0"].astype(str).str.strip()
conv_tab["totalConversionRate"] = pd.to_numeric(conv_tab["totalConversionRate"], errors="coerce")
conv_w_arch = conv_tab.merge(parent_to_arch, on="Parent0", how="left")

# Parse sales inputs
try:
    median_sales_df, growth_df, year_map = extract_sales_inputs(sales_raw)
except Exception as e:
    st.error(f"Parsing failed: {e}")
    st.info("This usually happens if the text labels moved/changed in 'Sales volume parameters'.")
    st.stop()

# Visual: distribution of Yield_Factor and Conversion
colA, colB = st.columns(2)
with colA:
    st.markdown("**Yield Factor** = Actual yield ÷ Planned yield")
    fig_y = go.Figure()
    fig_y.add_trace(go.Histogram(x=yield_w_arch["Yield_Factor"].dropna(), nbinsx=30, name="Yield_Factor"))
    fig_y.update_layout(height=320, xaxis_title="Yield_Factor", yaxis_title="Count")
    st.plotly_chart(fig_y, use_container_width=True)
with colB:
    st.markdown("**Conversion Rate** = totalConversionRate")
    fig_c = go.Figure()
    fig_c.add_trace(go.Histogram(x=conv_w_arch["totalConversionRate"].dropna(), nbinsx=30, name="ConversionRate"))
    fig_c.update_layout(height=320, xaxis_title="Conversion Rate", yaxis_title="Count")
    st.plotly_chart(fig_c, use_container_width=True)

with st.expander("Show extracted sales inputs (clean tables)", expanded=False):
    st.markdown("### Median first-year sales (Year 1 baseline)")
    st.dataframe(median_sales_df, use_container_width=True)
    st.markdown("### Median growth rates (Years 2–10 YoY)")
    st.dataframe(growth_df.head(30), use_container_width=True)

st.divider()

# =============================
# 4) Interactive "Explain the run"
# =============================
st.subheader("4) Interactive explanation (pick a scenario and see the model build)")

archetypes = sorted(median_sales_df["Archetype"].dropna().unique())
maturities = [85, 95, 105, 115]

left, right = st.columns([1.05, 1.6], gap="large")

with left:
    selected_arch = st.selectbox("Archetype", archetypes)
    selected_mat = st.selectbox("Maturity breakpoint", maturities, index=0)

    st.markdown("### Loss assumptions (same logic as your cell)")
    prod_loss_rate = st.slider("Production quality loss", 0.0, 0.10, 0.02, 0.005)
    carry_loss_rate = st.slider("Carryover quality loss", 0.0, 0.30, 0.10, 0.01)

    # Year breakdown selector for the waterfall
    year_breakdown = st.selectbox("Show inventory breakdown for year", [f"Year {i}" for i in range(1, 11)], index=0)

# Lookups
row_ms = median_sales_df[median_sales_df["Archetype"] == selected_arch]
y1 = None
if not row_ms.empty:
    v = row_ms[selected_mat].dropna()
    if not v.empty:
        y1 = float(v.iloc[0])

row_gr = growth_df[(growth_df["Archetype"] == selected_arch) & (growth_df["Maturity"] == selected_mat)]
yoy_rates = None
if not row_gr.empty:
    yoy_rates = [to_rate(row_gr[year_map[y]].iloc[0]) for y in range(2, 11)]

# Archetype means
y_vals = yield_w_arch.loc[yield_w_arch["Archetype"] == selected_arch, "Yield_Factor"].dropna()
c_vals = conv_w_arch.loc[conv_w_arch["Archetype"] == selected_arch, "totalConversionRate"].dropna()
y_mean = float(y_vals.mean()) if len(y_vals) else float(yield_w_arch["Yield_Factor"].dropna().mean())
c_mean = float(c_vals.mean()) if len(c_vals) else float(conv_w_arch["totalConversionRate"].dropna().mean())

with right:
    # Metrics
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Year-1 median sales", "N/A" if y1 is None else f"{y1:,.1f}")
    m2.metric("Yield mean (arch)", f"{y_mean:.4f}")
    m3.metric("Conv mean (arch)", f"{c_mean:.4f}")
    m4.metric("YoY years", "2..10")

    if y1 is None:
        st.error("No median first-year sales found for this archetype/maturity.")
        st.stop()
    if yoy_rates is None:
        st.error("No growth rates found for this archetype/maturity.")
        st.stop()

    # Build
    sales = build_sales_curve(y1, yoy_rates)
    lifecycle_df = simulate_inventory(sales, y_mean, c_mean, prod_loss_rate=prod_loss_rate, carry_loss_rate=carry_loss_rate)
    remaining = lifecycle_df.loc["Remaining inventory (carryover out)"].astype(float).values

    # Visual: Sales curve build
    fig_sales_build = chart_sales_build(sales, yoy_rates)
    st.plotly_chart(fig_sales_build, use_container_width=True)

    # Visual: Final output plot
    fig_out = chart_sales_and_inventory(
        sales,
        remaining,
        title=f"Sales vs Remaining Inventory — {selected_arch} | Maturity {selected_mat}"
    )
    st.plotly_chart(fig_out, use_container_width=True)

st.divider()

# =============================
# 5) Explain the inventory math with a per-year breakdown (visual)
# =============================
st.subheader("5) Visual: explain the inventory math (one year breakdown)")
st.caption(
    "This visual answers: 'What happened in a given year?' "
    "Carryover comes in → we apply carryover loss → we add new production → apply production loss → sell units → leftover becomes next carryover."
)

# Get values for selected year index
yr_idx = int(year_breakdown.split()[-1]) - 1  # 0-based
cols = [f"Year {i}" for i in range(1, 11)]
col = cols[yr_idx]

carry_in = float(lifecycle_df.loc["Carryover inventory from prior year", col])
carry_loss = float(lifecycle_df.loc["Carryover quality loss", col])           # negative
planned = float(lifecycle_df.loc["Planned production (= next yr sales)", col])
new_prod = float(lifecycle_df.loc["New production (after yield & conv.)", col])
prod_loss = float(lifecycle_df.loc["Production quality loss", col])           # negative
sales_this = float(lifecycle_df.loc["Sales", col])

wf = chart_inventory_waterfall(
    year_label=col,
    carry_in=carry_in,
    carry_loss=carry_loss,
    new_prod=new_prod,
    prod_loss=prod_loss,
    sales=sales_this
)
st.plotly_chart(wf, use_container_width=True)

st.divider()

# =============================
# 6) Show the lifecycle table + allow download
# =============================
st.subheader("6) Lifecycle table (what the cell outputs)")
st.dataframe(lifecycle_df.round(1), use_container_width=True)

csv = lifecycle_df.round(4).to_csv().encode("utf-8")
st.download_button("⬇️ Download lifecycle table as CSV", data=csv, file_name="lifecycle_table.csv", mime="text/csv")

st.divider()

# =============================
# 7) A script you can read while showing the visuals
# =============================
with st.expander("🎤 Presentation script (read this while demoing)", expanded=True):
    st.markdown(
        f"""
**Here’s what I did in this cell (while pointing at the visuals):**

1) **Loaded 4 Excel sheets** and cleaned column names so merges work reliably.  
2) From **Product parameters**, I created a mapping **`Parent0 → Archetype`**.  
3) From **Production yields**, I computed **Yield_Factor = Actual ÷ Planned** and merged Archetype onto each yield row.  
4) From **Conversion rates**, I converted `totalConversionRate` to numeric and merged Archetype onto each conversion row.  
5) From the report-style **Sales volume parameters** sheet, I **extracted two clean tables**:
   - **Median first-year sales** (Year 1 baseline) by Archetype and maturity {maturities}
   - **Median growth rates** (Years 2–10) by Archetype and maturity  
6) When I select **Archetype = `{selected_arch}`** and **Maturity = `{selected_mat}`**, the model:
   - pulls **Year 1 median sales** and **YoY rates**  
   - computes **archetype-specific means** for Yield_Factor and Conversion Rate  
7) I then **built a 10-year sales curve** by compounding YoY rates year over year.  
8) Finally, I ran the **inventory lifecycle simulation**:
   - planned production = next year’s sales  
   - new production = planned production × yield_mean × conv_mean  
   - apply **{prod_loss_rate:.3f}** production loss and **{carry_loss_rate:.3f}** carryover loss  
   - leftover becomes next year’s carryover  
9) The outputs are the **lifecycle table**, the **Sales vs Inventory plot**, and the **year-by-year breakdown** chart.
"""
    )
