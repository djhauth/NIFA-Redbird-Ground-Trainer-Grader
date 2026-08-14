import streamlit as st
import pandas as pd
import xml.etree.ElementTree as ET
import numpy as np
from fpdf import FPDF
import tempfile
import matplotlib.pyplot as plt
import re
import os

# --- APP CONFIGURATION ---
st.set_page_config(page_title="Flight Maneuver Grader", layout="wide")
st.title("✈️ Flight Maneuver Auto-Grader v15.0")
st.markdown("Upload KML data, sync Google Sheets, and generate side-by-side Normalized and Interpolated scores.")

# --- HELPER FUNCTION: PARSE KML ---
def parse_kml(uploaded_file):
    tree = ET.parse(uploaded_file)
    root = tree.getroot()
    
    ns = {'kml': 'http://www.opengis.net/kml/2.2', 'gx': 'http://www.google.com/kml/ext/2.2'}
    times = [elem.text for elem in root.findall('.//kml:when', ns)]
    
    def get_array_data(name):
        path = f".//gx:SimpleArrayData[@name='{name}']/gx:value"
        return [float(elem.text) for elem in root.findall(path, ns)]
    
    data = {
        'Timestamp': times,
        'Course': get_array_data('course'),
        'Altitude': get_array_data('altitude'),
        'Airspeed': get_array_data('speed_kts')
    }
    
    min_len = min(len(times), len(data['Course']), len(data['Altitude']), len(data['Airspeed']))
    for key in data.keys():
        data[key] = data[key][:min_len]
        
    df = pd.DataFrame(data)
    df['Timestamp'] = pd.to_datetime(df['Timestamp'])
    return df

# --- HELPER FUNCTION: GENERATE PDF WITH IMAGES ---
def generate_pdf(c_name, c_num, c_school, norm_points, interp_points, flight_time, missing_pct, integrity_warning, df, img_paths):
    pdf = FPDF()
    
    # PAGE 1: Text Summary
    pdf.add_page()
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, "Flight Maneuver Grade Report", ln=True, align="C")
    pdf.ln(5)
    
    pdf.set_font("Arial", "", 12)
    pdf.cell(0, 8, f"Contestant Name: {c_name}", ln=True)
    pdf.cell(0, 8, f"Contestant Number: {c_num}", ln=True)
    pdf.cell(0, 8, f"School / University: {c_school}", ln=True)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(5)
    
    # Dual Scores
    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 8, f"Normalized Final Score (Penalty Points): {norm_points:.2f}", ln=True)
    pdf.cell(0, 8, f"Interpolated Final Score (Penalty Points): {interp_points:.2f}", ln=True)
    
    pdf.set_font("Arial", "", 12)
    pdf.cell(0, 8, f"Total Scheduled Flight Time: {flight_time} seconds", ln=True)
    pdf.cell(0, 8, f"Data Loss: {missing_pct:.1f}%", ln=True)
    
    if integrity_warning:
        pdf.set_text_color(255, 0, 0)
        pdf.set_font("Arial", "B", 12)
        pdf.cell(0, 8, "WARNING: Data loss exceeds 10%. Score is statistically unreliable. Re-fly recommended.", ln=True)
        pdf.set_text_color(0, 0, 0)
        
    pdf.ln(5)
    
    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 10, "Performance Summary (Max Deviations):", ln=True)
    pdf.set_font("Arial", "", 12)
    pdf.cell(0, 8, f"Max Altitude Penalty: {df['Altitude_Penalty'].max():.2f} pts", ln=True)
    pdf.cell(0, 8, f"Max Heading Penalty: {df['Heading_Penalty'].max():.2f} pts", ln=True)
    pdf.cell(0, 8, f"Max Airspeed Penalty: {df['Airspeed_Penalty'].max():.2f} pts", ln=True)
    
    # PAGE 2: Overhead Map
    pdf.add_page()
    pdf.set_font("Arial", "B", 14)
    pdf.cell(0, 10, "Flight Path (Curve-Fitted Overhead Map)", ln=True, align="C")
    pdf.image(img_paths['map'], x=10, y=pdf.get_y() + 5, w=190)
    
    # PAGE 3: Telemetry Charts
    pdf.add_page()
    pdf.set_font("Arial", "B", 14)
    pdf.cell(0, 10, "Telemetry Analysis", ln=True, align="C")
    curr_y = pdf.get_y() + 5
    pdf.image(img_paths['head'], x=10, y=curr_y, w=190)
    pdf.image(img_paths['alt'], x=10, y=curr_y + 80, w=190)
    pdf.image(img_paths['spd'], x=10, y=curr_y + 160, w=190)
    
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        pdf.output(tmp.name)
        return tmp.name

