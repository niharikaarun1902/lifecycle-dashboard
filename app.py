import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px

st.set_page_config(page_title="Lifecycle Dashboard", layout="wide")

# =============================
# Constants for binning maturity
# =============================
MATURITY_BINS   = [0, 85, 95, 105, 115, 999]
MATURITY_LABELS = ["≤85", "86-95", "96-105", "106-115", "116+"]

def maturity_bin(series: pd.Series) -> pd.Categorical:
    return pd.cut(series, bins=MATURITY_BINS, labels=MATURITY_LABELS, right=True, include_lowest=True)

# =============================
# HELPERS (same as your notebook for lifecycle)
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
    conv_tab   = pd.read_excel(uploaded_file, sheet_name="Conversion rates")
    yield_tab  = pd.read_excel(uploaded_file, sheet_name="Production yields")
    params_tab = pd.read_excel(uploaded_file, sheet_name="Product parameters")
    sales_raw  = pd.read_excel(uploaded_file, sheet_name="Sales volume parameters", header=None)

    # clean col names like notebook
    for df_ in [conv_tab, yield_tab, params_tab]:
        df_.columns = df_.columns.astype(str).str.strip()

    return conv_tab, yield_tab, params_tab, sales_raw

@st.cache_data(show_spinner=False)
def load_product_params(uploaded_file):
    df = pd.read_excel(uploaded_file, sheet_name="Product parameters")
    df.columns = df.columns.astype(str).str.strip()
    return df

@st.cache_data(show_spinner=False)
def load_production_yields(uploaded_file):
    df = pd.read_excel(uploaded_file, sheet_name="Production yields")
    df.columns = df.columns.astype(str).str.strip()
    return df

# =============================
# Parse Sales volume parameters (EXACT notebook logic you had working)
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
# Archetype-specific yield + conversion
# =============================
def prep_yield_conv_with_archetype(conv_tab, yield_tab, params_tab):
    # mapping Parent0 -> Archetype
    if "Parent0" not in params_tab.columns or "Archetype" not in params_tab.columns:
        raise ValueError("Product parameters must contain columns: Parent0, Archetype")

    params_tab = params_tab.copy()
    params_tab["Parent0"] = params_tab["Parent0"].astype(str).str.strip()
    params_tab["Archetype"] = params_tab["Archetype"].astype(str).str.strip()
    parent_to_arch = params_tab[["Parent0", "Archetype"]].dropna()

    # Yield_Factor per Parent0
    for col in ["Parent0", "Planned yield (bu/ac)", "Actual yield"]:
        if col not in yield_tab.columns:
            raise ValueError(f"Production yields sheet missing column: {col}")

    y = yield_tab.copy()
    y["Parent0"] = y["Parent0"].astype(str).str.strip()
    y["Planned yield (bu/ac)"] = pd.to_numeric(y["Planned yield (bu/ac)"], errors="coerce")
    y["Actual yield"] = pd.to_numeric(y["Actual yield"], errors="coerce")
    y["Yield_Factor"] = y["Actual yield"] / y["Planned yield (bu/ac)"]
    y["Yield_Factor"] = y["Yield_Factor"].replace([np.inf, -np.inf], np.nan)
    yield_w_arch = y.merge(parent_to_arch, on="Parent0", how="left")

    # Conversion per Parent0
    for col in ["Parent0", "totalConversionRate"]:
        if col not in conv_tab.columns:
            raise ValueError(f"Conversion rates sheet missing column: {col}")

    c = conv_tab.copy()
    c["Parent0"] = c["Parent0"].astype(str).str.strip()
    c["totalConversionRate"] = pd.to_numeric(c["totalConversionRate"], errors="coerce")
    conv_w_arch = c.merge(parent_to_arch, on="Parent0", how="left")

    # overall fallback means
    fallback_y = float(yield_w_arch["Yield_Factor"].dropna().mean())
    fallback_c = float(conv_w_arch["totalConversionRate"].dropna().mean())

    return yield_w_arch, conv_w_arch, fallback_y, fallback_c

