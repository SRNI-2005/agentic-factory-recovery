"""Central theme & CSS injection for the COE Factory Recovery dashboard.

Provides a dark glassmorphism aesthetic with animated accents,
gradient KPI cards, and professional typography.
"""
from __future__ import annotations

# ---------- colour palette ----------
NAVY = "#0a0e27"
NAVY_LIGHT = "#111638"
CARD_BG = "rgba(17, 22, 56, 0.7)"
CARD_BORDER = "rgba(99, 130, 255, 0.15)"
ACCENT_BLUE = "#6366f1"
ACCENT_CYAN = "#22d3ee"
ACCENT_EMERALD = "#10b981"
ACCENT_AMBER = "#f59e0b"
ACCENT_ROSE = "#f43f5e"
TEXT_PRIMARY = "#e2e8f0"
TEXT_SECONDARY = "#94a3b8"
TEXT_MUTED = "#64748b"
GLASS_BG = "rgba(17, 22, 56, 0.55)"
GLASS_BORDER = "rgba(99, 130, 255, 0.18)"

# ---------- Plotly figure template ----------
PLOTLY_TEMPLATE = dict(
    layout=dict(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(17, 22, 56, 0.4)",
        font=dict(color=TEXT_PRIMARY, family="Inter, sans-serif"),
        xaxis=dict(gridcolor="rgba(99,130,255,0.08)", zerolinecolor="rgba(99,130,255,0.12)"),
        yaxis=dict(gridcolor="rgba(99,130,255,0.08)", zerolinecolor="rgba(99,130,255,0.12)"),
        colorway=[ACCENT_BLUE, ACCENT_CYAN, ACCENT_EMERALD, ACCENT_AMBER, ACCENT_ROSE,
                  "#8b5cf6", "#ec4899", "#14b8a6", "#f97316", "#a78bfa"],
        margin=dict(l=40, r=20, t=50, b=40),
    )
)


def apply_plotly_theme(fig):
    """Apply the COE dark theme to any Plotly figure."""
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(17, 22, 56, 0.4)",
        font=dict(color=TEXT_PRIMARY, family="Inter, sans-serif"),
        xaxis=dict(gridcolor="rgba(99,130,255,0.08)", zerolinecolor="rgba(99,130,255,0.12)"),
        yaxis=dict(gridcolor="rgba(99,130,255,0.08)", zerolinecolor="rgba(99,130,255,0.12)"),
        margin=dict(l=40, r=20, t=50, b=40),
    )
    return fig


# ---------- CSS ----------

GLOBAL_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&family=JetBrains+Mono:wght@400;500&display=swap');

/* ---- root ---- */
:root {
    --navy: #0a0e27;
    --navy-light: #111638;
    --accent-blue: #6366f1;
    --accent-cyan: #22d3ee;
    --accent-emerald: #10b981;
    --accent-amber: #f59e0b;
    --accent-rose: #f43f5e;
    --text-primary: #e2e8f0;
    --text-secondary: #94a3b8;
    --glass-bg: rgba(17, 22, 56, 0.55);
    --glass-border: rgba(99, 130, 255, 0.18);
    --card-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
}

/* ---- main background ---- */
.stApp, [data-testid="stAppViewContainer"] {
    background: linear-gradient(135deg, #0a0e27 0%, #111638 40%, #0f1535 70%, #0a0e27 100%) !important;
    color: var(--text-primary) !important;
    font-family: 'Inter', sans-serif !important;
}
.main .block-container {
    background: transparent !important;
    padding-top: 2rem !important;
    padding-bottom: 3rem !important;
    max-width: 95% !important;
}

/* ---- sidebar ---- */
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #0d1230 0%, #0a0e27 100%) !important;
    border-right: 1px solid var(--glass-border) !important;
}
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] span,
[data-testid="stSidebar"] label {
    color: var(--text-primary);
}
[data-testid="stSidebar"] .stSelectbox label,
[data-testid="stSidebar"] .stRadio label {
    color: var(--text-secondary) !important;
    font-weight: 500 !important;
}

/* ---- hide streamlit defaults ---- */
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
.stDeployButton {display: none !important;}
[data-testid="stDecoration"] {display: none !important;}
header[data-testid="stHeader"] {
    background: transparent !important;
}

