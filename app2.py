# app.py
# Streamlit app to demo your lifecycle model (Sales curve + Inventory simulation)
# with archetype-specific Yield Factor + Conversion Rate means, parsed from the
# report-style "Sales volume parameters" sheet.

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go

st.set_page_config(page_title="Lifecycle Simulator", layout="wide")

# -----------------------------
# Helpers: small UI
# -----------------------------
def banner():
    st.title("📈 Inventory Lifecycle Simulator")
    st.caption(
        "Loads your Excel, extracts Median Year-1 Sales + Median Growth Rates (Years 2–10), "
        "computes archetype-specific Yield Factor and Conversion Rate means, then simulates "
        "10-year sales + inventory carryover."
    )

def info_box():
    with st.expander("What this app is doing (high-level)", expanded=False):
        st.markdown(
            """
**Pipeline**
1) Load sheets: Conversion rates, Production yields, Product parameters, Sales volume parameters  
2) Map `Parent0 → Archetype` (from Product parameters)  
3) Compute `Yield_Factor = Actual yield / Planned yield (bu/ac)` and attach archetype  
4) Attach archetype to conversion rates (`totalConversionRate`)  
5) Parse report-style Sales sheet to extract:
   - Median first-year sales (Year 1 baseline) by Archetype & Maturity (85/95/105/115)
   - Median YoY growth rates (Years 2–10) by Archetype & Maturity  
6) Build Sales curve (Year1..Year10) by compounding YoY rates  
7) Run inventory logic (planned production = next year sales) with 2% production loss + 10% carryover loss  
8) Show table + plots
"""
        )

# -----------------------------
# Helpers: parsing report-style sheet
# -----------------------------
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
        "relative sales year"
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
    """Convert growth cell to decimal rate."""
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
    # if looks like percent (e.g., 51.3), scale down
    return v / 100.0 if abs(v) > 2 else v

def extract_median_sales_and_growth(sales_raw: pd.DataFrame):
    """
    Returns:
      median_sales_df: columns ['Archetype', 85, 95, 105, 115]
      growth_df: columns include ['Archetype', 'Maturity', <year columns>]
      year_map: dict mapping int year -> actual column label in growth_df
    """
    # 1) Median first year sales
    anchor = find_cell(sales_raw, "Median first year sales volumes")
    if anchor is None:
        raise ValueError("Couldn't find 'Median first year sales volumes' block in Sales volume parameters.")
    anchor_r, _ = anchor

    median_header_r = None
    for r in range(anchor_r, min(anchor_r + 40, sales_raw.shape[0])):
        if norm_txt(sales_raw.iat[r, 0]) == "archetype":
            median_header_r = r
            break
    if median_header_r is None:
        raise ValueError("Couldn't locate 'Archetype' header row for the Median first year sales block.")

    median_start_c = 0
    median_end_c = scan_row_until_blank(sales_raw, median_header_r, median_start_c)

    median_df = sales_raw.iloc[median_header_r + 1 :, median_start_c:median_end_c].copy()
    median_df.columns = sales_raw.iloc[median_header_r, median_start_c:median_end_c].values

    if "Archetype" not in median_df.columns:
        raise ValueError("Median sales block parse failed: 'Archetype' column not found after header assignment.")

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
        raise ValueError(
            f"Missing maturity columns {missing} in median sales block. Found maturities: {sorted(maturity_cols_map.keys())}"
        )

    median_sales_df = median_df[["Archetype"] + [maturity_cols_map[m] for m in needed_maturities]].copy()
    median_sales_df.columns = ["Archetype"] + needed_maturities
    for m in needed_maturities:
        median_sales_df[m] = pd.to_numeric(median_sales_df[m], errors="coerce")
    median_sales_df = median_sales_df.dropna(subset=needed_maturities, how="all")

    # 2) Median growth rates
    growth_anchor = find_cell(sales_raw, "Median growth rates")
    if growth_anchor is None:
        raise ValueError("Couldn't find 'Median growth rates' block in Sales volume parameters.")
    ga_r, _ = growth_anchor

    growth_header_r = None
    growth_start_c = None
    for r in range(ga_r, min(ga_r + 50, sales_raw.shape[0])):
        for c in range(sales_raw.shape[1] - 1):
            if norm_txt(sales_raw.iat[r, c]) == "archetype" and norm_txt(sales_raw.iat[r, c + 1]) == "maturity":
                growth_header_r = r
                growth_start_c = c
                break
        if growth_header_r is not None:
            break
    if growth_header_r is None:
        raise ValueError("Couldn't locate 'Archetype' + 'Maturity' header for the Median growth rates block.")

    growth_end_c = scan_row_until_blank(sales_raw, growth_header_r, growth_start_c)

    growth_df = sales_raw.iloc[growth_header_r + 1 :, growth_start_c:growth_end_c].copy()
    growth_df.columns = sales_raw.iloc[growth_header_r, growth_start_c:growth_end_c].values

    if "Archetype" not in growth_df.columns or "Maturity" not in growth_df.columns:
        raise ValueError("Growth block parse failed: 'Archetype'/'Maturity' columns not found.")

    growth_df = growth_df.dropna(subset=["Archetype", "Maturity"])
    growth_df["Archetype"] = clean_series(growth_df["Archetype"])
    growth_df["Maturity"] = pd.to_numeric(growth_df["Maturity"], errors="coerce")
    growth_df = growth_df[~growth_df["Archetype"].apply(is_bad_archetype)]
    growth_df = growth_df.dropna(subset=["Archetype", "Maturity"])

    year_map = normalize_numeric_cols([c for c in growth_df.columns if norm_txt(c) not in ["archetype", "maturity"]])

    years_needed = list(range(2, 11))
    missing_years = [y for y in years_needed if y not in year_map]
    if missing_years:
        raise ValueError(
            f"Missing growth year columns {missing_years} in Median growth rates block. Found: {sorted(year_map.keys())}"
        )

    return median_sales_df, growth_df, year_map