def get_archetype_means(archetype, yield_w_arch, conv_w_arch, fallback_y, fallback_c):
    y = yield_w_arch.loc[yield_w_arch["Archetype"] == archetype, "Yield_Factor"].dropna()
    c = conv_w_arch.loc[conv_w_arch["Archetype"] == archetype, "totalConversionRate"].dropna()

    y_mean = float(y.mean()) if len(y) else float(fallback_y)
    c_mean = float(c.mean()) if len(c) else float(fallback_c)
    return y_mean, c_mean

# =============================
# Lifecycle logic
# =============================
def build_lifecycle_df(archetype, maturity, median_sales_df, growth_df, year_map, years_needed,
                      yield_w_arch, conv_w_arch, fallback_y, fallback_c):
    # median sales lookup
    row = median_sales_df[median_sales_df["Archetype"] == archetype]
    if row.empty:
        return None, None, None, None, None
    val = row[maturity].dropna()
    if val.empty:
        return None, None, None, None, None
    y1 = float(val.iloc[0])

    # yoy lookup
    rowg = growth_df[(growth_df["Archetype"] == archetype) & (growth_df["Maturity"] == maturity)]
    if rowg.empty:
        return None, None, None, None, None

    yoy = []
    for y in years_needed:
        raw = rowg[year_map[y]].iloc[0] if y in year_map else np.nan
        yoy.append(_to_rate(raw))

    # archetype-specific yield/conv
    y_mean, c_mean = get_archetype_means(archetype, yield_w_arch, conv_w_arch, fallback_y, fallback_c)

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
        new_prod = planned_prod * y_mean * c_mean
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

    return cols, sales[:10], lifecycle_df, y_mean, c_mean

# =============================
# PRODUCTION MASTER
# =============================
def build_master_for_production(prod: pd.DataFrame, pp: pd.DataFrame) -> pd.DataFrame:
    if "Parent0" not in pp.columns:
        cand = [c for c in pp.columns if "parent" in str(c).lower()]
        if cand:
            pp = pp.rename(columns={cand[0]: "Parent0"})
    if "Trait" not in pp.columns:
        cand = [c for c in pp.columns if "trait" in str(c).lower()]
        if cand:
            pp = pp.rename(columns={cand[0]: "Trait"})
    if "Maturity" not in pp.columns:
        cand = [c for c in pp.columns if "maturity" in str(c).lower()]
        if cand:
            pp = pp.rename(columns={cand[0]: "Maturity"})

    pp["Trait"] = pp["Trait"].astype(str).str.strip()
    pp["Maturity"] = pd.to_numeric(pp["Maturity"], errors="coerce")
    if "Archetype" in pp.columns:
        pp["Archetype"] = pp["Archetype"].astype(str).str.strip()

    for c in ["Qactual (bu)", "Actual yield", "area (ac)"]:
        if c in prod.columns:
            prod[c] = pd.to_numeric(prod[c], errors="coerce")

    keep_cols = ["Parent0", "Trait", "Maturity"] + (["Archetype"] if "Archetype" in pp.columns else [])
    master = prod.merge(pp[keep_cols], on="Parent0", how="left")

    if "Qactual (bu)" in master.columns and master["Qactual (bu)"].notna().sum() > 0:
        master["Production_Volume"] = master["Qactual (bu)"]
    elif "area (ac)" in master.columns and "Actual yield" in master.columns:
        master["Production_Volume"] = master["area (ac)"] * master["Actual yield"]
    else:
        master["Production_Volume"] = np.nan

    master = master[master["Production_Volume"].notna()].copy()
    master["Maturity_Bin"] = maturity_bin(master["Maturity"])
    return master

# =============================
# UI
# =============================
st.title("📊 Lifecycle Dashboard")

uploaded = st.file_uploader("Upload your Excel file", type=["xlsx"])
if not uploaded:
    st.info("Upload your Excel file to begin.")
    st.stop()

