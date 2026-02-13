import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px

st.set_page_config(page_title="Lifecycle Dashboard", layout="wide")

# =============================
# HELPERS (same as your notebook)
# =============================
def _scan_row_until_blank(row, start_col):
    end = start_col
    while end < len(row) and not pd.isna(row[end]):
        end += 1
    return end

def _normalize_numeric_cols(cols):
    out = {}
    for c in cols:
        try:
            out[int(float(str(c).strip()))] = c
        except Exception:
            pass
    return out

def _clean_text_series(s):
    s = s.astype(str).str.strip()
    s = s.replace({"nan": np.nan, "None": np.nan, "": np.nan})
    return s

def _is_bad_archetype_value(x: str) -> bool:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return True
    t = str(x).strip().lower()
    if t in ["archetype", "maturity"]:
        return True
    bad_phrases = [
        "average first year", "median first year", "sales volumes",
        "median growth", "relative sales year", "growth rates"
    ]
    return any(p in t for p in bad_phrases)

def _to_rate(x):
    if pd.isna(x):
        return 0.0
    if isinstance(x, str):
        xs = x.strip()
        if xs.endswith("%"):
            xs = xs[:-1].strip()
            v = pd.to_numeric(xs, errors="coerce")
            return 0.0 if pd.isna(v) else float(v) / 100.0
        v = pd.to_numeric(xs, errors="coerce")
        return 0.0 if pd.isna(v) else float(v) / (100.0 if abs(float(v)) > 2 else 1.0)
    v = float(x)
    return v / 100.0 if abs(v) > 2 else v

# =============================
# Load workbook
# =============================
@st.cache_data(show_spinner=False)
def load_required_sheets(uploaded_file):
    conv_tab  = pd.read_excel(uploaded_file, sheet_name="Conversion rates")
    yield_tab = pd.read_excel(uploaded_file, sheet_name="Production yields")
    sales_raw = pd.read_excel(uploaded_file, sheet_name="Sales volume parameters", header=None)

    # clean col names like notebook
    for df_ in [conv_tab, yield_tab]:
        df_.columns = df_.columns.astype(str).str.strip()

    return conv_tab, yield_tab, sales_raw

@st.cache_data(show_spinner=False)
def load_product_params(uploaded_file):
    return pd.read_excel(uploaded_file, sheet_name="Product parameters")

@st.cache_data(show_spinner=False)
def load_production_yields(uploaded_file):
    return pd.read_excel(uploaded_file, sheet_name="Production yields")

# =============================
# Parse Sales volume parameters (EXACT notebook logic)
# =============================
def parse_sales_volume_parameters(sales_raw: pd.DataFrame):
    # 1) MEDIAN FIRST YEAR SALES TABLE (LEFT BLOCK)
    median_header_row = int(
        sales_raw.index[sales_raw[0].astype(str).str.strip().str.lower().eq("archetype")][0]
    )

    median_start_col = 0
    median_end_col = _scan_row_until_blank(sales_raw.iloc[median_header_row].values, median_start_col)

    median_sales_df = sales_raw.iloc[median_header_row + 1 :, median_start_col:median_end_col].copy()
    median_sales_df.columns = sales_raw.iloc[median_header_row, median_start_col:median_end_col].values
    median_sales_df = median_sales_df.dropna(subset=["Archetype"])

    maturity_map = _normalize_numeric_cols([c for c in median_sales_df.columns if c != "Archetype"])
    needed_maturities = [85, 95, 105, 115]
    missing_m = [m for m in needed_maturities if m not in maturity_map]
    if missing_m:
        raise ValueError(f"Missing maturity columns {missing_m} in median sales table. Found: {list(maturity_map.keys())}")

    median_sales_df = median_sales_df[["Archetype"] + [maturity_map[m] for m in needed_maturities]].copy()
    median_sales_df.columns = ["Archetype"] + needed_maturities

    median_sales_df["Archetype"] = _clean_text_series(median_sales_df["Archetype"])
    for m in needed_maturities:
        median_sales_df[m] = pd.to_numeric(median_sales_df[m], errors="coerce")

    median_sales_df = median_sales_df[~median_sales_df["Archetype"].apply(_is_bad_archetype_value)]
    median_sales_df = median_sales_df.dropna(subset=needed_maturities, how="all")

    # 2) MEDIAN GROWTH RATES TABLE (RIGHT BLOCK)
    growth_header_row, growth_start_col = None, None
    for r in range(sales_raw.shape[0]):
        for c in range(sales_raw.shape[1] - 1):
            a = str(sales_raw.iat[r, c]).strip().lower()
            b = str(sales_raw.iat[r, c + 1]).strip().lower()
            if a == "archetype" and b == "maturity" and c != 0:
                growth_header_row, growth_start_col = r, c
                break
        if growth_header_row is not None:
            break

    if growth_header_row is None:
        raise ValueError("Could not find growth table header ('Archetype' + 'Maturity').")

    growth_end_col = _scan_row_until_blank(sales_raw.iloc[growth_header_row].values, growth_start_col)

    growth_df = sales_raw.iloc[growth_header_row + 1 :, growth_start_col:growth_end_col].copy()
    growth_df.columns = sales_raw.iloc[growth_header_row, growth_start_col:growth_end_col].values
    growth_df = growth_df.dropna(subset=["Archetype", "Maturity"])

    growth_df["Archetype"] = _clean_text_series(growth_df["Archetype"])
    growth_df["Maturity"] = pd.to_numeric(growth_df["Maturity"], errors="coerce")

    growth_df = growth_df[~growth_df["Archetype"].apply(_is_bad_archetype_value)]
    growth_df = growth_df.dropna(subset=["Archetype", "Maturity"])

    year_map = _normalize_numeric_cols([c for c in growth_df.columns if c not in ["Archetype", "Maturity"]])
    years_needed = list(range(2, 11))  # 2..10

    return median_sales_df, growth_df, year_map, years_needed, needed_maturities

