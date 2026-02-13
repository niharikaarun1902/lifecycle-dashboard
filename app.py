import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go

st.set_page_config(page_title="Lifecycle Dashboard", layout="wide")

# ----------------------------
# Constants
# ----------------------------
MATURITY_BINS   = [0, 85, 95, 105, 115, 999]
MATURITY_LABELS = ["≤85", "86-95", "96-105", "106-115", "116+"]

# ----------------------------
# Utilities
# ----------------------------
def maturity_bin(series: pd.Series) -> pd.Categorical:
    return pd.cut(series, bins=MATURITY_BINS, labels=MATURITY_LABELS, right=True, include_lowest=True)

def pick_col(df: pd.DataFrame, keywords, required=False):
    cols = list(df.columns)
    low = [str(c).lower().strip() for c in cols]
    for kw in keywords:
        kw = kw.lower().strip()
        for i, c in enumerate(low):
            if kw in c:
                return cols[i]
    if required:
        raise KeyError(f"Could not find column with keywords={keywords}. Available columns={cols}")
    return None

def clean_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = (
        df.columns.astype(str)
        .str.strip()
        .str.replace("\n", " ", regex=False)
        .str.replace("  ", " ", regex=False)
    )
    return df

def _to_rate(x):
    """Convert growth cell to decimal rate: '51.3%'->0.513, 51.3->0.513, 0.513->0.513."""
    if pd.isna(x):
        return 0.0
    if isinstance(x, str):
        xs = x.strip()
        if xs.endswith("%"):
            xs = xs[:-1].strip()
            v = pd.to_numeric(xs, errors="coerce")
            return 0.0 if pd.isna(v) else float(v) / 100.0
        v = pd.to_numeric(xs, errors="coerce")
        if pd.isna(v):
            return 0.0
        v = float(v)
        return v / 100.0 if abs(v) > 2 else v
    v = float(x)
    return v / 100.0 if abs(v) > 2 else v

@st.cache_data(show_spinner=False)
def load_workbook(uploaded_file):
    xls = pd.ExcelFile(uploaded_file)
    dfs = {name: pd.read_excel(uploaded_file, sheet_name=name) for name in xls.sheet_names}
    dfs = {name: clean_columns(df) for name, df in dfs.items()}
    return dfs

@st.cache_data(show_spinner=False)
def load_required_sheets(uploaded_file):
    conv_tab  = clean_columns(pd.read_excel(uploaded_file, sheet_name="Conversion rates"))
    yield_tab = clean_columns(pd.read_excel(uploaded_file, sheet_name="Production yields"))
    sales_raw = pd.read_excel(uploaded_file, sheet_name="Sales volume parameters", header=None)
    return conv_tab, yield_tab, sales_raw

def compute_yield_and_conv_means(conv_tab, yield_tab):
    # Yield factor = Actual yield / Planned yield (bu/ac)
    if "Planned yield (bu/ac)" in yield_tab.columns and "Actual yield" in yield_tab.columns:
        yield_tab["Planned yield (bu/ac)"] = pd.to_numeric(yield_tab["Planned yield (bu/ac)"], errors="coerce")
        yield_tab["Actual yield"] = pd.to_numeric(yield_tab["Actual yield"], errors="coerce")
        yield_tab["Yield_Factor"] = yield_tab["Actual yield"] / yield_tab["Planned yield (bu/ac)"]
        yield_mean = yield_tab["Yield_Factor"].replace([np.inf, -np.inf], np.nan).mean()
    else:
        yield_mean = np.nan

    # Conversion mean = totalConversionRate mean
    if "totalConversionRate" in conv_tab.columns:
        conv_tab["totalConversionRate"] = pd.to_numeric(conv_tab["totalConversionRate"], errors="coerce")
        conv_mean = conv_tab["totalConversionRate"].mean()
    else:
        conv_mean = np.nan

    return yield_mean, conv_mean

