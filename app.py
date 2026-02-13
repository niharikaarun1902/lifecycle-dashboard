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
# Helpers
# ----------------------------
def maturity_bin(series: pd.Series) -> pd.Categorical:
    return pd.cut(series, bins=MATURITY_BINS, labels=MATURITY_LABELS, right=True, include_lowest=True)

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

def make_unique(cols):
    """Make duplicate column names unique: Archetype, Archetype__2, Archetype__3, ..."""
    seen = {}
    out = []
    for c in cols:
        c = "" if c is None else str(c).strip()
        if c == "":
            c = "blank"
        if c not in seen:
            seen[c] = 1
            out.append(c)
        else:
            seen[c] += 1
            out.append(f"{c}__{seen[c]}")
    return out

def is_numlike(s: str) -> bool:
    try:
        float(str(s).strip())
        return True
    except:
        return False

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

    if "totalConversionRate" in conv_tab.columns:
        conv_tab["totalConversionRate"] = pd.to_numeric(conv_tab["totalConversionRate"], errors="coerce")
        conv_mean = conv_tab["totalConversionRate"].mean()
    else:
        conv_mean = np.nan

    return yield_mean, conv_mean

def build_master_for_production(dfs):
    params = dfs["Product parameters"].copy()
    prod   = dfs["Production yields"].copy()

    # expect these exact names in your workbook
    # (your file already worked for other tabs, so we keep it simple)
    if "Parent0" not in params.columns:
        # fallback: try common variants
        parent = [c for c in params.columns if "parent" in c.lower()]
        if not parent:
            raise KeyError("Could not find Parent column in Product parameters.")
        params = params.rename(columns={parent[0]: "Parent0"})
    if "Trait" not in params.columns:
        trait = [c for c in params.columns if "trait" in c.lower()]
        if not trait:
            raise KeyError("Could not find Trait column in Product parameters.")
        params = params.rename(columns={trait[0]: "Trait"})
    if "Maturity" not in params.columns:
        mat = [c for c in params.columns if "maturity" in c.lower()]
        if not mat:
            raise KeyError("Could not find Maturity column in Product parameters.")
        params = params.rename(columns={mat[0]: "Maturity"})

    has_arch = "Archetype" in params.columns

    params["Maturity"] = pd.to_numeric(params["Maturity"], errors="coerce")
    params["Trait"] = params["Trait"].astype(str).str.strip()
    if has_arch:
        params["Archetype"] = params["Archetype"].astype(str).str.strip()

    for col in ["Qactual (bu)", "Actual yield", "area (ac)", "Planned yield (bu/ac)"]:
        if col in prod.columns:
            prod[col] = pd.to_numeric(prod[col], errors="coerce")

    keep = ["Parent0", "Trait", "Maturity"] + (["Archetype"] if has_arch else [])
    prm = params[keep].copy()

    master = prod.merge(prm, on="Parent0", how="left")

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
# Sales parsing (robust to duplicate headers)
# ----------------------------
def find_all_archetype_headers(sales_raw: pd.DataFrame):
    rows = []
    for r in range(sales_raw.shape[0]):
        if str(sales_raw.iat[r, 0]).strip().lower() == "archetype":
            rows.append(r)
    return rows