# --- HELPER FUNCTION: PLOT CHARTS WITH RED GAPS ---
def plot_telemetry_chart(df, df_interp, y_actual, y_target, title, ylabel):
    fig, ax = plt.subplots(figsize=(10, 4))
    
    ax.plot(df['Elapsed_Sec'], df[y_target], label='Target Baseline', linestyle='--', color='gray', linewidth=2)
    ax.plot(df_interp['Elapsed_Sec'], df_interp[y_actual], label='Interpolated Data', linestyle=':', color='lightblue', linewidth=2)
    ax.plot(df['Elapsed_Sec'], df[y_actual], label='Actual Flown', color='blue', linewidth=2)
    
    gap_indices = df.index[df['Timestamp'].diff().dt.total_seconds() > 3.0].tolist()
    first_gap = True
    for idx in gap_indices:
        prev_idx = idx - 1
        if prev_idx in df.index and idx in df.index:
            x_gap = [df.loc[prev_idx, 'Elapsed_Sec'], df.loc[idx, 'Elapsed_Sec']]
            y_gap = [df.loc[prev_idx, y_actual], df.loc[idx, y_actual]]
            label = 'Data Gap (>3s)' if first_gap else ""
            ax.plot(x_gap, y_gap, color='red', linewidth=3, label=label)
            first_gap = False
            
    ax.set_title(title)
    ax.set_xlabel("Elapsed Time (Seconds)")
    ax.set_ylabel(ylabel)
    ax.grid(True, linestyle=':', alpha=0.7)
    ax.legend()
    return fig

# --- HELPER FUNCTION: LOAD GOOGLE SHEET ---
@st.cache_data(ttl=60)
def load_google_sheet(url):
    try:
        match = re.search(r'\/d\/([a-zA-Z0-9-_]+)', url)
        if match:
            export_url = f"https://docs.google.com/spreadsheets/d/{match.group(1)}/export?format=csv"
            return pd.read_csv(export_url)
        return None
    except Exception as e:
        return None

# --- UI: CONTESTANT & FILE UPLOAD ---
st.header("1. Contestant Info & Flight Data")
col_c1, col_c2, col_c3 = st.columns(3)
c_name = col_c1.text_input("Contestant Name", placeholder="e.g. John Doe")
c_num = col_c2.text_input("Contestant Number", placeholder="e.g. 42")
c_school = col_c3.text_input("School / University", placeholder="e.g. Flight State")

uploaded_file = st.file_uploader("Upload Redbird KML File", type=['kml'])

# --- UI: FLIGHT PLAN TARGETS ---
st.header("2. Live Flight Plan Targets (Google Sheets)")
sheet_url = st.text_input(
    "Google Sheet URL", 
    value="https://docs.google.com/spreadsheets/d/1-iLt1JF3XOD7ZriVI5AiMRKMsP5QwuIxSF4LHzslBIk/edit?usp=drivesdk"
)

edited_plan = load_google_sheet(sheet_url)

if edited_plan is not None:
    st.dataframe(edited_plan, use_container_width=True)
else:
    st.error("🚨 Cannot load Google Sheet. Ensure the link is correct and sharing is set to 'Anyone with the link can view'.")

# --- UI: QUALITY ASSURANCE & CONFIGURATION ---
st.header("3. Quality Assurance & Settings")