conv_tab, yield_tab, params_tab, sales_raw = load_required_sheets(uploaded)

# prep archetype-specific yield & conversion
yield_w_arch, conv_w_arch, fallback_y, fallback_c = prep_yield_conv_with_archetype(conv_tab, yield_tab, params_tab)

# ✅ Removed top KPI cards (overall mean) as requested

tabs = st.tabs(["Maturity distribution", "Production volume", "Inventory lifecycle"])

# =============================
# TAB: Maturity distribution
# =============================
with tabs[0]:
    st.subheader("Maturity distribution")

    pp = load_product_params(uploaded)

    if "Trait" not in pp.columns:
        cand = [c for c in pp.columns if "trait" in str(c).lower()]
        if cand: pp = pp.rename(columns={cand[0]: "Trait"})
    if "Maturity" not in pp.columns:
        cand = [c for c in pp.columns if "maturity" in str(c).lower()]
        if cand: pp = pp.rename(columns={cand[0]: "Maturity"})

    pp["Trait"] = pp["Trait"].astype(str).str.strip()
    pp["Maturity"] = pd.to_numeric(pp["Maturity"], errors="coerce")
    if "Archetype" in pp.columns:
        pp["Archetype"] = pp["Archetype"].astype(str).str.strip()

    pp["Maturity_Bin"] = maturity_bin(pp["Maturity"])

    left, right = st.columns(2)

    with left:
        st.markdown("### All Traits (stacked)")
        pivot = (
            pp.groupby(["Trait", "Maturity_Bin"], observed=False)
              .size()
              .reset_index(name="Count")
        )
        fig = px.bar(
            pivot,
            x="Trait",
            y="Count",
            color="Maturity_Bin",
            barmode="stack",
            category_orders={"Maturity_Bin": MATURITY_LABELS},
            height=520,
        )
        st.plotly_chart(fig, use_container_width=True)

    with right:
        st.markdown("### Top N Traits (stacked)")
        top_n = st.slider("Top N traits", 5, 50, 15, 1)
        top_traits = (
            pp.groupby("Trait", observed=False)
              .size()
              .sort_values(ascending=False)
              .head(top_n)
              .index.tolist()
        )
        pivot_top = (
            pp[pp["Trait"].isin(top_traits)]
            .groupby(["Trait", "Maturity_Bin"], observed=False)
            .size()
            .reset_index(name="Count")
        )
        fig2 = px.bar(
            pivot_top,
            x="Trait",
            y="Count",
            color="Maturity_Bin",
            barmode="stack",
            category_orders={"Trait": top_traits, "Maturity_Bin": MATURITY_LABELS},
            height=520,
        )
        st.plotly_chart(fig2, use_container_width=True)

    if "Archetype" in pp.columns:
        st.markdown("---")
        st.markdown("### By Archetype")
        arch = st.selectbox("Select archetype", sorted(pp["Archetype"].dropna().unique().tolist()))
        sub = pp[pp["Archetype"] == arch].copy()

        pivot_arch = (
            sub.groupby(["Trait", "Maturity_Bin"], observed=False)
               .size()
               .reset_index(name="Count")
        )
        fig3 = px.bar(
            pivot_arch,
            x="Trait",
            y="Count",
            color="Maturity_Bin",
            barmode="stack",
            category_orders={"Maturity_Bin": MATURITY_LABELS},
            height=520,
            title=f"Maturity distribution — {arch}",
        )
        st.plotly_chart(fig3, use_container_width=True)
    else:
        st.info("No 'Archetype' column found in Product parameters; skipping Archetype view.")