def build_master_for_production(dfs):
    params = dfs["Product parameters"].copy()
    prod   = dfs["Production yields"].copy()

    # robust cols
    PARENT_COL  = pick_col(params, ["parent0", "parent"], required=True)
    TRAIT_COL   = pick_col(params, ["trait"], required=True)
    MAT_COL     = pick_col(params, ["maturity"], required=True)
    ARCH_COL    = pick_col(params, ["archetype"], required=False)

    params[MAT_COL] = pd.to_numeric(params[MAT_COL], errors="coerce")
    params[TRAIT_COL] = params[TRAIT_COL].astype(str).str.strip()
    if ARCH_COL:
        params[ARCH_COL] = params[ARCH_COL].astype(str).str.strip()

    # numeric cols in prod
    for col in ["Qactual (bu)", "Actual yield", "area (ac)", "Planned yield (bu/ac)"]:
        if col in prod.columns:
            prod[col] = pd.to_numeric(prod[col], errors="coerce")

    # merge
    keep_cols = [PARENT_COL, TRAIT_COL, MAT_COL] + ([ARCH_COL] if ARCH_COL else [])
    prm = params[keep_cols].rename(columns={PARENT_COL: "Parent0", TRAIT_COL: "Trait", MAT_COL: "Maturity"})
    if ARCH_COL:
        prm = prm.rename(columns={ARCH_COL: "Archetype"})

    master = prod.merge(prm, on="Parent0", how="left")

    # production volume
    if "Qactual (bu)" in master.columns and master["Qactual (bu)"].notna().sum() > 0:
        master["Production_Volume"] = master["Qactual (bu)"]
    elif "area (ac)" in master.columns and "Actual yield" in master.columns:
        master["Production_Volume"] = master["area (ac)"] * master["Actual yield"]
    else:
        master["Production_Volume"] = np.nan

    master = master[master["Production_Volume"].notna()].copy()
    master["Maturity_Bin"] = maturity_bin(master["Maturity"])
    return master

# ----------------------------
# Sales tables parsing (UPDATED for your matrix format)
# ----------------------------
def find_header(sales_raw: pd.DataFrame, start_col_min=0):
    for r in range(sales_raw.shape[0]):
        for c in range(start_col_min, sales_raw.shape[1] - 1):
            a = str(sales_raw.iat[r, c]).strip().lower()
            b = str(sales_raw.iat[r, c + 1]).strip().lower()
            if a == "archetype" and b == "maturity":
                return r, c
    return None, None

def parse_sales_tables(sales_raw: pd.DataFrame):
    """
    Supports two formats for first-year sales:
    A) "Long" table: Archetype | Maturity | Median/Average first year sales volumes
    B) "Wide/matrix": Archetype | 85 | 95 | 105 | 115 | ...   (maturity columns)

    Also finds the YoY growth table: Archetype | Maturity | 2 | 3 | ... (years)
    """

    # ---------- Find a header row where col0 == "Archetype" ----------
    header_row = None
    for r in range(sales_raw.shape[0]):
        v = str(sales_raw.iat[r, 0]).strip().lower()
        if v == "archetype":
            header_row = r
            break
    if header_row is None:
        raise ValueError("Could not find 'Archetype' header in Sales volume parameters.")

    header = sales_raw.iloc[header_row].tolist()
    header = [str(x).strip() if not pd.isna(x) else "" for x in header]

    data = sales_raw.iloc[header_row + 1 :].copy()
    data.columns = header

    # Drop empty rows (must have Archetype)
    if "Archetype" not in data.columns:
        raise ValueError("Sales volume parameters: could not create table with 'Archetype' column.")
    data = data[data["Archetype"].notna()].copy()
    data["Archetype"] = data["Archetype"].astype(str).str.strip()

    # ---------- Detect maturity columns (wide matrix case) ----------
    maturity_cols = []
    for c in data.columns:
        if c in ["Archetype", "Maturity"]:
            continue
        try:
            float(c)  # columns like "85", "95.0", "105.0"
            maturity_cols.append(c)
        except:
            pass

    first_year_col = "FirstYearSales"
    first_tbl = None

    # ---------- Case A: long table ----------
    if "Maturity" in data.columns:
        candidates = [
            "Median first year sales volumes",
            "Average first year sales volumes",
            "First year sales volumes",
        ]
        fy = None
        for cand in candidates:
            if cand in data.columns:
                fy = cand
                break
        if fy is None:
            for c in data.columns:
                cl = str(c).lower()
                if "first year" in cl and "sales" in cl:
                    fy = c
                    break

        if fy is not None:
            first_tbl = data[["Archetype", "Maturity", fy]].copy()
            first_tbl["Maturity"] = pd.to_numeric(first_tbl["Maturity"], errors="coerce")
            first_tbl[fy] = pd.to_numeric(first_tbl[fy], errors="coerce")
            first_tbl = first_tbl.rename(columns={fy: first_year_col})

    # ---------- Case B: wide/matrix ----------
    if first_tbl is None:
        if len(maturity_cols) == 0:
            raise ValueError(f"Could not detect maturity columns for first-year matrix. Columns={list(data.columns)}")

        wide = data[["Archetype"] + maturity_cols].copy()
        for c in maturity_cols:
            wide[c] = pd.to_numeric(wide[c], errors="coerce")

        first_tbl = wide.melt(id_vars=["Archetype"], var_name="Maturity", value_name=first_year_col)
        first_tbl["Maturity"] = pd.to_numeric(first_tbl["Maturity"], errors="coerce")
        first_tbl = first_tbl.dropna(subset=[first_year_col, "Maturity"]).copy()

    # ---------- Find YoY growth table header: (Archetype, Maturity) somewhere later ----------
    yoy_header_row, yoy_start_col = None, None
    for r in range(header_row + 1, sales_raw.shape[0]):
        for c in range(sales_raw.shape[1] - 1):
            a = str(sales_raw.iat[r, c]).strip().lower()
            b = str(sales_raw.iat[r, c + 1]).strip().lower()
            if a == "archetype" and b == "maturity":
                yoy_header_row, yoy_start_col = r, c
                break
        if yoy_header_row is not None:
            break

    if yoy_header_row is None:
        raise ValueError("Could not find YoY growth table header (Archetype, Maturity).")

    yoy_cols = list(sales_raw.iloc[yoy_header_row, yoy_start_col:].values)
    yoy_cols = [str(x).strip() if not pd.isna(x) else "" for x in yoy_cols]

    yoy = sales_raw.iloc[yoy_header_row + 1 :, yoy_start_col:].copy()
    yoy.columns = yoy_cols
    yoy = yoy[yoy["Archetype"].notna()].copy()

    yoy["Archetype"] = yoy["Archetype"].astype(str).str.strip()
    yoy["Maturity"] = pd.to_numeric(yoy["Maturity"], errors="coerce")

    # year cols: numeric-ish like 2, 3, 4...
    year_cols = []
    for c in yoy.columns:
        if c in ["Archetype", "Maturity"]:
            continue
        try:
            float(c)
            year_cols.append(c)
        except:
            pass

    for c in year_cols:
        yoy[c] = yoy[c].apply(_to_rate)

    year_cols = sorted(year_cols, key=lambda x: float(x))
    return first_tbl, first_year_col, yoy, year_cols