# -----------------------------
# Lifecycle builder
# -----------------------------
def build_sales_curve(y1: float, yoy_rates: list[float]):
    sales = [float(y1)]
    for r in yoy_rates:  # Year2..Year10
        nxt = sales[-1] * (1 + float(r))
        if nxt < 0:
            nxt = 0.0
        sales.append(nxt)
    return sales  # length 10

def simulate_inventory(sales: list[float], y_mean: float, c_mean: float, prod_loss_rate=0.02, carry_loss_rate=0.10):
    carryover = 0.0
    rows = []
    for yr in range(10):
        planned_prod = sales[yr + 1] if yr < 9 else 0.0  # planned = next year's sales
        new_prod = planned_prod * y_mean * c_mean
        prod_loss = new_prod * prod_loss_rate
        carry_loss = carryover * carry_loss_rate

        total_saleable = (carryover - carry_loss) + (new_prod - prod_loss)
        remaining = total_saleable - sales[yr]

        rows.append([
            carryover,
            -carry_loss,
            planned_prod,
            new_prod,
            -prod_loss,
            total_saleable,
            sales[yr],
            remaining
        ])
        carryover = remaining

    cols = [f"Year {i}" for i in range(1, 11)]
    idx = [
        "Carryover inventory from prior year",
        "Carryover quality loss (10%)",
        "Planned production (= next yr sales)",
        "New production (after yield & conv.)",
        "Production quality loss (2%)",
        "Total saleable inventory",
        "Sales",
        "Remaining inventory (carryover out)"
    ]
    lifecycle_df = pd.DataFrame(np.array(rows).T, columns=cols, index=idx)
    return lifecycle_df

def plot_sales_vs_inventory(sales: list[float], remaining: np.ndarray, title: str):
    cols = [f"Year {i}" for i in range(1, 11)]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=cols, y=sales, mode="lines+markers", name="Sales"))
    fig.add_trace(go.Scatter(x=cols, y=remaining, mode="lines+markers", name="Remaining inventory"))
    fig.update_layout(title=title, xaxis_title="Year", yaxis_title="Volume", height=520)
    return fig

# -----------------------------
# Data loader (cached)
# -----------------------------
@st.cache_data(show_spinner=False)
def load_workbook(file_bytes: bytes):
    # Read sheets
    conv_tab = pd.read_excel(file_bytes, sheet_name="Conversion rates")
    yield_tab = pd.read_excel(file_bytes, sheet_name="Production yields")
    params_tab = pd.read_excel(file_bytes, sheet_name="Product parameters")
    sales_raw = pd.read_excel(file_bytes, sheet_name="Sales volume parameters", header=None)

    # Clean column names for structured tables
    for df_ in [conv_tab, yield_tab, params_tab]:
        df_.columns = df_.columns.astype(str).str.strip()

    return conv_tab, yield_tab, params_tab, sales_raw

