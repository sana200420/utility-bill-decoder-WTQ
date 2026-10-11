"""Bijli Buddy: friendly demo UI for the Utility Bill Decoder.

    python -m streamlit run app.py
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from billdecoder.answer import answer_questions
from billdecoder.config import Config
from billdecoder.explain import explain_bill
from billdecoder.extract import extract_bill
from billdecoder.llm import GeminiClient

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIRS = [ROOT / "train" / "bills", ROOT / "test" / "bills"]
BLUE, ORANGE = "#2a78d6", "#eb6834"  # categorical slots 1 and 2 of the validated reference palette
CHECK_NAMES = {"readings": "meter readings add up", "charges_sum": "charge lines = subtotal",
               "taxes_sum": "tax lines = subtotal", "bill_total": "charges + taxes = bill",
               "payable": "bill + arrears = payable", "late_ge_within": "late amount ≥ on-time",
               "date_order": "dates in order", "month_match": "bill month matches"}
PROVIDER_NAMES = {"KE": "K-Electric", "LESCO": "LESCO (Lahore)", "IESCO": "IESCO (Islamabad)"}
SUGGESTIONS = ["How much of my bill is taxes?", "Why is my bill this amount?",
               "How does this month compare to my usual usage?", "What happens if I pay late?",
               "What is FPA on my bill?", "Estimate next month's bill if I use the same units."]

st.set_page_config(page_title="Bijli Buddy · Bill Decoder", page_icon="⚡", layout="wide")

st.markdown("""
<style>
.block-container {padding-top: 1.6rem; max-width: 1200px;}
.hero {background: linear-gradient(120deg, #fff4e6 0%, #ffe3d3 45%, #e3efff 100%);
       border-radius: 22px; padding: 22px 28px; margin-bottom: 18px; border: 1px solid #f3d9c6;}
.hero h1 {margin: 0; font-size: 2.1rem; color: #0b0b0b;}
.hero p {margin: 4px 0 0 0; color: #52514e; font-size: 1.02rem;}
.card {background: #ffffff; border: 1px solid #ece7e1; border-radius: 18px; padding: 14px 18px;
       box-shadow: 0 2px 10px rgba(30, 20, 10, 0.05); height: 100%;}
.card .label {color: #6b6a66; font-size: 0.82rem; text-transform: uppercase; letter-spacing: .04em;}
.card .value {color: #0b0b0b; font-size: 1.55rem; font-weight: 700; margin-top: 2px;}
.card .sub {color: #6b6a66; font-size: 0.85rem;}
.headline {background: #fff8ef; border-left: 6px solid #eb6834; border-radius: 12px; padding: 14px 18px;
           font-size: 1.12rem; color: #0b0b0b; margin: 6px 0 12px 0;}
.pill {display: inline-block; padding: 3px 11px; border-radius: 999px; font-size: 0.8rem; margin: 2px 4px 2px 0;
       border: 1px solid #e3ddd5; background: #faf8f5; color: #3b3a37;}
.pill.ok {background: #eaf7ef; border-color: #bfe5cc; color: #14532d;}
.pill.bad {background: #fdecec; border-color: #f5c2c2; color: #7f1d1d;}
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="hero">
  <h1>⚡ Bijli Buddy</h1>
  <p>Snap or upload your K-Electric, LESCO or IESCO bill. I'll read it, double-check the maths,
  and explain it in plain words. Ask me anything about it!</p>
</div>
""", unsafe_allow_html=True)


@st.cache_resource
def get_client() -> GeminiClient:
    return GeminiClient(Config.from_env())


def money(x) -> str:
    if x is None:
        return "—"
    if x < 0:
        return f"PKR {abs(x):,.2f} credit".replace(".00 ", " ")
    return f"PKR {x:,.2f}".replace(".00", "")


def card(col, label: str, value: str, sub: str = "") -> None:
    col.markdown(f'<div class="card"><div class="label">{label}</div><div class="value">{value}</div>'
                 f'<div class="sub">{sub}</div></div>', unsafe_allow_html=True)


# ---------- choose a bill ----------
with st.sidebar:
    st.header("📄 Your bill")
    up = st.file_uploader("Upload a bill image", type=["png", "jpg", "jpeg", "webp"],
                          help="Name it like KESC_0008.png / LESCO_0005.png so I know the provider.")
    samples = [p for d in SAMPLE_DIRS if d.exists() for p in sorted(d.iterdir())
               if p.suffix.lower() in (".png", ".jpg", ".jpeg")]
    sample = st.selectbox("…or try a sample bill", ["—"] + [f"{p.parent.parent.name}/{p.name}" for p in samples])
    st.divider()
    st.caption("🧠 Gemini 3.5 Flash-Lite reads the bill twice; Gemini 3.1 Flash-Lite referees disagreements. "
               "All arithmetic is done in Python and every number in an answer is checked against the bill.")

if up is not None:
    name, data = up.name, up.getvalue()
elif sample != "—":
    path = next(p for p in samples if f"{p.parent.parent.name}/{p.name}" == sample)
    name, data = path.name, path.read_bytes()
else:
    st.info("👈 Upload a bill or pick a sample bill from the sidebar to get started.")
    st.stop()

key = hashlib.sha1(data).hexdigest()
state = st.session_state.setdefault(key, {"chat": []})

if "result" not in state:
    tmp = Path(tempfile.mkdtemp()) / name
    tmp.write_bytes(data)
    with st.status("Reading your bill…", expanded=True) as status:
        st.write("🔄 Straightening and sharpening the image")
        st.write("👀 Reading it twice (original + zoomed views) and comparing")
        try:
            state["result"] = extract_bill(get_client(), tmp)
        except Exception as e:  # show a friendly error instead of a stack trace
            status.update(label="Couldn't read this bill", state="error")
            st.error(f"Sorry, I couldn't read this bill: {e}")
            st.stop()
        st.write("🧮 Cross-checking totals")
        st.write("✍️ Writing your plain-language summary")
        try:
            state["explain"] = explain_bill(get_client(), Path(name).stem, state["result"]["record"])
        except Exception as e:
            state["explain"] = {"headline": "", "points": [], "tips": [], "error": str(e)}
        status.update(label="All done!", state="complete", expanded=False)

res, exp = state["result"], state["explain"]
l1, rec, audit = res["level1"], res["record"], res["audit"]
bill_id = Path(name).stem

# ---------- overview ----------
left, right = st.columns([5, 7], gap="large")
with left:
    st.image(data, caption=name, use_column_width=True)
with right:
    st.subheader(f"{PROVIDER_NAMES.get(l1['provider'], l1['provider'])} · {l1.get('bill_month') or ''}")
    if exp.get("headline"):
        st.markdown(f'<div class="headline">💡 {exp["headline"]}</div>', unsafe_allow_html=True)
    c1, c2, c3 = st.columns(3)
    card(c1, "Pay by due date", money(l1["payable_within_due_date"]), f"due {l1.get('due_date') or '—'}")
    late = l1.get("payable_after_due_date")
    extra = (late - l1["payable_within_due_date"]) if None not in (late, l1["payable_within_due_date"]) else None
    card(c2, "If paid late", money(late), f"+{money(extra)} extra" if extra and extra > 0 else "")
    tax_share = (l1["total_taxes"] / l1["current_bill"] * 100) if l1.get("total_taxes") and l1.get("current_bill") else None
    card(c3, "Units used", f"{l1['units_consumed'] if l1.get('units_consumed') is not None else '—'} kWh",
         f"taxes {tax_share:.1f}% of bill" if tax_share else "")
    st.write("")
    checks = audit["checks"]
    passed = sum(c["status"] == "pass" for c in checks)
    failed = sum(c["status"] == "fail" for c in checks)
    pills = "".join(f'<span class="pill {"ok" if c["status"] == "pass" else "bad" if c["status"] == "fail" else ""}">'
                    f'{"✓" if c["status"] == "pass" else "✗" if c["status"] == "fail" else "–"} {CHECK_NAMES.get(c["check"], c["check"])}'
                    f'</span>' for c in checks)
    st.markdown(f"**Trust meter:** {passed} maths checks passed, {failed} failed · "
                f"readings agreed on {audit['agreement']:.0%} of fields<br>{pills}", unsafe_allow_html=True)
    if exp.get("points"):
        st.markdown("#### 🧾 Your bill in plain words")
        st.markdown("\n".join(f"- {p}" for p in exp["points"]))
    if exp.get("tips"):
        st.markdown("#### 🌱 Tips")
        st.markdown("\n".join(f"- {t}" for t in exp["tips"]))

st.divider()
tab_chat, tab_usage, tab_money, tab_json = st.tabs(["💬 Ask Bijli Buddy", "📊 Usage history",
                                                    "💸 Where the money goes", "🧩 Extracted data (Level 1)"])

# ---------- chat ----------
with tab_chat:
    cols = st.columns(3)
    clicked = None
    for i, s in enumerate(SUGGESTIONS):
        if cols[i % 3].button(s, key=f"sugg{i}", use_container_width=True):
            clicked = s
    for m in state["chat"]:
        with st.chat_message(m["role"], avatar="⚡" if m["role"] == "assistant" else "🙂"):
            st.markdown(m["text"])
    typed = st.chat_input("Ask anything about this bill…")
    question = typed or clicked
    if question:
        state["chat"].append({"role": "user", "text": question})
        with st.chat_message("user", avatar="🙂"):
            st.markdown(question)
        with st.chat_message("assistant", avatar="⚡"):
            with st.spinner("Planning → calculating in Python → writing…"):
                out = answer_questions(get_client(), bill_id, rec, [("Q1", question)])
            st.markdown(out["answers"]["Q1"])
            with st.expander("🔍 How I worked it out"):
                st.json(out["audit"]["work"][0])
        state["chat"].append({"role": "assistant", "text": out["answers"]["Q1"]})

# ---------- usage history ----------
with tab_usage:
    hist = rec.get("_extras", {}).get("usage_history", [])
    rows = [{"Month": h["month"], "Units": h["units"],
             "Period": "This month" if h["month"] == l1.get("bill_month") else "Previous months"}
            for h in hist if h.get("units") is not None]
    if l1.get("bill_month") and l1.get("units_consumed") is not None and not any(r["Period"] == "This month" for r in rows):
        rows.append({"Month": l1["bill_month"], "Units": l1["units_consumed"], "Period": "This month"})
    if rows:
        df = pd.DataFrame(rows)
        chart = alt.Chart(df).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4, size=22).encode(
            x=alt.X("Month:N", sort=None, title=None, axis=alt.Axis(labelAngle=-40)),
            y=alt.Y("Units:Q", title="Units (kWh)", axis=alt.Axis(grid=True, gridOpacity=0.25)),
            color=alt.Color("Period:N", scale=alt.Scale(domain=["Previous months", "This month"],
                                                        range=[BLUE, ORANGE]),
                            legend=alt.Legend(orient="top", title=None)),
            tooltip=["Month", "Units", "Period"],
        ).properties(height=320)
        st.altair_chart(chart, use_container_width=True)
        prev = [r["Units"] for r in rows if r["Period"] == "Previous months"]
        if prev and l1.get("units_consumed") is not None:
            avg = sum(prev) / len(prev)
            st.markdown(f"This month: **{l1['units_consumed']} units** vs your average of **{avg:.0f} units** "
                        f"over {len(prev)} previous months.")
        with st.expander("Table view"):
            st.dataframe(pd.DataFrame(hist), use_container_width=True, hide_index=True)
    else:
        st.info("No usage history was printed on this bill.")

# ---------- money breakdown ----------
with tab_money:
    lines = [{"Item": (c.get("label") or c["type"]), "Type": c["type"], "Amount (PKR)": c["amount"], "Group": "Charges"}
             for c in rec.get("charges") or []]
    lines += [{"Item": (t.get("label") or t["type"]), "Type": t["type"], "Amount (PKR)": t["amount"], "Group": "Taxes"}
              for t in rec.get("taxes") or []]
    if not rec.get("taxes") and l1.get("total_taxes") is not None:
        lines.append({"Item": "Taxes (combined)", "Type": "taxes", "Amount (PKR)": l1["total_taxes"], "Group": "Taxes"})
    if lines:
        df = pd.DataFrame(lines)
        chart = alt.Chart(df).mark_bar(cornerRadiusEnd=4, size=18).encode(
            y=alt.Y("Item:N", sort=None, title=None),
            x=alt.X("Amount (PKR):Q", axis=alt.Axis(grid=True, gridOpacity=0.25)),
            color=alt.Color("Group:N", scale=alt.Scale(domain=["Charges", "Taxes"], range=[BLUE, ORANGE]),
                            legend=alt.Legend(orient="top", title=None)),
            tooltip=["Item", "Type", alt.Tooltip("Amount (PKR):Q", format=",.2f")],
        ).properties(height=max(160, 34 * len(df)))
        st.altair_chart(chart, use_container_width=True)
        c1, c2, c3 = st.columns(3)
        card(c1, "Charges subtotal", money(l1.get("total_charges")))
        card(c2, "Taxes subtotal", money(l1.get("total_taxes")))
        card(c3, "Previous dues", money(l1.get("arrears")))
    else:
        st.info("No charge lines were printed on this bill.")

# ---------- level 1 json ----------
with tab_json:
    a, b = st.columns([3, 2])
    with a:
        st.json(l1)
        st.download_button("⬇️ Download JSON", json.dumps(l1, indent=2), file_name=f"{bill_id}.json",
                           mime="application/json")
    with b:
        st.markdown("**Arithmetic cross-checks**")
        for c in checks:
            icon = {"pass": "✅", "fail": "❌", "na": "➖"}[c["status"]]
            st.write(f"{icon} **{c['check']}** · {c['detail']}")
        st.markdown(f"**Reading passes:** {', '.join(audit['passes'])}")
        if audit.get("fields_without_unanimity"):
            st.markdown(f"**Fields that needed a referee:** {', '.join(audit['fields_without_unanimity'])}")
        st.caption(f"Image preprocessing: deskewed {audit['preprocess']['deskew_deg']}°")