def parse_sales_tables(sales_raw: pd.DataFrame):
    """
    Your sheet structure (from errors):
    - First-year sales table is a MATRIX:
        Archetype | 85 | 95 | 105 | 115 | ...
    - YoY growth table exists somewhere below:
        Archetype | Maturity | 2 | 3 | ... | 15
    - Headers may repeat (duplicate labels). We make them unique to avoid pandas errors.
    """

    # 1) Find header rows where first cell == Archetype
    arch_header_rows = find_all_archetype_headers(sales_raw)
    if not arch_header_rows:
        raise ValueError("Could not find an 'Archetype' header row in Sales volume parameters.")

    # 2) First-year matrix is the FIRST Archetype header row
    fy_header_row = arch_header_rows[0]

    raw_header = sales_raw.iloc[fy_header_row].tolist()
    header = make_unique(raw_header)

    data = sales_raw.iloc[fy_header_row + 1 :].copy()
    data.columns = header

    # Ensure we use the first 'Archetype' column (could be Archetype__2 etc.)
    arch_col = "Archetype" if "Archetype" in data.columns else None
    if arch_col is None:
        # fallback: any col starting with Archetype
        candidates = [c for c in data.columns if str(c).startswith("Archetype")]
        if not candidates:
            raise ValueError("Could not locate an Archetype column in first-year table.")
        arch_col = candidates[0]

    data = data[data[arch_col].notna()].copy()
    data[arch_col] = data[arch_col].astype(str).str.strip()

    # First-year maturity columns are numeric column names
    maturity_cols = [c for c in data.columns if c != arch_col and is_numlike(c)]
    if not maturity_cols:
        raise ValueError(f"Could not detect maturity columns (85/95/105...) in first-year table. Columns={list(data.columns)}")

    wide = data[[arch_col] + maturity_cols].copy()
    for c in maturity_cols:
        wide[c] = pd.to_numeric(wide[c], errors="coerce")

    first_tbl = wide.melt(id_vars=[arch_col], var_name="Maturity", value_name="FirstYearSales")
    first_tbl = first_tbl.rename(columns={arch_col: "Archetype"})
    first_tbl["Maturity"] = pd.to_numeric(first_tbl["Maturity"], errors="coerce")
    first_tbl["FirstYearSales"] = pd.to_numeric(first_tbl["FirstYearSales"], errors="coerce")
    first_tbl = first_tbl.dropna(subset=["Archetype", "Maturity", "FirstYearSales"]).copy()

    # 3) YoY table: find a row with adjacent cells "Archetype" and "Maturity"
    yoy_header_row, yoy_start_col = None, None
    for r in range(fy_header_row + 1, sales_raw.shape[0]):
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

    yoy_raw_header = list(sales_raw.iloc[yoy_header_row, yoy_start_col:].values)
    yoy_header = make_unique(yoy_raw_header)

    yoy = sales_raw.iloc[yoy_header_row + 1 :, yoy_start_col:].copy()
    yoy.columns = yoy_header

    # Identify proper archetype/maturity columns (may be Archetype__2, Maturity__2)
    yoy_arch = "Archetype" if "Archetype" in yoy.columns else [c for c in yoy.columns if str(c).startswith("Archetype")][0]
    yoy_mat  = "Maturity"  if "Maturity"  in yoy.columns else [c for c in yoy.columns if str(c).startswith("Maturity")][0]

    yoy = yoy[yoy[yoy_arch].notna()].copy()
    yoy[yoy_arch] = yoy[yoy_arch].astype(str).str.strip()
    yoy[yoy_mat] = pd.to_numeric(yoy[yoy_mat], errors="coerce")
    yoy = yoy.rename(columns={yoy_arch: "Archetype", yoy_mat: "Maturity"})

    # Year columns are numeric-ish (2..15)
    year_cols = [c for c in yoy.columns if c not in ["Archetype", "Maturity"] and is_numlike(c)]
    if not year_cols:
        raise ValueError("Could not find YoY year columns (2..15).")

    # Convert rates
    for c in year_cols:
        yoy[c] = yoy[c].apply(_to_rate)

    year_cols = sorted(year_cols, key=lambda x: float(str(x)))
    return first_tbl, "FirstYearSales", yoy, year_cols

def build_lifecycle(archetype, maturity, first_tbl, first_year_col, yoy_tbl, year_cols, yield_mean, conv_mean):
    row = first_tbl[(first_tbl["Archetype"] == archetype) & (first_tbl["Maturity"] == maturity)]
    if row.empty:
        return None

    y1 = float(row.iloc[0][first_year_col])

    grow = yoy_tbl[(yoy_tbl["Archetype"] == archetype) & (yoy_tbl["Maturity"] == maturity)]
    if grow.empty:
        return None

    # Build sales for 10 years: Year1 + compounded using YoY rates columns (2..15)
    rates = [float(grow.iloc[0][c]) for c in year_cols]
    sales = [y1]
    # Use rates starting from year 2 onward
    for rate in rates[1:]:
        sales.append(max(sales[-1] * (1 + rate), 0.0))
    sales = (sales[:10] + [0.0] * 10)[:10]

    carryover = 0.0
    rows = []
    for yr in range(10):
        planned_prod = sales[yr + 1] if yr < 9 else 0.0  # planned production = next year sales
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

# Validate required sheets for other tabs
for sname in ["Product parameters", "Production yields", "Conversion rates"]:
    if sname not in dfs:
        st.error(f"Sheet '{sname}' not found. Upload the correct workbook.")
        st.stop()

yield_mean, conv_mean = compute_yield_and_conv_means(conv_tab, yield_tab)

k1, k2, k3 = st.columns(3)
k1.metric("Yield factor (mean)", f"{yield_mean:.4f}" if pd.notna(yield_mean) else "NA")
k2.metric("Conversion rate (mean)", f"{conv_mean:.4f}" if pd.notna(conv_mean) else "NA")
k3.metric("Sheets found", str(len(dfs)))

tabs = st.tabs(["Maturity distribution", "Production volume", "Inventory lifecycle"])

# ----------------------------
# Tab 1: Maturity distribution
# ----------------------------
with tabs[0]:
    df = dfs["Product parameters"].copy()

    # Normalize column names if needed
    if "Trait" not in df.columns:
        trait = [c for c in df.columns if "trait" in c.lower()]
        if trait: df = df.rename(columns={trait[0]: "Trait"})
    if "Maturity" not in df.columns:
        mat = [c for c in df.columns if "maturity" in c.lower()]
        if mat: df = df.rename(columns={mat[0]: "Maturity"})

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
# Tab 2: Production volume
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
# Tab 3: Inventory lifecycle
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