# =============================
# Lifecycle logic (same)
# =============================
def build_lifecycle_df(archetype, maturity, median_sales_df, growth_df, year_map, years_needed, yield_mean, conv_mean):
    # lookup median sales
    row = median_sales_df[median_sales_df["Archetype"] == archetype]
    if row.empty:
        return None, None, None
    val = row[maturity].dropna()
    if val.empty:
        return None, None, None
    y1 = float(val.iloc[0])

    # lookup yoy
    rowg = growth_df[(growth_df["Archetype"] == archetype) & (growth_df["Maturity"] == maturity)]
    if rowg.empty:
        return None, None, None

    yoy = []
    for y in years_needed:
        raw = rowg[year_map[y]].iloc[0] if y in year_map else np.nan
        yoy.append(_to_rate(raw))

    # Sales Year1..Year10
    sales = [y1]
    for rate in yoy:  # Year2..Year10
        next_sales = sales[-1] * (1 + rate)
        if next_sales < 0:
            next_sales = 0.0
        sales.append(next_sales)

    # Inventory lifecycle
    carryover = 0.0
    rows = []
    for yr in range(10):
        planned_prod = sales[yr + 1] if yr < 9 else 0.0  # planned = next yr sales
        new_prod = planned_prod * yield_mean * conv_mean
        prod_loss = new_prod * 0.02
        carry_loss = carryover * 0.10

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
    lifecycle_df = pd.DataFrame(
        np.array(rows).T,
        columns=cols,
        index=[
            "Carryover inventory from prior year",
            "Carryover quality loss (10%)",
            "Planned production (= next yr sales)",
            "New production (after yield & conv.)",
            "Production quality loss (2%)",
            "Total saleable inventory",
            "Sales",
            "Remaining inventory (carryover out)"
        ]
    )

    return cols, sales[:10], lifecycle_df

# =============================
# UI
# =============================
st.title("📊 Lifecycle Dashboard")

uploaded = st.file_uploader("Upload your Excel file", type=["xlsx"])
if not uploaded:
    st.info("Upload your Excel file to begin.")
    st.stop()

conv_tab, yield_tab, sales_raw = load_required_sheets(uploaded)

# Means (same)
yield_tab["Planned yield (bu/ac)"] = pd.to_numeric(yield_tab["Planned yield (bu/ac)"], errors="coerce")
yield_tab["Actual yield"] = pd.to_numeric(yield_tab["Actual yield"], errors="coerce")
yield_tab["Yield_Factor"] = yield_tab["Actual yield"] / yield_tab["Planned yield (bu/ac)"]
yield_mean = yield_tab["Yield_Factor"].replace([np.inf, -np.inf], np.nan).mean()

conv_tab["totalConversionRate"] = pd.to_numeric(conv_tab["totalConversionRate"], errors="coerce")
conv_mean = conv_tab["totalConversionRate"].mean()

k1, k2 = st.columns(2)
k1.metric("Yield factor (mean)", f"{yield_mean:.4f}")
k2.metric("Conversion rate (mean)", f"{conv_mean:.4f}")

tabs = st.tabs(["Inventory lifecycle", "Maturity distribution", "Production volume"])