# =============================
# TAB: Production volume
# =============================
with tabs[1]:
    st.subheader("Production volume")

    prod = load_production_yields(uploaded)
    pp = load_product_params(uploaded)

    try:
        master = build_master_for_production(prod, pp)
    except Exception as e:
        st.error(f"Could not build production master table: {e}")
        st.stop()

    colA, colB = st.columns(2)

    with colA:
        st.markdown("### Trait × Maturity bin")
        agg_trait = (
            master.groupby(["Trait", "Maturity_Bin"], observed=False)["Production_Volume"]
                  .sum()
                  .reset_index()
        )

        trait_list = sorted(agg_trait["Trait"].dropna().unique().tolist())
        trait = st.selectbox("Trait", trait_list)

        bin_choice = st.selectbox("Maturity bin", ["All"] + MATURITY_LABELS)

        dfp = agg_trait[agg_trait["Trait"] == trait].copy()
        if bin_choice != "All":
            dfp = dfp[dfp["Maturity_Bin"] == bin_choice]

        fig = px.bar(
            dfp,
            x="Maturity_Bin",
            y="Production_Volume",
            color="Maturity_Bin" if bin_choice == "All" else None,
            category_orders={"Maturity_Bin": MATURITY_LABELS},
            height=520,
            title=f"Production Volume — Trait: {trait}",
        )
        fig.update_layout(xaxis_title="Maturity Bin", yaxis_title="Total Production Volume (bu)")
        st.plotly_chart(fig, use_container_width=True)

    with colB:
        st.markdown("### Archetype × Maturity bin")
        if "Archetype" not in master.columns:
            st.info("No 'Archetype' column found in Product parameters; skipping Archetype chart.")
        else:
            agg_arch = (
                master.groupby(["Archetype", "Maturity_Bin"], observed=False)["Production_Volume"]
                      .sum()
                      .reset_index()
            )
            arch_list = sorted(agg_arch["Archetype"].dropna().unique().tolist())
            arch = st.selectbox("Archetype", arch_list)

            bin_choice2 = st.selectbox("Maturity bin (Archetype)", ["All"] + MATURITY_LABELS)

            dfp2 = agg_arch[agg_arch["Archetype"] == arch].copy()
            if bin_choice2 != "All":
                dfp2 = dfp2[dfp2["Maturity_Bin"] == bin_choice2]

            fig2 = px.bar(
                dfp2,
                x="Maturity_Bin",
                y="Production_Volume",
                color="Maturity_Bin" if bin_choice2 == "All" else None,
                category_orders={"Maturity_Bin": MATURITY_LABELS},
                height=520,
                title=f"Production Volume — Archetype: {arch}",
            )
            fig2.update_layout(xaxis_title="Maturity Bin", yaxis_title="Total Production Volume (bu)")
            st.plotly_chart(fig2, use_container_width=True)

# =============================
# TAB: Inventory lifecycle
# =============================
with tabs[2]:
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
        cols, sales, lifecycle_df, y_mean, c_mean = build_lifecycle_df(
            arch, mat, median_sales_df, growth_df, year_map, years_needed,
            yield_w_arch, conv_w_arch, fallback_y, fallback_c
        )
        if lifecycle_df is None:
            st.warning("No data found for this Archetype + Maturity combination.")
        else:
            # ✅ Show big KPI cards here (instead of st.info line)
            c1, c2 = st.columns(2)
            c1.metric("Yield factor (Archetype mean)", f"{y_mean:.4f}")
            c2.metric("Conversion rate (Archetype mean)", f"{c_mean:.4f}")

            st.dataframe(lifecycle_df.round(1), use_container_width=True)

            remaining = lifecycle_df.loc["Remaining inventory (carryover out)"].astype(float).values

            fig = go.Figure()
            fig.add_trace(go.Scatter(x=cols, y=sales, mode="lines+markers", name="Sales"))
            fig.add_trace(go.Scatter(x=cols, y=remaining, mode="lines+markers", name="Remaining inventory"))
            fig.update_layout(
                title=f"Inventory Lifecycle – {arch} | Maturity {mat} (Archetype-specific Yield & Conv)",
                xaxis_title="Year",
                yaxis_title="Volume",
                height=520
            )
            st.plotly_chart(fig, use_container_width=True)