# -----------------------------
# Main App
# -----------------------------
banner()
info_box()

st.sidebar.header("📦 Input")
uploaded = st.sidebar.file_uploader("Upload your Excel (.xlsx)", type=["xlsx"])

use_default_path = st.sidebar.checkbox("Use Colab path (/content/data (1).xlsx)", value=False)

file_source = None
file_bytes = None

if use_default_path:
    file_source = "path"
else:
    file_source = "upload"

if file_source == "path":
    st.sidebar.info("Using `/content/data (1).xlsx` (make sure it exists in this environment).")
    try:
        # Streamlit runs locally; this will work only if file exists on the same machine.
        # For most cases, upload is easier.
        with open(file_path, "rb") as f:
            file_bytes = f.read()
    except Exception as e:
        st.error(
            "Couldn't read `/content/data (1).xlsx` from this Streamlit environment. "
            "Upload the file using the sidebar instead."
        )
        st.stop()
else:
    if uploaded is None:
        st.warning("Upload your Excel file to begin.")
        st.stop()
    file_bytes = uploaded.getvalue()

# Load workbook
with st.spinner("Loading workbook..."):
    conv_tab, yield_tab, params_tab, sales_raw = load_workbook(file_bytes)

# Sheet sanity check preview
with st.expander("✅ Sanity-check: preview sheets", expanded=False):
    st.write("**Conversion rates**", conv_tab.head())
    st.write("**Production yields**", yield_tab.head())
    st.write("**Product parameters**", params_tab.head())
    st.write("**Sales volume parameters (raw grid)**", sales_raw.head())

# Validate + build Parent0 -> Archetype mapping
required_params = {"Parent0", "Archetype"}
if not required_params.issubset(set(params_tab.columns)):
    st.error(f"`Product parameters` must contain columns: {sorted(list(required_params))}")
    st.stop()

params_tab["Parent0"] = params_tab["Parent0"].astype(str).str.strip()
params_tab["Archetype"] = params_tab["Archetype"].astype(str).str.strip()
parent_to_arch = params_tab[["Parent0", "Archetype"]].dropna()

# Yield factor prep
required_yield = {"Parent0", "Planned yield (bu/ac)", "Actual yield"}
if not required_yield.issubset(set(yield_tab.columns)):
    st.error(f"`Production yields` must contain columns: {sorted(list(required_yield))}")
    st.stop()

yield_tab["Parent0"] = yield_tab["Parent0"].astype(str).str.strip()
yield_tab["Planned yield (bu/ac)"] = pd.to_numeric(yield_tab["Planned yield (bu/ac)"], errors="coerce")
yield_tab["Actual yield"] = pd.to_numeric(yield_tab["Actual yield"], errors="coerce")
yield_tab["Yield_Factor"] = yield_tab["Actual yield"] / yield_tab["Planned yield (bu/ac)"]
yield_tab["Yield_Factor"] = yield_tab["Yield_Factor"].replace([np.inf, -np.inf], np.nan)
yield_w_arch = yield_tab.merge(parent_to_arch, on="Parent0", how="left")

# Conversion prep
required_conv = {"Parent0", "totalConversionRate"}
if not required_conv.issubset(set(conv_tab.columns)):
    st.error(f"`Conversion rates` must contain columns: {sorted(list(required_conv))}")
    st.stop()

conv_tab["Parent0"] = conv_tab["Parent0"].astype(str).str.strip()
conv_tab["totalConversionRate"] = pd.to_numeric(conv_tab["totalConversionRate"], errors="coerce")
conv_w_arch = conv_tab.merge(parent_to_arch, on="Parent0", how="left")

# Parse sales inputs
with st.spinner("Parsing Sales volume parameters (report-style)..."):
    median_sales_df, growth_df, year_map = extract_median_sales_and_growth(sales_raw)

with st.expander("🔎 Parsed sales inputs (what was extracted)", expanded=False):
    st.write("**Median first-year sales (Year 1 baseline)**")
    st.dataframe(median_sales_df)
    st.write("**Median growth rates (YoY for Years 2–10)**")
    st.dataframe(growth_df.head(20))

# Sidebar controls
st.sidebar.header("🎛️ Controls")
archetypes = sorted(median_sales_df["Archetype"].dropna().unique())
maturities = [85, 95, 105, 115]

selected_arch = st.sidebar.selectbox("Archetype", archetypes)
selected_mat = st.sidebar.selectbox("Maturity", maturities, index=0)

