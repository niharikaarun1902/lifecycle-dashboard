import streamlit as st
import pandas as pd
import plotly.express as px

st.set_page_config(layout="wide")

st.title("📊 Lifecycle Dashboard")

uploaded = st.file_uploader("Upload your Excel file", type=["xlsx"])

if uploaded is None:
    st.info("Upload your data file to begin.")
    st.stop()

xls = pd.ExcelFile(uploaded)
sheet = st.selectbox("Select sheet", xls.sheet_names)
df = pd.read_excel(uploaded, sheet_name=sheet)

st.subheader("Data Preview")
st.dataframe(df)

cols = df.columns.tolist()

x_col = st.sidebar.selectbox("X axis", cols)
y_col = st.sidebar.selectbox("Y axis", cols)

fig = px.bar(df, x=x_col, y=y_col)
st.plotly_chart(fig, use_container_width=True)