/* ---- headings ---- */
h1 {
    background: linear-gradient(135deg, #6366f1 0%, #22d3ee 50%, #10b981 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    font-weight: 800 !important;
    font-size: 2.2rem !important;
    letter-spacing: -0.02em;
}
h2 {
    color: #e2e8f0 !important;
    font-weight: 700 !important;
    font-size: 1.5rem !important;
    border-bottom: 2px solid rgba(99, 130, 255, 0.2);
    padding-bottom: 0.4rem;
}
h3 {
    color: #cbd5e1 !important;
    font-weight: 600 !important;
}

/* ---- metric cards ---- */
[data-testid="stMetric"] {
    background: var(--glass-bg) !important;
    backdrop-filter: blur(12px) !important;
    border: 1px solid var(--glass-border) !important;
    border-radius: 16px !important;
    padding: 1.2rem 1.4rem !important;
    box-shadow: var(--card-shadow) !important;
    transition: transform 0.25s ease, box-shadow 0.25s ease !important;
}
[data-testid="stMetric"]:hover {
    transform: translateY(-3px) !important;
    box-shadow: 0 12px 40px rgba(99, 102, 241, 0.15) !important;
}
[data-testid="stMetric"] label {
    color: var(--text-secondary) !important;
    font-weight: 500 !important;
    font-size: 0.85rem !important;
    text-transform: uppercase !important;
    letter-spacing: 0.05em !important;
}
[data-testid="stMetric"] [data-testid="stMetricValue"] {
    color: var(--text-primary) !important;
    font-weight: 700 !important;
    font-size: 1.8rem !important;
}
[data-testid="stMetric"] [data-testid="stMetricDelta"] {
    font-weight: 600 !important;
}

/* ---- tabs ---- */
.stTabs [data-baseweb="tab-list"] {
    background: rgba(17, 22, 56, 0.4) !important;
    border-radius: 12px !important;
    padding: 4px !important;
    gap: 4px !important;
    border: 1px solid var(--glass-border) !important;
}
.stTabs [data-baseweb="tab"] {
    border-radius: 8px !important;
    color: var(--text-secondary) !important;
    font-weight: 500 !important;
    padding: 8px 20px !important;
    transition: all 0.2s ease !important;
}
.stTabs [data-baseweb="tab"][aria-selected="true"] {
    background: linear-gradient(135deg, rgba(99,102,241,0.3), rgba(34,211,238,0.2)) !important;
    color: var(--text-primary) !important;
    font-weight: 600 !important;
}
.stTabs [data-baseweb="tab-highlight"] {
    background: transparent !important;
}
.stTabs [data-baseweb="tab-border"] {
    display: none !important;
}

/* ---- buttons ---- */
.stButton > button {
    background: linear-gradient(135deg, #6366f1 0%, #4f46e5 100%) !important;
    color: white !important;
    border: none !important;
    border-radius: 10px !important;
    padding: 0.6rem 1.6rem !important;
    font-weight: 600 !important;
    font-size: 0.9rem !important;
    transition: all 0.3s ease !important;
    box-shadow: 0 4px 15px rgba(99, 102, 241, 0.3) !important;
}
.stButton > button:hover {
    transform: translateY(-2px) !important;
    box-shadow: 0 6px 20px rgba(99, 102, 241, 0.5) !important;
}
.stButton > button:active {
    transform: translateY(0) !important;
}

/* ---- danger buttons ---- */
.danger-btn > button {
    background: linear-gradient(135deg, #f43f5e 0%, #e11d48 100%) !important;
    box-shadow: 0 4px 15px rgba(244, 63, 94, 0.3) !important;
}
.danger-btn > button:hover {
    box-shadow: 0 6px 20px rgba(244, 63, 94, 0.5) !important;
}

/* ---- success buttons ---- */
.success-btn > button {
    background: linear-gradient(135deg, #10b981 0%, #059669 100%) !important;
    box-shadow: 0 4px 15px rgba(16, 185, 129, 0.3) !important;
}
.success-btn > button:hover {
    box-shadow: 0 6px 20px rgba(16, 185, 129, 0.5) !important;
}

/* ---- dataframes ---- */
[data-testid="stDataFrame"] {
    background: var(--glass-bg) !important;
    border: 1px solid var(--glass-border) !important;
    border-radius: 12px !important;
    overflow: hidden !important;
}
[data-testid="stDataFrame"] th {
    background: rgba(99, 102, 241, 0.15) !important;
    color: var(--text-primary) !important;
    font-weight: 600 !important;
}

/* ---- expanders ---- */
.streamlit-expanderHeader {
    background: var(--glass-bg) !important;
    border: 1px solid var(--glass-border) !important;
    border-radius: 12px !important;
    color: var(--text-primary) !important;
    font-weight: 500 !important;
}

/* ---- selectbox ---- */
[data-baseweb="select"] {
    background: rgba(17, 22, 56, 0.6) !important;
}
[data-baseweb="select"] * {
    color: var(--text-primary) !important;
}

/* ---- chat ---- */
[data-testid="stChatMessage"] {
    background: var(--glass-bg) !important;
    border: 1px solid var(--glass-border) !important;
    border-radius: 16px !important;
    backdrop-filter: blur(12px) !important;
}

/* ---- status/spinner ---- */
[data-testid="stStatusWidget"] {
    background: var(--glass-bg) !important;
    border: 1px solid var(--glass-border) !important;
    border-radius: 12px !important;
}

/* ---- file uploader ---- */
[data-testid="stFileUploader"] {
    background: var(--glass-bg) !important;
    border: 1px solid var(--glass-border) !important;
    border-radius: 12px !important;
}

/* ---- text input ---- */
.stTextInput input, .stTextArea textarea {
    background: rgba(17, 22, 56, 0.6) !important;
    color: var(--text-primary) !important;
    border: 1px solid var(--glass-border) !important;
    border-radius: 10px !important;
}

/* ---- chat input ---- */
[data-testid="stChatInput"] textarea {
    background: rgba(17, 22, 56, 0.6) !important;
    color: var(--text-primary) !important;
    border: 1px solid var(--glass-border) !important;
    border-radius: 12px !important;
}

/* ---- scrollbar ---- */
::-webkit-scrollbar {width: 6px; height: 6px;}
::-webkit-scrollbar-track {background: transparent;}
::-webkit-scrollbar-thumb {background: rgba(99, 130, 255, 0.3); border-radius: 3px;}
::-webkit-scrollbar-thumb:hover {background: rgba(99, 130, 255, 0.5);}

/* ---- divider ---- */
hr {border-color: rgba(99, 130, 255, 0.12) !important;}

/* ---- pulse animation ---- */
@keyframes pulse {
    0% {box-shadow: 0 0 0 0 rgba(99, 102, 241, 0.4);}
    70% {box-shadow: 0 0 0 10px rgba(99, 102, 241, 0);}
    100% {box-shadow: 0 0 0 0 rgba(99, 102, 241, 0);}
}

@keyframes slideUp {
    from {opacity: 0; transform: translateY(20px);}
    to {opacity: 1; transform: translateY(0);}
}

@keyframes glow {
    0%, 100% {box-shadow: 0 0 5px rgba(99, 102, 241, 0.3);}
    50% {box-shadow: 0 0 20px rgba(99, 102, 241, 0.6), 0 0 40px rgba(34, 211, 238, 0.2);}
}

@keyframes shimmer {
    0% {background-position: -200% center;}
    100% {background-position: 200% center;}
}

/* ---- glass card container ---- */
.glass-card {
    background: var(--glass-bg);
    backdrop-filter: blur(12px);
    border: 1px solid var(--glass-border);
    border-radius: 16px;
    padding: 1.5rem;
    box-shadow: var(--card-shadow);
    animation: slideUp 0.5s ease;
}

/* ---- gradient KPI ---- */
.kpi-gradient {
    background: linear-gradient(135deg, rgba(99,102,241,0.15), rgba(34,211,238,0.1));
    border: 1px solid rgba(99,102,241,0.2);
    border-radius: 16px;
    padding: 1.5rem;
    text-align: center;
}
.kpi-gradient .kpi-value {
    font-size: 2.5rem;
    font-weight: 800;
    background: linear-gradient(135deg, #6366f1, #22d3ee);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
}
.kpi-gradient .kpi-label {
    color: #94a3b8;
    font-size: 0.85rem;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    margin-top: 0.3rem;
}

/* ---- status badges ---- */
.status-badge {
    display: inline-block;
    padding: 4px 12px;
    border-radius: 20px;
    font-size: 0.78rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}
.status-badge.operational {
    background: rgba(16, 185, 129, 0.15);
    color: #10b981;
    border: 1px solid rgba(16, 185, 129, 0.3);
}
.status-badge.failed {
    background: rgba(244, 63, 94, 0.15);
    color: #f43f5e;
    border: 1px solid rgba(244, 63, 94, 0.3);
    animation: pulse 2s infinite;
}
.status-badge.unavailable {
    background: rgba(245, 158, 11, 0.15);
    color: #f59e0b;
    border: 1px solid rgba(245, 158, 11, 0.3);
}

/* ---- pipeline node ---- */
.pipeline-node {
    background: var(--glass-bg);
    border: 1px solid var(--glass-border);
    border-radius: 12px;
    padding: 0.8rem 1.2rem;
    margin: 0.3rem 0;
    transition: all 0.3s ease;
    display: flex;
    align-items: center;
    gap: 0.8rem;
}
.pipeline-node.active {
    border-color: rgba(99, 102, 241, 0.5);
    background: linear-gradient(135deg, rgba(99,102,241,0.15), rgba(34,211,238,0.08));
    animation: glow 2s ease-in-out infinite;
}
.pipeline-node.completed {
    border-color: rgba(16, 185, 129, 0.4);
    background: rgba(16, 185, 129, 0.08);
}
.pipeline-node .node-icon {
    font-size: 1.4rem;
}
.pipeline-node .node-label {
    font-weight: 500;
    color: var(--text-primary);
}
.pipeline-node .node-status {
    margin-left: auto;
    font-size: 0.8rem;
    color: var(--text-secondary);
}

/* ---- resource card ---- */
.resource-card {
    background: var(--glass-bg);
    backdrop-filter: blur(12px);
    border: 1px solid var(--glass-border);
    border-radius: 16px;
    padding: 1.2rem;
    transition: all 0.3s ease;
    margin-bottom: 0.8rem;
}
.resource-card:hover {
    border-color: rgba(99, 102, 241, 0.35);
    transform: translateY(-2px);
    box-shadow: 0 8px 25px rgba(0, 0, 0, 0.3);
}
.resource-card .rc-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 0.6rem;
}
.resource-card .rc-name {
    font-weight: 600;
    font-size: 1.05rem;
    color: var(--text-primary);
}
.resource-card .rc-detail {
    color: var(--text-secondary);
    font-size: 0.85rem;
    line-height: 1.6;
}

/* ---- timeline ---- */
.timeline-item {
    position: relative;
    padding-left: 2rem;
    padding-bottom: 1.5rem;
    border-left: 2px solid rgba(99, 130, 255, 0.2);
}
.timeline-item::before {
    content: '';
    position: absolute;
    left: -6px;
    top: 4px;
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: #6366f1;
    border: 2px solid rgba(99, 130, 255, 0.4);
}
.timeline-item.success::before {background: #10b981;}
.timeline-item.failure::before {background: #f43f5e;}
.timeline-item .tl-time {
    font-size: 0.78rem;
    color: var(--text-muted);
    font-family: 'JetBrains Mono', monospace;
}
.timeline-item .tl-content {
    margin-top: 0.3rem;
    color: var(--text-primary);
}

/* ---- progress bar override ---- */
.stProgress > div > div > div > div {
    background: linear-gradient(90deg, #6366f1, #22d3ee) !important;
    border-radius: 10px !important;
}

/* ---- download button ---- */
.stDownloadButton > button {
    background: linear-gradient(135deg, rgba(99,102,241,0.2), rgba(34,211,238,0.15)) !important;
    color: var(--text-primary) !important;
    border: 1px solid var(--glass-border) !important;
}

/* ---- alerts / info / warning / error ---- */
[data-testid="stAlert"] {
    background: var(--glass-bg) !important;
    border-radius: 12px !important;
    border: 1px solid var(--glass-border) !important;
}

/* ---- navigation pills ---- */
[data-testid="stSidebarNav"] a {
    color: var(--text-secondary) !important;
    font-weight: 500 !important;
    border-radius: 8px !important;
    transition: all 0.2s ease !important;
}
[data-testid="stSidebarNav"] a[aria-current="page"] {
    background: linear-gradient(135deg, rgba(99,102,241,0.2), rgba(34,211,238,0.1)) !important;
    color: var(--text-primary) !important;
    font-weight: 600 !important;
}

/* ---- JSON viewer ---- */
.stJson {
    background: rgba(17, 22, 56, 0.6) !important;
    border: 1px solid var(--glass-border) !important;
    border-radius: 12px !important;
}

/* ---- slider ---- */
.stSlider [data-baseweb="slider"] [role="slider"] {
    background: #6366f1 !important;
}

/* ---- architecture diagram ---- */
.arch-flow {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 0.5rem;
    flex-wrap: wrap;
    padding: 1rem;
}
.arch-node {
    background: var(--glass-bg);
    border: 1px solid var(--glass-border);
    border-radius: 12px;
    padding: 0.6rem 1rem;
    font-size: 0.85rem;
    font-weight: 500;
    color: var(--text-primary);
    text-align: center;
    min-width: 100px;
    transition: all 0.2s;
}
.arch-node:hover {
    border-color: rgba(99, 102, 241, 0.5);
    transform: scale(1.05);
}
.arch-arrow {
    color: var(--accent-cyan);
    font-size: 1.2rem;
}

/* ---- caption override ---- */
.stCaption, .stMarkdown small {
    color: var(--text-muted) !important;
}
</style>
"""


def inject_theme() -> None:
    """Call once at app start to inject the global CSS."""
    import streamlit as st
    st.markdown(GLOBAL_CSS, unsafe_allow_html=True)


# ---------- helper HTML generators ----------

def kpi_card(value: str, label: str, color: str = ACCENT_BLUE) -> str:
    """Return HTML for a gradient KPI card."""
    return f"""
    <div class="kpi-gradient" style="border-color: {color}30;">
        <div class="kpi-value" style="background: linear-gradient(135deg, {color}, {ACCENT_CYAN});
             -webkit-background-clip: text; -webkit-text-fill-color: transparent;
             background-clip: text;">{value}</div>
        <div class="kpi-label">{label}</div>
    </div>
    """


def status_badge(status: str) -> str:
    """Return HTML for a coloured status badge."""
    status_lower = status.lower()
    cls = "operational"
    if status_lower in ("failed", "blocked", "infeasible"):
        cls = "failed"
    elif status_lower in ("unavailable", "absent", "unknown"):
        cls = "unavailable"
    return f'<span class="status-badge {cls}">{status}</span>'


def glass_card(content: str, extra_style: str = "") -> str:
    """Wrap content in a glass card div."""
    return f'<div class="glass-card" style="{extra_style}">{content}</div>'


def resource_card_html(name: str, status: str, details: list[str]) -> str:
    """HTML for a resource card (machine/worker/material)."""
    badge = status_badge(status)
    detail_html = "<br>".join(
        f'<span class="rc-detail">{d}</span>' for d in details
    )
    return f"""
    <div class="resource-card">
        <div class="rc-header">
            <span class="rc-name">{name}</span>
            {badge}
        </div>
        {detail_html}
    </div>
    """


def pipeline_node_html(icon: str, label: str, status: str = "pending") -> str:
    """HTML for a pipeline node (pending / active / completed)."""
    return f"""
    <div class="pipeline-node {status}">
        <span class="node-icon">{icon}</span>
        <span class="node-label">{label}</span>
        <span class="node-status">{'✓' if status == 'completed' else '⟳' if status == 'active' else '○'}</span>
    </div>
    """


def timeline_item_html(time_str: str, content: str, status: str = "") -> str:
    """HTML for a timeline entry."""
    return f"""
    <div class="timeline-item {status}">
        <div class="tl-time">{time_str}</div>
        <div class="tl-content">{content}</div>
    </div>
    """

