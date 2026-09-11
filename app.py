import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.transforms as mtransforms
from matplotlib.lines import Line2D

# ═══════════════════════════════════════════════════════
# 1. 設定你的 Google Sheets CSV 網址
# ═══════════════════════════════════════════════════════
SHEET_URLS = {
    "Board_A": "https://docs.google.com/spreadsheets/d/1viaEU8TiREaTKlSUQuXC28ZT-KS-lv8FQn9JDlYsQpo/export?format=csv",
    "Board_B": "https://docs.google.com/spreadsheets/d/1T8dUEpCaVoXjv6QsS3J6sC3E76i0MjWt4FnpD54eRFM/export?format=csv",
    "Board_C": "https://docs.google.com/spreadsheets/d/1KpbSS7ENqRNEc7Pw8E8dPWWQuo9NUkook-bLmI9LWzY/export?format=csv",
    "Board_D": "https://docs.google.com/spreadsheets/d/1nuPwu7S7ufYDojdgGiPjk9Jx8fAmJWqXxURzpkLv4tg/export?format=csv",
}

# ═══════════════════════════════════════════════════════
# 2. 原有函式保留區
# ═══════════════════════════════════════════════════════
TYPE_COLOR = {"B": "#8a6fd6", "P": "#eb6834", "D": "#2a9d5c", "O": "#c9a600"}
TYPE_COLOR_DEFAULT = "#999999"

def is_randomized(mac):
    try:
        return bool(int(mac[0:2], 16) & 0x02)
    except (ValueError, IndexError):
        return False

def parse_macs(s):
    out = []
    if not isinstance(s, str) or not s:
        return out
    for entry in s.split(","):
        parts = entry.split(":")
        if len(parts) != 4:
            continue
        mac, ch, rssi, typ = parts
        try:
            rssi_val = int(rssi)
        except ValueError:
            rssi_val = None
        out.append((mac.upper(), ch, rssi_val, typ.upper()))
    return out

@st.cache_data(ttl=10)
def load_all_boards(tz="Asia/Taipei"):
    long_dfs = []
    board_names = []
    
    for board_name, url in SHEET_URLS.items():
        try:
            df = pd.read_csv(url, encoding="utf-8-sig")
            if "mac_details" not in df.columns or "timestamp" not in df.columns:
                continue
                
            df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce")
            df = df.dropna(subset=["timestamp"])
            
            df["dt_corrected"] = (
                pd.to_datetime(df["timestamp"], unit="ms", utc=True)
                .dt.tz_convert(tz)
                .dt.tz_localize(None)
            )
            df = df.sort_values("dt_corrected").reset_index(drop=True)
            
            rows = []
            for dt, mac_str in zip(df["dt_corrected"], df["mac_details"].fillna("")):
                for mac, ch, rssi, typ in parse_macs(mac_str):
                    rows.append({
                        "board": board_name,
                        "datetime": dt,
                        "mac": mac,
                        "channel": ch,
                        "rssi": rssi,
                        "type": typ
                    })
            if rows:
                long_dfs.append(pd.DataFrame(rows))
                board_names.append(board_name)
        except Exception as e:
            st.warning(f"無法讀取 {board_name}: {e}")
            
    if not long_dfs:
        return None, []
    
    return pd.concat(long_dfs, ignore_index=True), board_names

# ═══════════════════════════════════════════════════════
# 3. Streamlit 網頁介面
# ═══════════════════════════════════════════════════════
st.set_page_config(page_title="ESP32 多板實時追蹤", layout="wide")
st.title("多板時間軸追蹤")

long_df, active_boards = load_all_boards()

if long_df is None or long_df.empty:
    st.error("沒有讀取到任何有效資料，請檢查 Google Sheets 連結。")
    st.stop()

# 側邊欄過濾參數設定
st.sidebar.header("過濾參數設定")

# 新增：讓使用者勾選必須同時收到的板子，預設全選
selected_boards = st.sidebar.multiselect(
    "選擇必須同時收到的板子 (最少勾一塊)",
    options=active_boards,
    default=active_boards
)

window_sec = st.sidebar.number_input("時間對齊桶寬 (--window)", min_value=1, max_value=60, value=5)
min_count = st.sidebar.number_input("最少出現次數 (--min-count)", min_value=1, max_value=100, value=1)
top_n = st.sidebar.number_input("顯示前 N 個裝置 (0=全部)", min_value=0, max_value=200, value=0)
pkt_type = st.sidebar.selectbox("封包類型 (--type)", ["全部", "P", "B", "D", "O"])
rssi_min = st.sidebar.slider("最小 RSSI (--rssi-min)", -100, -10, -100)