prod_loss_rate = st.sidebar.slider("Production loss rate", 0.0, 0.10, 0.02, 0.005)
carry_loss_rate = st.sidebar.slider("Carryover quality loss rate", 0.0, 0.30, 0.10, 0.01)

# Lookup: median sales
row_ms = median_sales_df[median_sales_df["Archetype"] == selected_arch]
y1 = None
if not row_ms.empty:
    v = row_ms[selected_mat].dropna()
    if not v.empty:
        y1 = float(v.iloc[0])

# Lookup: YoY rates
row_gr = growth_df[(growth_df["Archetype"] == selected_arch) & (growth_df["Maturity"] == selected_mat)]
yoy_rates = None
if not row_gr.empty:
    yoy_rates = [to_rate(row_gr[year_map[y]].iloc[0]) for y in range(2, 11)]

# Archetype-specific means for yield and conversion (fallback to overall)
y_vals = yield_w_arch.loc[yield_w_arch["Archetype"] == selected_arch, "Yield_Factor"].dropna()
c_vals = conv_w_arch.loc[conv_w_arch["Archetype"] == selected_arch, "totalConversionRate"].dropna()

y_mean = float(y_vals.mean()) if len(y_vals) else float(yield_w_arch["Yield_Factor"].dropna().mean())
c_mean = float(c_vals.mean()) if len(c_vals) else float(conv_w_arch["totalConversionRate"].dropna().mean())

# Main output layout
left, right = st.columns([1.1, 1.4], gap="large")

with left:
    st.subheader("1) Inputs used for this run")

    c1, c2, c3 = st.columns(3)
    c1.metric("Archetype", selected_arch)
    c2.metric("Maturity", str(selected_mat))
    c3.metric("Year-1 Median Sales", "N/A" if y1 is None else f"{y1:,.1f}")

    st.markdown("**Archetype-specific assumptions (means)**")
    a1, a2 = st.columns(2)
    a1.metric("Yield Factor (mean)", f"{y_mean:.4f}")
    a2.metric("Conversion Rate (mean)", f"{c_mean:.4f}")

    st.markdown("**YoY rates used (Years 2–10)**")
    if yoy_rates is None:
        st.error("No growth rates found for this Archetype + Maturity.")
    else:
        yoy_df = pd.DataFrame(
            {"Year": [f"Year {i}" for i in range(2, 11)], "YoY Rate (decimal)": yoy_rates}
        )
        st.dataframe(yoy_df, use_container_width=True)

with right:
    st.subheader("2) Simulation Output")

    if y1 is None:
        st.error("No median first-year sales found for this Archetype + Maturity.")
        st.stop()
    if yoy_rates is None:
        st.stop()

    sales = build_sales_curve(y1, yoy_rates)
    lifecycle_df = simulate_inventory(
        sales, y_mean, c_mean, prod_loss_rate=prod_loss_rate, carry_loss_rate=carry_loss_rate
    )

    st.markdown("**Lifecycle table (Year 1–Year 10)**")
    st.dataframe(lifecycle_df.round(1), use_container_width=True)

    remaining = lifecycle_df.loc["Remaining inventory (carryover out)"].astype(float).values

    fig = plot_sales_vs_inventory(
        sales,
        remaining,
        title=f"Inventory Lifecycle – {selected_arch} | Maturity {selected_mat} (Archetype-specific Yield & Conv)",
    )
    st.plotly_chart(fig, use_container_width=True)

# Optional: show the sales-only plot
with st.expander("📉 Sales curve only", expanded=False):
    cols = [f"Year {i}" for i in range(1, 11)]
    fig2 = go.Figure()
    fig2.add_trace(go.Scatter(x=cols, y=sales, mode="lines+markers", name="Sales"))
    fig2.update_layout(title="Sales (Year1..Year10)", xaxis_title="Year", yaxis_title="Sales", height=420)
    st.plotly_chart(fig2, use_container_width=True)

# Optional: quick diagnostics
with st.expander("🧪 Diagnostics (optional)", expanded=False):
    st.write("Rows with missing Archetype after mapping (Yield table):")
    st.write(int(yield_w_arch["Archetype"].isna().sum()))
    st.write("Rows with missing Archetype after mapping (Conversion table):")
    st.write(int(conv_w_arch["Archetype"].isna().sum()))

st.caption("Tip: If parsing fails, it usually means the 'Sales volume parameters' sheet text labels changed or moved.")