# =============================
# TAB: Inventory lifecycle (main)
# =============================
with tabs[0]:
    st.subheader("Inventory lifecycle (Sales + Remaining inventory)")

    try:
        median_sales_df, growth_df, year_map, years_needed, maturities = parse_sales_volume_parameters(sales_raw)
    except Exception as e:
        st.error(f"Could not parse 'Sales volume parameters' sheet: {e}")
        st.stop()

    archetypes = sorted(median_sales_df["Archetype"].dropna().unique())
    arch = st.selectbox("Archetype", archetypes)
    mat = st.selectbox("Maturity", maturities)

    if st.button("Build lifecycle"):
        cols, sales, lifecycle_df = build_lifecycle_df(
            arch, mat, median_sales_df, growth_df, year_map, years_needed, yield_mean, conv_mean
        )
        if lifecycle_df is None:
            st.warning("No data found for this Archetype + Maturity combination.")
        else:
            st.dataframe(lifecycle_df.round(1), use_container_width=True)

            remaining = lifecycle_df.loc["Remaining inventory (carryover out)"].astype(float).values

            fig = go.Figure()
            fig.add_trace(go.Scatter(x=cols, y=sales, mode="lines+markers", name="Sales"))
            fig.add_trace(go.Scatter(x=cols, y=remaining, mode="lines+markers", name="Remaining inventory"))
            fig.update_layout(
                title=f"Inventory Lifecycle – {arch} | Maturity {mat}",
                xaxis_title="Year",
                yaxis_title="Volume",
                height=520
            )
            st.plotly_chart(fig, use_container_width=True)

# =============================
# TAB: Maturity distribution (simple)
# =============================
with tabs[1]:
    st.subheader("Maturity distribution")
    try:
        pp = load_product_params(uploaded)
        if "Trait" in pp.columns and "Maturity" in pp.columns:
            pp["Trait"] = pp["Trait"].astype(str).str.strip()
            pp["Maturity"] = pd.to_numeric(pp["Maturity"], errors="coerce")
            # bins
            bins = [0, 85, 95, 105, 115, 999]
            labels = ["≤85", "86-95", "96-105", "106-115", "116+"]
            pp["Maturity_Bin"] = pd.cut(pp["Maturity"], bins=bins, labels=labels, include_lowest=True)

            pivot = pp.groupby(["Trait", "Maturity_Bin"], observed=False).size().reset_index(name="Count")
            fig = px.bar(pivot, x="Trait", y="Count", color="Maturity_Bin", barmode="stack")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Product parameters sheet missing Trait/Maturity columns.")
    except Exception as e:
        st.info(f"Could not render maturity distribution: {e}")

# =============================
# TAB: Production volume (simple)
# =============================
with tabs[2]:
    st.subheader("Production volume")
    try:
        prod = load_production_yields(uploaded)
        pp = load_product_params(uploaded)

        # normalize likely columns
        if "Parent0" not in pp.columns:
            cand = [c for c in pp.columns if "parent" in str(c).lower()]
            if cand: pp = pp.rename(columns={cand[0]: "Parent0"})
        if "Trait" not in pp.columns:
            cand = [c for c in pp.columns if "trait" in str(c).lower()]
            if cand: pp = pp.rename(columns={cand[0]: "Trait"})
        if "Maturity" not in pp.columns:
            cand = [c for c in pp.columns if "maturity" in str(c).lower()]
            if cand: pp = pp.rename(columns={cand[0]: "Maturity"})

        pp["Maturity"] = pd.to_numeric(pp["Maturity"], errors="coerce")
        pp["Trait"] = pp["Trait"].astype(str).str.strip()

        for c in ["Qactual (bu)", "Actual yield", "area (ac)"]:
            if c in prod.columns:
                prod[c] = pd.to_numeric(prod[c], errors="coerce")

        master = prod.merge(pp[["Parent0", "Trait", "Maturity"]], on="Parent0", how="left")
        if "Qactual (bu)" in master.columns and master["Qactual (bu)"].notna().sum() > 0:
            master["Production_Volume"] = master["Qactual (bu)"]
        elif "area (ac)" in master.columns and "Actual yield" in master.columns:
            master["Production_Volume"] = master["area (ac)"] * master["Actual yield"]
        else:
            master["Production_Volume"] = np.nan

        master = master[master["Production_Volume"].notna()].copy()
        bins = [0, 85, 95, 105, 115, 999]
        labels = ["≤85", "86-95", "96-105", "106-115", "116+"]
        master["Maturity_Bin"] = pd.cut(master["Maturity"], bins=bins, labels=labels, include_lowest=True)

        agg = master.groupby(["Trait", "Maturity_Bin"], observed=False)["Production_Volume"].sum().reset_index()
        trait = st.selectbox("Trait", sorted(agg["Trait"].dropna().unique()))
        dfp = agg[agg["Trait"] == trait]

        fig = px.bar(dfp, x="Maturity_Bin", y="Production_Volume", color="Maturity_Bin")
        st.plotly_chart(fig, use_container_width=True)
    except Exception as e:
        st.info(f"Could not render production volume: {e}")