if uploaded_file is not None and edited_plan is not None:
    raw_df = parse_kml(uploaded_file)
    time_options = raw_df['Timestamp'].dt.strftime('%H:%M:%S').unique().tolist()
    
    cols = edited_plan.columns.str.lower()
    time_col = edited_plan.columns[cols.str.contains('time|duration')][0] if any(cols.str.contains('time|duration')) else None
    head_col = edited_plan.columns[cols.str.contains('head|course')][0] if any(cols.str.contains('head|course')) else None
    alt_col = edited_plan.columns[cols.str.contains('alt')][0] if any(cols.str.contains('alt')) else None
    spd_col = edited_plan.columns[cols.str.contains('speed|kts')][0] if any(cols.str.contains('speed|kts')) else None
    
    st.subheader("⏱️ Evaluator Settings")
    col1, col2, col3, col4 = st.columns(4)
    start_time_sel = col1.selectbox("Grading Start Time (UTC)", options=time_options, index=0)
    end_time_sel = col2.selectbox("Grading End Time (UTC)", options=time_options, index=len(time_options)-1)
    mag_var = col3.number_input("Magnetic Variation (e.g., -11 for West)", value=-11.0)
    grace_period = col4.number_input("Leg Grace Period (sec)", min_value=0, value=2)

    if start_time_sel >= end_time_sel:
        st.error("🚨 Start Time must be before End Time!")
    else:
        if st.button("Run Analysis & Calculate Grade", type="primary"):
            if not all([time_col, head_col, alt_col, spd_col]):
                st.error("🚨 Could not identify all required columns in your Google Sheet.")
            else:
                with st.spinner("Running Dual-Engine analysis (Normalization & Interpolation)..."):
                    
                    # --- BASE DATA PREP ---
                    df = raw_df.copy()
                    df['Course'] = (df['Course'] - mag_var) % 360
                    mask = (df['Timestamp'].dt.strftime('%H:%M:%S') >= start_time_sel) & \
                           (df['Timestamp'].dt.strftime('%H:%M:%S') <= end_time_sel)
                    df = df[mask].reset_index(drop=True)
                    
                    t0 = df['Timestamp'].iloc[0]
                    df['Elapsed_Sec'] = (df['Timestamp'] - t0).dt.total_seconds().astype(int)
                    
                    evaluated_rows = len(df)
                    total_expected_seconds = int(df['Elapsed_Sec'].max()) + 1
                    missing_pct = (max(0, total_expected_seconds - evaluated_rows) / total_expected_seconds) * 100 if total_expected_seconds > 0 else 0
                    integrity_warning = missing_pct > 10.0
                    
                    # --- TARGET GENERATION ---
                    master_headings, master_altitudes, master_airspeeds = [], [], []
                    master_turn_rates, master_climb_rates, master_grace = [], [], []
                    
                    try:
                        curr_heading = float(edited_plan.iloc[0][head_col])
                        curr_altitude = float(str(edited_plan.iloc[0][alt_col]).replace(',', ''))
                    except:
                        curr_heading, curr_altitude = 360.0, 0.0

                    for index, row in edited_plan.iterrows():
                        raw_time = str(row[time_col]).strip()
                        if ':' in raw_time:
                            parts = raw_time.split(':')
                            duration = int(parts[0]) * 60 + int(parts[1])
                        else:
                            try: duration = int(float(raw_time))
                            except: duration = 30
                            
                        h_cmd = str(row[head_col]).strip().lower()
                        a_cmd = str(row[alt_col]).strip().lower()
                        
                        is_turn = h_cmd in ['left turn', 'right turn']
                        is_climb = a_cmd == 'climb'
                        is_desc = a_cmd == 'descend'
                        
                        tgt_turn_rate = 3.0 if is_turn else 0.0
                        if is_climb: tgt_climb_rate = 500.0
                        elif is_desc: tgt_climb_rate = -500.0
                        else: tgt_climb_rate = 0.0
                        
                        leg_grace = [True] * min(grace_period, duration) + [False] * max(0, duration - grace_period)
                        master_grace.extend(leg_grace)
                        
                        for _ in range(duration):
                            if h_cmd == 'left turn':
                                curr_heading = (curr_heading - 3) % 360
                                if curr_heading == 0: curr_heading = 360
                            elif h_cmd == 'right turn':
                                curr_heading = (curr_heading + 3) % 360
                                if curr_heading == 0: curr_heading = 360
                            else:
                                try: curr_heading = float(h_cmd)
                                except: pass
                            
                            if a_cmd == 'climb': curr_altitude += (500.0 / 60.0)
                            elif a_cmd == 'descend': curr_altitude -= (500.0 / 60.0)
                            else:
                                try: curr_altitude = float(str(a_cmd).replace(',', ''))
                                except: pass
                                
                            master_headings.append(curr_heading)
                            master_altitudes.append(curr_altitude)
                            master_airspeeds.append(float(str(row[spd_col]).lower().replace('kts', '').strip()))
                            master_turn_rates.append(tgt_turn_rate)
                            master_climb_rates.append(tgt_climb_rate)
                    
                    def get_tgt(sec, lst, default):
                        if sec < len(lst): return lst[sec]
                        if len(lst) > 0: return lst[-1]
                        return default
                    
                    # --- INTERPOLATION ENGINE ---
                    # Create a perfect 1-second continuous timeline
                    df_interp = pd.DataFrame({'Elapsed_Sec': range(total_expected_seconds)})
                    df_interp = df_interp.merge(df[['Elapsed_Sec', 'Course', 'Altitude', 'Airspeed']], on='Elapsed_Sec', how='left')
                    
                    # Sine/Cosine interpolation for Course (handles 360-degree wraparound seamlessly)
                    df_interp['Course_Sin'] = np.sin(np.radians(df_interp['Course']))
                    df_interp['Course_Cos'] = np.cos(np.radians(df_interp['Course']))
                    df_interp['Course_Sin'] = df_interp['Course_Sin'].interpolate(method='linear', limit_direction='both')
                    df_interp['Course_Cos'] = df_interp['Course_Cos'].interpolate(method='linear', limit_direction='both')
                    df_interp['Course'] = (np.degrees(np.arctan2(df_interp['Course_Sin'], df_interp['Course_Cos'])) + 360) % 360
                    
                    # Linear interpolation for linear variables
                    df_interp['Altitude'] = df_interp['Altitude'].interpolate(method='linear', limit_direction='both')
                    df_interp['Airspeed'] = df_interp['Airspeed'].interpolate(method='linear', limit_direction='both')
                    
                    # --- PENALTY MATH FOR BOTH DATASETS ---
                    def calculate_penalties(data_frame, is_interpolated=False):
                        data_frame['Target_Heading'] = data_frame['Elapsed_Sec'].apply(lambda x: get_tgt(x, master_headings, 360.0))
                        data_frame['Target_Altitude'] = data_frame['Elapsed_Sec'].apply(lambda x: get_tgt(x, master_altitudes, 0.0))
                        data_frame['Target_Airspeed'] = data_frame['Elapsed_Sec'].apply(lambda x: get_tgt(x, master_airspeeds, 0.0))
                        data_frame['Target_Turn_Rate'] = data_frame['Elapsed_Sec'].apply(lambda x: get_tgt(x, master_turn_rates, 0.0))
                        data_frame['Target_Climb_Rate'] = data_frame['Elapsed_Sec'].apply(lambda x: get_tgt(x, master_climb_rates, 0.0))
                        data_frame['In_Grace_Period'] = data_frame['Elapsed_Sec'].apply(lambda x: get_tgt(x, master_grace, False))
                        
                        # Calculate Rates
                        if not is_interpolated:
                            dt = df['Timestamp'].diff().dt.total_seconds().fillna(1)
                            dt = np.where(dt == 0, 1, dt)
                        else:
                            dt = 1.0 # Interpolated data is exactly 1 row per second
                            
                        course_diff = data_frame['Course'].diff().fillna(0).abs()
                        circular_diff = np.minimum(course_diff, 360 - course_diff)
                        data_frame['Turn_Rate_deg_sec'] = circular_diff / dt
                        data_frame['Climb_Rate_FPM'] = (data_frame['Altitude'].diff().fillna(0) / dt) * 60
                        
                        # Base Penalties
                        head_diff = (data_frame['Course'] - data_frame['Target_Heading']).abs()
                        data_frame['Heading_Penalty'] = np.minimum(head_diff, 360 - head_diff) * 1
                        data_frame['Altitude_Penalty'] = (data_frame['Altitude'] - data_frame['Target_Altitude']).abs() / 10
                        data_frame['Airspeed_Penalty'] = (data_frame['Airspeed'] - data_frame['Target_Airspeed']).abs() * 1
                        data_frame['Turn_Penalty'] = (data_frame['Turn_Rate_deg_sec'] - data_frame['Target_Turn_Rate']).abs() * 25
                        data_frame['Climb_Penalty'] = (data_frame['Climb_Rate_FPM'] - data_frame['Target_Climb_Rate']).abs() / 100 * 2
                        
                        # Zero out grace periods
                        p_cols = ['Heading_Penalty', 'Altitude_Penalty', 'Airspeed_Penalty', 'Turn_Penalty', 'Climb_Penalty']
                        data_frame.loc[data_frame['In_Grace_Period'] == True, p_cols] = 0
                        data_frame['Total_Penalty'] = data_frame[p_cols].sum(axis=1)
                        return data_frame

                    # Run calculations on both engines
                    df = calculate_penalties(df, is_interpolated=False)
                    df_interp = calculate_penalties(df_interp, is_interpolated=True)
                    
                    # 1. NORMALIZED SCORE (Prorated actuals)
                    avg_penalty_per_sec = df['Total_Penalty'].sum() / evaluated_rows if evaluated_rows > 0 else 0
                    normalized_total_score = avg_penalty_per_sec * total_expected_seconds
                    
                    # 2. INTERPOLATED SCORE (Sum of filled-in dataset)
                    interpolated_total_score = df_interp['Total_Penalty'].sum()
                    
                    # --- CARTESIAN XY MAP MATH ---
                    map_x, map_y = [0.0], [0.0]
                    red_segments = []
                    curr_x, curr_y = 0.0, 0.0
                    
                    for i in range(1, len(df)):
                        dt = df['Elapsed_Sec'].iloc[i] - df['Elapsed_Sec'].iloc[i-1]
                        is_large_gap = dt > 3.0
                        
                        if dt > 1.0 and dt < 120: 
                            c1 = df['Course'].iloc[i-1]
                            c2 = df['Course'].iloc[i]
                            s1 = df['Airspeed'].iloc[i-1]
                            s2 = df['Airspeed'].iloc[i]
                            
                            diff = (c2 - c1 + 180) % 360 - 180
                            turn_rate = diff / dt
                            speed_rate = (s2 - s1) / dt
                            
                            seg_x, seg_y = [curr_x], [curr_y]
                            
                            for step in range(1, int(dt) + 1):
                                interp_c = (c1 + turn_rate * step) % 360
                                interp_s = s1 + speed_rate * step
                                dx = interp_s * 1.68781 * np.sin(np.radians(interp_c)) * 1.0
                                dy = interp_s * 1.68781 * np.cos(np.radians(interp_c)) * 1.0
                                curr_x += dx
                                curr_y += dy
                                map_x.append(curr_x)
                                map_y.append(curr_y)
                                seg_x.append(curr_x)
                                seg_y.append(curr_y)
                                
                            if is_large_gap:
                                red_segments.append((seg_x, seg_y))
                        else:
                            dx = df['Airspeed'].iloc[i] * 1.68781 * np.sin(np.radians(df['Course'].iloc[i])) * dt
                            dy = df['Airspeed'].iloc[i] * 1.68781 * np.cos(np.radians(df['Course'].iloc[i])) * dt
                            prev_x, prev_y = curr_x, curr_y
                            curr_x += dx
                            curr_y += dy
                            map_x.append(curr_x)
                            map_y.append(curr_y)
                            if is_large_gap:
                                red_segments.append(([prev_x, curr_x], [prev_y, curr_y]))
    
                    ideal_df = pd.DataFrame({'Target_Heading': master_headings, 'Target_Airspeed': master_airspeeds})
                    ideal_df['Target_dX'] = ideal_df['Target_Airspeed'] * 1.68781 * np.sin(np.radians(ideal_df['Target_Heading'])) * 1.0
                    ideal_df['Target_dY'] = ideal_df['Target_Airspeed'] * 1.68781 * np.cos(np.radians(ideal_df['Target_Heading'])) * 1.0
                    ideal_df['Target_X'] = ideal_df['Target_dX'].cumsum()
                    ideal_df['Target_Y'] = ideal_df['Target_dY'].cumsum()
                    
                    # --- RESULTS DASHBOARD & IMAGE GENERATION ---
                    st.success("Dual-Engine Analysis Complete!")
                    st.divider()
                    
                    st.header("📊 Performance Results")
                    
                    if integrity_warning:
                        st.error(f"🚨 **COMPETITION INTEGRITY WARNING:** {missing_pct:.1f}% of telemetry data is missing. Scores may be statistically unreliable. A simulator re-fly is recommended.")
                    
                    col_metric1, col_metric2, col_metric3, col_metric4 = st.columns(4)
                    col_metric1.metric("Normalized Penalty", round(normalized_total_score, 2))
                    col_metric2.metric("Interpolated Penalty", round(interpolated_total_score, 2))
                    col_metric3.metric("Evaluated Flight Time", f"{total_expected_seconds} sec")
                    col_metric4.metric("Telemetry Data Loss", f"{missing_pct:.1f}%")
                    
                    st.subheader("🗺️ Overhead Map View")
                    fig_map, ax_map = plt.subplots(figsize=(10, 8))
                    
                    ax_map.plot(ideal_df['Target_X'], ideal_df['Target_Y'], label='Target Flight Path (Ideal)', linestyle='--', color='gray', linewidth=2)
                    ax_map.plot(map_x, map_y, label='Actual Flight Path (Curve-Fitted)', color='blue', linewidth=2, zorder=1)
                    
                    first_gap = True
                    for rx, ry in red_segments:
                        label = 'Data Gap (>3s)' if first_gap else ""
                        ax_map.plot(rx, ry, color='red', linewidth=3, label=label, zorder=2)
                        first_gap = False
                    
                    ax_map.set_aspect('equal', 'box')
                    ax_map.set_title("Derived XY Track (Top-Down View)")
                    ax_map.set_xlabel("Relative Distance East/West (Feet)")
                    ax_map.set_ylabel("Relative Distance North/South (Feet)")
                    ax_map.grid(True, linestyle=':', alpha=0.7)
                    ax_map.legend()
                    st.pyplot(fig_map)
                    
                    tmp_map = tempfile.NamedTemporaryFile(delete=False, suffix=".png").name
                    fig_map.savefig(tmp_map, bbox_inches='tight')
                    
                    st.subheader("Flight Telemetry vs Targets (Elapsed Time)")
                    tab1, tab2, tab3 = st.tabs(["Magnetic Heading", "Altitude", "Airspeed"])
                    
                    fig_head = plot_telemetry_chart(df, df_interp, 'Course', 'Target_Heading', 'Magnetic Heading Performance', 'Degrees')
                    with tab1: st.pyplot(fig_head)
                    tmp_head = tempfile.NamedTemporaryFile(delete=False, suffix=".png").name
                    fig_head.savefig(tmp_head, bbox_inches='tight')
                    
                    fig_alt = plot_telemetry_chart(df, df_interp, 'Altitude', 'Target_Altitude', 'Altitude Performance', 'Feet (MSL)')
                    with tab2: st.pyplot(fig_alt)
                    tmp_alt = tempfile.NamedTemporaryFile(delete=False, suffix=".png").name
                    fig_alt.savefig(tmp_alt, bbox_inches='tight')
                    
                    fig_spd = plot_telemetry_chart(df, df_interp, 'Airspeed', 'Target_Airspeed', 'Airspeed Performance', 'Knots')
                    with tab3: st.pyplot(fig_spd)
                    tmp_spd = tempfile.NamedTemporaryFile(delete=False, suffix=".png").name
                    fig_spd.savefig(tmp_spd, bbox_inches='tight')
                    
                    img_paths = {'map': tmp_map, 'head': tmp_head, 'alt': tmp_alt, 'spd': tmp_spd}
                        
                    st.subheader("Raw Data Table (Interpolated Engine)")
                    st.dataframe(df_interp)
                    
                    st.divider()
                    st.header("📥 Export Reports")
                    col_export1, col_export2 = st.columns(2)
                    
                    csv_data = df_interp.to_csv(index=False).encode('utf-8')
                    col_export1.download_button(
                        label="Download Full Interpolated CSV",
                        data=csv_data,
                        file_name=f"{c_name.replace(' ', '_')}_interpolated_data.csv" if c_name else 'interpolated_flight_data.csv',
                        mime='text/csv',
                        use_container_width=True
                    )
                    
                    pdf_path = generate_pdf(c_name, c_num, c_school, normalized_total_score, interpolated_total_score, total_expected_seconds, missing_pct, integrity_warning, df, img_paths)
                    
                    for path in img_paths.values():
                        try: os.remove(path)
                        except: pass
                    
                    with open(pdf_path, "rb") as pdf_file:
                        col_export2.download_button(
                            label="Download PDF Report Card",
                            data=pdf_file,
                            file_name=f"{c_name.replace(' ', '_')}_Scorecard.pdf" if c_name else "Flight_Grade_Report.pdf",
                            mime="application/pdf",
                            use_container_width=True
                        )