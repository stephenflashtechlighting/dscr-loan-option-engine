import streamlit as st
import pandas as pd


def section_title(title: str, subtitle: str = "") -> None:
    st.markdown(f"### {title}")
    if subtitle:
        st.caption(subtitle)


def metric_row(items: list[tuple]) -> None:
    """items: list of (label, value, delta) tuples"""
    cols = st.columns(len(items))
    for col, (label, value, delta) in zip(cols, items):
        with col:
            st.metric(label, value, delta)


def scenario_summary_card(title: str, rows: list[tuple], highlight: bool = False) -> None:
    """Render a labeled key-value card."""
    border_color = "#2563eb" if highlight else "#e2e8f0"
    bg_color = "#eff6ff" if highlight else "#f8fafc"
    html = f"""
    <div style="border:1.5px solid {border_color}; border-radius:8px; padding:14px;
                background:{bg_color}; margin-bottom:8px;">
      <div style="font-weight:700; font-size:13px; color:#1e293b; margin-bottom:8px;">{title}</div>
    """
    for label, value in rows:
        html += f"""
      <div style="display:flex; justify-content:space-between; margin-bottom:4px;">
        <span style="font-size:11px; color:#64748b;">{label}</span>
        <span style="font-size:11px; font-weight:600; color:#1e293b;">{value}</span>
      </div>
        """
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)


def styled_dataframe(df: pd.DataFrame, currency_cols: list[str] = None, pct_cols: list[str] = None) -> None:
    """Render a dataframe with basic formatting."""
    if df.empty:
        st.info("No data to display.")
        return
    fmt = {}
    if currency_cols:
        for c in currency_cols:
            if c in df.columns:
                fmt[c] = "${:,.2f}"
    if pct_cols:
        for c in pct_cols:
            if c in df.columns:
                fmt[c] = "{:.3f}"
    st.dataframe(df.style.format(fmt), use_container_width=True)


def status_chip(label: str, color: str = "blue") -> str:
    colors = {
        "blue": ("#dbeafe", "#1d4ed8"),
        "green": ("#dcfce7", "#15803d"),
        "yellow": ("#fef9c3", "#a16207"),
        "red": ("#fee2e2", "#b91c1c"),
        "grey": ("#f1f5f9", "#475569"),
    }
    bg, fg = colors.get(color, colors["grey"])
    return f'<span style="background:{bg}; color:{fg}; padding:2px 8px; border-radius:12px; font-size:11px; font-weight:600;">{label}</span>'


def confidence_badge(level: str) -> str:
    mapping = {"high": "green", "medium": "yellow", "low": "red"}
    return status_chip(level.upper(), mapping.get(level, "grey"))


def extraction_review_row(field: str, value, confidence: str, key: str):
    """Render an editable extraction field with confidence badge."""
    col1, col2, col3 = st.columns([2, 3, 1])
    with col1:
        st.markdown(f"**{field}**", unsafe_allow_html=True)
    with col2:
        if isinstance(value, float):
            return st.number_input("", value=value, key=key, label_visibility="collapsed")
        elif isinstance(value, int):
            return st.number_input("", value=value, step=1, key=key, label_visibility="collapsed")
        else:
            return st.text_input("", value=str(value) if value else "", key=key, label_visibility="collapsed")
    with col3:
        st.markdown(confidence_badge(confidence), unsafe_allow_html=True)
    return None


def deal_card(deal, scenario_count: int) -> None:
    """Compact deal summary card for the home page."""
    html = f"""
    <div style="border:1px solid #e2e8f0; border-radius:8px; padding:14px 16px;
                background:#ffffff; margin-bottom:8px; cursor:pointer;">
      <div style="font-weight:700; font-size:14px; color:#1e293b;">{deal.deal_name}</div>
      <div style="font-size:12px; color:#64748b; margin-top:2px;">{deal.property_address or 'No address'}</div>
      <div style="display:flex; gap:16px; margin-top:8px;">
        <div><span style="font-size:11px; color:#94a3b8;">Purchase</span><br>
             <span style="font-size:13px; font-weight:600;">${deal.purchase_price:,.0f}</span></div>
        <div><span style="font-size:11px; color:#94a3b8;">Rent</span><br>
             <span style="font-size:13px; font-weight:600;">${deal.monthly_rent:,.0f}/mo</span></div>
        <div><span style="font-size:11px; color:#94a3b8;">Scenarios</span><br>
             <span style="font-size:13px; font-weight:600;">{scenario_count}</span></div>
        <div><span style="font-size:11px; color:#94a3b8;">Mode</span><br>
             <span style="font-size:13px; font-weight:600;">{deal.objective_mode}</span></div>
      </div>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)