def build_lifecycle(archetype, maturity, first_tbl, first_year_col, yoy_tbl, year_cols, yield_mean, conv_mean):
    row = first_tbl[(first_tbl["Archetype"] == archetype) & (first_tbl["Maturity"] == maturity)]
    if row.empty:
        return None

    y1 = float(row.iloc[0][first_year_col])
    grow = yoy_tbl[(yoy_tbl["Archetype"] == archetype) & (yoy_tbl["Maturity"] == maturity)]
    if grow.empty:
        return None

    rates = [float(grow.iloc[0][c]) for c in year_cols]
    sales = [y1]
    for rate in rates[1:]:
        nxt = sales[-1] * (1 + rate)
        sales.append(max(nxt, 0.0))

    sales = sales[:10] + [0.0] * max(0, 10 - len(sales))
    sales = sales[:10]

    # Inventory lifecycle logic
    carryover = 0.0
    rows = []
    for yr in range(10):
        planned_prod = sales[yr + 1] if yr < 9 else 0.0
        new_prod = planned_prod * float(yield_mean) * float(conv_mean)
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
        ],
    )
    return cols, sales, lifecycle_df

# ----------------------------
# UI
# ----------------------------
st.title("📊 Lifecycle Dashboard")

uploaded = st.file_uploader("Upload your Excel file", type=["xlsx"])
if not uploaded:
    st.info("Upload your Excel file to begin.")
    st.stop()

dfs = load_workbook(uploaded)
conv_tab, yield_tab, sales_raw = load_required_sheets(uploaded)

required = ["Product parameters", "Production yields", "Conversion rates", "Sales volume parameters"]
missing = [s for s in required if s not in dfs and s != "Sales volume parameters"]
if "Product parameters" not in dfs:
    st.error("Sheet 'Product parameters' not found. Upload the correct workbook.")
    st.stop()
if "Production yields" not in dfs:
    st.error("Sheet 'Production yields' not found. Upload the correct workbook.")
    st.stop()
if "Conversion rates" not in dfs:
    st.error("Sheet 'Conversion rates' not found. Upload the correct workbook.")
    st.stop()

yield_mean, conv_mean = compute_yield_and_conv_means(conv_tab, yield_tab)

k1, k2, k3 = st.columns(3)
k1.metric("Yield factor (mean)", f"{yield_mean:.4f}" if pd.notna(yield_mean) else "NA")
k2.metric("Conversion rate (mean)", f"{conv_mean:.4f}" if pd.notna(conv_mean) else "NA")
k3.metric("Sheets found", str(len(dfs)))