if st.button("🔄 重新載入最新資料"):
    st.cache_data.clear()
    st.rerun()

st.write(f"✅ 成功讀取 **{len(active_boards)}** 塊板子: {', '.join(active_boards)}")

# 防呆機制：確保至少勾選了一塊板子
if not selected_boards:
    st.warning("請在側邊欄至少選擇一塊板子！")
    st.stop()

# 基本過濾邏輯
filtered_df = long_df.copy()

# 只保留使用者勾選的板子資料
filtered_df = filtered_df[filtered_df["board"].isin(selected_boards)]

if pkt_type != "全部":
    filtered_df = filtered_df[filtered_df["type"] == pkt_type]
if rssi_min > -100:
    filtered_df = filtered_df[filtered_df["rssi"] >= rssi_min]
filtered_df = filtered_df.dropna(subset=["rssi"])

if filtered_df.empty:
    st.warning("套用過濾條件後沒有任何資料剩下。")
    st.stop()

# 時間切桶與多板交集
filtered_df["bucket"] = filtered_df["datetime"].dt.floor(f"{window_sec}s")
board_count = filtered_df.groupby(["bucket", "mac"])["board"].nunique().reset_index(name="n_boards")

# 動態判斷：只留下「有被勾選的板子」全部同時收到的 MAC
n_required = len(selected_boards)
full_coverage = board_count[board_count["n_boards"] == n_required]

if full_coverage.empty:
    st.warning(f"沒有任何 MAC 同時被勾選的 {n_required} 塊板子收到。可考慮加大時間桶寬。")
    st.stop()

keep_keys = set(zip(full_coverage["bucket"], full_coverage["mac"]))
filtered_df["_key"] = list(zip(filtered_df["bucket"], filtered_df["mac"]))
final_df = filtered_df[filtered_df["_key"].isin(keep_keys)].drop(columns="_key")

# 建立 presence
presence = {}
for (mac, bucket), grp in final_df.groupby(["mac", "bucket"]):
    presence.setdefault(mac, []).append((bucket, grp["type"].mode().iloc[0]))

macs = [(m, sorted(pts)) for m, pts in presence.items() if len(pts) >= min_count]
if top_n > 0:
    macs.sort(key=lambda x: -len(x[1]))
    macs = macs[:top_n]
macs.sort(key=lambda x: x[1][0][0]) 

n_macs = len(macs)
if n_macs == 0:
    st.warning("套用 min-count 後沒有裝置符合。")
    st.stop()

# ═══════════════════════════════════════════════════════
# 4. 繪圖區塊
# ═══════════════════════════════════════════════════════
all_buckets = sorted(final_df["bucket"].unique())
t_min, t_max = all_buckets[0], all_buckets[-1]

row_h = 0.09
fig_h = max(1.0 + n_macs * row_h, 3.0)

fig, ax_main = plt.subplots(figsize=(12, fig_h))
right_text_trans = mtransforms.blended_transform_factory(ax_main.transAxes, ax_main.transData)

for row, (mac, pts) in enumerate(macs):
    marker = "o" if is_randomized(mac) else "s"
    by_type = {}
    for bucket, typ in pts:
        by_type.setdefault(typ, []).append(bucket)
    for typ, times in by_type.items():
        color = TYPE_COLOR.get(typ, TYPE_COLOR_DEFAULT)
        ax_main.scatter(times, [row] * len(times), s=10, color=color, marker=marker, linewidths=0)

    first_t, last_t = pts[0][0], pts[-1][0]
    span_min = (last_t - first_t).total_seconds() / 60.0
    ax_main.text(1.01, row, f"{span_min:.1f} min", transform=right_text_trans, fontsize=8, va="center")

ax_main.set_ylim(-1, n_macs)
ax_main.set_yticks(range(n_macs))
ax_main.set_yticklabels([m[-6:] for m, _ in macs], fontsize=8)
ax_main.set_xlim(t_min, t_max)
ax_main.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
ax_main.grid(alpha=0.2)

legend_elems = [Line2D([0], [0], marker="s", color="none", markerfacecolor=c, markersize=8, label=t) for t, c in TYPE_COLOR.items()]
ax_main.legend(handles=legend_elems, loc="upper right", fontsize=8, ncol=len(legend_elems))
plt.tight_layout()

# 用 Streamlit 渲染圖片
st.pyplot(fig)