tabs = st.tabs(["Maturity distribution", "Production volume", "Inventory lifecycle"])

# ----------------------------
# Tab 1
# ----------------------------
with tabs[0]:
    df = dfs["Product parameters"].copy()
    df["Trait"] = df["Trait"].astype(str).str.strip()
    df["Maturity"] = pd.to_numeric(df["Maturity"], errors="coerce")
    if "Archetype" in df.columns:
        df["Archetype"] = df["Archetype"].astype(str).str.strip()

    df["Maturity_Bin"] = maturity_bin(df["Maturity"])

    left, right = st.columns([1, 1])

    with left:
        st.subheader("All Traits (stacked)")
        pivot = (
            df.groupby(["Trait", "Maturity_Bin"], observed=False)
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
        st.subheader("Top N Traits (stacked)")
        top_n = st.slider("Top N traits", 5, 50, 15, 1)
        top_traits = (
            df.groupby("Trait", observed=False)
              .size()
              .sort_values(ascending=False)
              .head(top_n)
              .index.tolist()
        )
        pivot_top = (
            df[df["Trait"].isin(top_traits)]
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

    if "Archetype" in df.columns:
        st.markdown("---")
        st.subheader("By Archetype")
        arch = st.selectbox("Select archetype", sorted(df["Archetype"].dropna().unique().tolist()))
        sub = df[df["Archetype"] == arch].copy()

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

# ----------------------------
# Tab 2
# ----------------------------
with tabs[1]:
    master = build_master_for_production(dfs)
    st.subheader("Production Volume")

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
            title=f"Production Volume — Trait: {trait} ({'All bins' if bin_choice=='All' else bin_choice})",
        )
        fig.update_layout(xaxis_title="Maturity Bin", yaxis_title="Total Production Volume (bu)")
        st.plotly_chart(fig, use_container_width=True)

    with colB:
        st.markdown("### Archetype × Maturity bin")
        if "Archetype" not in master.columns:
            st.info("No 'Archetype' column found after merge; skipping Archetype production view.")
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
                title=f"Production Volume — Archetype: {arch} ({'All bins' if bin_choice2=='All' else bin_choice2})",
            )
            fig2.update_layout(xaxis_title="Maturity Bin", yaxis_title="Total Production Volume (bu)")
            st.plotly_chart(fig2, use_container_width=True)

# ----------------------------
# Tab 3
# ----------------------------
with tabs[2]:
    st.subheader("Inventory lifecycle (Sales + Remaining inventory)")

    if pd.isna(yield_mean) or pd.isna(conv_mean):
        st.warning("Yield mean or conversion mean is missing (check 'Production yields' and 'Conversion rates' sheets).")

    try:
        first_tbl, first_year_col, yoy_tbl, year_cols = parse_sales_tables(sales_raw)
    except Exception as e:
        st.error(f"Could not parse 'Sales volume parameters' sheet: {e}")
        st.stop()

    arch_list = sorted(first_tbl["Archetype"].dropna().unique().tolist())
    arch = st.selectbox("Archetype", arch_list)

    mats = sorted(first_tbl[first_tbl["Archetype"] == arch]["Maturity"].dropna().unique().tolist())
    maturity = st.selectbox("Maturity", mats)

    if st.button("Build lifecycle"):
        res = build_lifecycle(arch, maturity, first_tbl, first_year_col, yoy_tbl, year_cols, yield_mean, conv_mean)
        if res is None:
            st.warning("No lifecycle data found for this Archetype + Maturity.")
        else:
            cols, sales, lifecycle_df = res

            st.markdown("### Lifecycle table")
            st.dataframe(lifecycle_df.round(1), use_container_width=True)

            remaining = lifecycle_df.loc["Remaining inventory (carryover out)"].astype(float).values

            fig = go.Figure()
            fig.add_trace(go.Scatter(x=cols, y=sales, mode="lines+markers", name="Sales"))
            fig.add_trace(go.Scatter(x=cols, y=remaining, mode="lines+markers", name="Remaining inventory"))
            fig.update_layout(
                title=f"Inventory Lifecycle — {arch} | Maturity {maturity}",
                xaxis_title="Year",
                yaxis_title="Volume",
                height=520,
            )
            st.plotly_chart(fig, use_container_width=True)

            st.caption(
                "Logic: planned production = next year sales; new production adjusted by mean yield factor × mean conversion; "
                "2% production loss; 10% carryover loss."
            )
