import streamlit as st
import pandas as pd
import xml.etree.ElementTree as ET
import numpy as np
from fpdf import FPDF
import tempfile
import matplotlib.pyplot as plt
import os

# --- APP CONFIGURATION ---
st.set_page_config(page_title="Flight Maneuver Grader", layout="wide")
st.title("✈️ Flight Maneuver Auto-Grader v9.0")
st.markdown("Upload your KML file, set the evaluator's start/stop times, map the flight plan, and generate an automated grade report.")

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
    
    # Calculate time difference to normalize rates during data gaps
    time_diff_sec = df['Timestamp'].diff().dt.total_seconds().fillna(1)
    time_diff_sec = np.where(time_diff_sec == 0, 1, time_diff_sec)
    
    # Calculate actual rates normalized by time
    course_diff = df['Course'].diff().fillna(0).abs()
    circular_diff = np.minimum(course_diff, 360 - course_diff)
    df['Turn_Rate_deg_sec'] = circular_diff / time_diff_sec
    
    df['Climb_Rate_FPM'] = (df['Altitude'].diff().fillna(0) / time_diff_sec) * 60
    
    return df

# --- HELPER FUNCTION: GENERATE PDF ---
def generate_pdf(total_points, flight_time, df):
    pdf = FPDF()
    pdf.add_page()
    
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, "Flight Maneuver Grade Report", ln=True, align="C")
    pdf.ln(10)
    
    pdf.set_font("Arial", "", 12)
    pdf.cell(0, 10, f"Total Penalty Points: {total_points:.2f}", ln=True)
    pdf.cell(0, 10, f"Evaluated Flight Time: {flight_time} seconds", ln=True)
    pdf.ln(10)
    
    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 10, "Performance Summary (Max Deviations):", ln=True)
    pdf.set_font("Arial", "", 12)
    pdf.cell(0, 10, f"Max Altitude Penalty: {df['Altitude_Penalty'].max():.2f} pts", ln=True)
    pdf.cell(0, 10, f"Max Heading Penalty: {df['Heading_Penalty'].max():.2f} pts", ln=True)
    pdf.cell(0, 10, f"Max Airspeed Penalty: {df['Airspeed_Penalty'].max():.2f} pts", ln=True)
    
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        pdf.output(tmp.name)
        return tmp.name

# --- UI: FILE UPLOAD ---
st.header("1. Upload Flight Data")
uploaded_file = st.file_uploader("Upload Redbird KML File", type=['kml'])

# --- UI: FLIGHT PLAN EDITOR ---
st.header("2. Define Flight Plan Targets")
st.info("Use numbers for static holds (e.g., '180', '3500'). Use 'Left Turn', 'Right Turn', 'Climb', or 'Descend' for dynamic legs.")

PLAN_FILE = "saved_flight_plan.csv"

if os.path.exists(PLAN_FILE):
    default_plan = pd.read_csv(PLAN_FILE)
else:
    default_plan = pd.DataFrame([
        {"Leg": 1, "Duration (sec)": 30, "Heading": "180", "Altitude": "3500", "Airspeed": 95},
        {"Leg": 2, "Duration (sec)": 120, "Heading": "Left Turn", "Altitude": "3500", "Airspeed": 85},
        {"Leg": 3, "Duration (sec)": 30, "Heading": "180", "Altitude": "Climb", "Airspeed": 75},
        {"Leg": 4, "Duration (sec)": 30, "Heading": "Left Turn", "Altitude": "3750", "Airspeed": 85},
        {"Leg": 5, "Duration (sec)": 30, "Heading": "090", "Altitude": "Descend", "Airspeed": 90}
    ])

edited_plan = st.data_editor(default_plan, num_rows="dynamic", use_container_width=True)
edited_plan.to_csv(PLAN_FILE, index=False)

# --- UI: QUALITY ASSURANCE & CONFIGURATION ---
st.header("3. Quality Assurance & Time Window")

if uploaded_file is not None:
    raw_df = parse_kml(uploaded_file)
    time_options = raw_df['Timestamp'].dt.strftime('%H:%M:%S').unique().tolist()
    
    st.subheader("🔍 Pre-Flight Data Check")
    
    # Time Gap Check
    time_diffs = raw_df['Timestamp'].diff().dt.total_seconds()
    gaps = time_diffs[time_diffs > 1.5]
    
    if len(gaps) > 0:
        st.warning(f"⚠️ **Data Gap Detected:** Found {len(gaps)} instance(s) of missing data. The auto-grader will naturally skip these gaps.")
    else:
        st.success("✅ **Telemetry Continuous:** No missing data gaps detected.")
        
    # Trim Recommendation
    try:
        leg1_heading = float(edited_plan.iloc[0]['Heading'])
        leg1_alt = float(str(edited_plan.iloc[0]['Altitude']).replace(',', ''))
        leg1_spd = float(edited_plan.iloc[0]['Airspeed'])
        
        head_diff = (raw_df['Course'] - leg1_heading).abs()
        circular_head_diff = np.minimum(head_diff, 360 - head_diff)
        
        match_idx = raw_df[
            (circular_head_diff <= 5) &
            ((raw_df['Altitude'] - leg1_alt).abs() <= 50) &
            ((raw_df['Airspeed'] - leg1_spd).abs() <= 5)
        ].index
        
        if len(match_idx) > 0:
            rec_idx = int(match_idx[0])
            rec_time = raw_df.loc[rec_idx, 'Timestamp'].strftime('%H:%M:%S')
            if rec_idx > 0:
                st.info(f"💡 **Trim Recommendation:** The aircraft stabilizes on Leg 1 targets at **{rec_time}** (UTC). Consider selecting this as your Start Time.")
            else:
                st.success("✅ **Clean Start:** The aircraft is already on target at the very beginning of the file.")
        else:
            st.warning("⚠️ **Target Mismatch:** The aircraft never stabilized on the Leg 1 targets in this file.")
    except:
        pass 

    st.subheader("⏱️ Evaluator Grading Window")
    col1, col2, col3 = st.columns(3)
    start_time_sel = col1.selectbox("Grading Start Time (UTC)", options=time_options, index=0)
    end_time_sel = col2.selectbox("Grading End Time (UTC)", options=time_options, index=len(time_options)-1)
    grace_period = col3.number_input("Leg Transition Grace Period (seconds)", min_value=0, value=2)

    if start_time_sel >= end_time_sel:
        st.error("🚨 Start Time must be before End Time!")
    else:
        # --- PROCESSING & GRADING LOGIC ---
        if st.button("Run Analysis & Calculate Grade", type="primary"):
            with st.spinner("Aligning clock vectors and mapping flight paths..."):
                
                # Apply the specific time window
                df = raw_df.copy()
                mask = (df['Timestamp'].dt.strftime('%H:%M:%S') >= start_time_sel) & \
                       (df['Timestamp'].dt.strftime('%H:%M:%S') <= end_time_sel)
                df = df[mask].reset_index(drop=True)
                
                t0 = df['Timestamp'].iloc[0]
                df['Elapsed_Sec'] = (df['Timestamp'] - t0).dt.total_seconds().astype(int)
                total_clock_time = df['Elapsed_Sec'].max()
                
                master_headings, master_altitudes, master_airspeeds = [], [], []
                master_turn_rates, master_climb_rates, master_grace = [], [], []
                
                try:
                    curr_heading = float(edited_plan.iloc[0]['Heading'])
                    curr_altitude = float(str(edited_plan.iloc[0]['Altitude']).replace(',', ''))
                except:
                    curr_heading, curr_altitude = 360.0, 0.0

                for index, row in edited_plan.iterrows():
                    duration = int(row['Duration (sec)'])
                    h_cmd = str(row['Heading']).strip().lower()
                    a_cmd = str(row['Altitude']).strip().lower()
                    
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
                        master_airspeeds.append(float(row['Airspeed']))
                        master_turn_rates.append(tgt_turn_rate)
                        master_climb_rates.append(tgt_climb_rate)
                
                def get_tgt(sec, lst, default):
                    if sec < len(lst): return lst[sec]
                    if len(lst) > 0: return lst[-1]
                    return default
                    
                df['Target_Heading'] = df['Elapsed_Sec'].apply(lambda x: get_tgt(x, master_headings, 360.0))
                df['Target_Altitude'] = df['Elapsed_Sec'].apply(lambda x: get_tgt(x, master_altitudes, 0.0))
                df['Target_Airspeed'] = df['Elapsed_Sec'].apply(lambda x: get_tgt(x, master_airspeeds, 0.0))
                df['Target_Turn_Rate'] = df['Elapsed_Sec'].apply(lambda x: get_tgt(x, master_turn_rates, 0.0))
                df['Target_Climb_Rate'] = df['Elapsed_Sec'].apply(lambda x: get_tgt(x, master_climb_rates, 0.0))
                df['In_Grace_Period'] = df['Elapsed_Sec'].apply(lambda x: get_tgt(x, master_grace, False))
                
                # --- CARTESIAN XY MAP MATH ---
                dt_sec = df['Timestamp'].diff().dt.total_seconds().fillna(1)
                df['Actual_dX'] = df['Airspeed'] * 1.68781 * np.sin(np.radians(df['Course'])) * dt_sec
                df['Actual_dY'] = df['Airspeed'] * 1.68781 * np.cos(np.radians(df['Course'])) * dt_sec
                df['Actual_X'] = df['Actual_dX'].cumsum()
                df['Actual_Y'] = df['Actual_dY'].cumsum()

                ideal_df = pd.DataFrame({
                    'Target_Heading': master_headings,
                    'Target_Airspeed': master_airspeeds
                })
                ideal_df['Target_dX'] = ideal_df['Target_Airspeed'] * 1.68781 * np.sin(np.radians(ideal_df['Target_Heading'])) * 1.0
                ideal_df['Target_dY'] = ideal_df['Target_Airspeed'] * 1.68781 * np.cos(np.radians(ideal_df['Target_Heading'])) * 1.0
                ideal_df['Target_X'] = ideal_df['Target_dX'].cumsum()
                ideal_df['Target_Y'] = ideal_df['Target_dY'].cumsum()

                # --- CALCULATE PENALTIES ---
                head_diff = (df['Course'] - df['Target_Heading']).abs()
                df['Heading_Penalty'] = np.minimum(head_diff, 360 - head_diff) * 1
                
                df['Altitude_Penalty'] = (df['Altitude'] - df['Target_Altitude']).abs() / 10
                df['Airspeed_Penalty'] = (df['Airspeed'] - df['Target_Airspeed']).abs() * 1
                
                df['Turn_Penalty'] = (df['Turn_Rate_deg_sec'] - df['Target_Turn_Rate']).abs() * 25
                df['Climb_Penalty'] = (df['Climb_Rate_FPM'] - df['Target_Climb_Rate']).abs() / 100 * 2
                
                penalty_cols = ['Heading_Penalty', 'Altitude_Penalty', 'Airspeed_Penalty', 'Turn_Penalty', 'Climb_Penalty']
                df.loc[df['In_Grace_Period'] == True, penalty_cols] = 0
                
                df['Total_Penalty'] = df[penalty_cols].sum(axis=1)
                total_score = df['Total_Penalty'].sum()
                
                # --- RESULTS DASHBOARD ---
                st.success("Analysis Complete!")
                st.divider()
                
                st.header("📊 Performance Results")
                col_metric1, col_metric2 = st.columns(2)
                col_metric1.metric("Total Penalty Points", round(total_score, 2))
                col_metric2.metric("Total Flight Time Evaluated", f"{total_clock_time} seconds")
                
                st.subheader("🗺️ Overhead Map View")
                fig, ax = plt.subplots(figsize=(10, 8))
                
                ax.plot(ideal_df['Target_X'], ideal_df['Target_Y'], label='Target Flight Path (Ideal)', linestyle='--', color='red', linewidth=2)
                ax.plot(df['Actual_X'], df['Actual_Y'], label='Actual Flight Path (Flown)', color='blue', linewidth=2)
                
                ax.set_aspect('equal', 'box')
                ax.set_title("Derived XY Track (Top-Down View)")
                ax.set_xlabel("Relative Distance East/West (Feet)")
                ax.set_ylabel("Relative Distance North/South (Feet)")
                ax.grid(True, linestyle=':', alpha=0.7)
                ax.legend()
                st.pyplot(fig)
                
                st.subheader("Flight Telemetry vs Targets (Elapsed Time)")
                tab1, tab2, tab3 = st.tabs(["Heading", "Altitude", "Airspeed"])
                with tab1: st.line_chart(data=df.set_index('Elapsed_Sec')[['Course', 'Target_Heading']])
                with tab2: st.line_chart(data=df.set_index('Elapsed_Sec')[['Altitude', 'Target_Altitude']])
                with tab3: st.line_chart(data=df.set_index('Elapsed_Sec')[['Airspeed', 'Target_Airspeed']])
                    
                st.subheader("Raw Data Table")
                st.dataframe(df)
                
                st.divider()
                st.header("📥 Export Reports")
                col_export1, col_export2 = st.columns(2)
                
                csv_data = df.to_csv(index=False).encode('utf-8')
                col_export1.download_button(
                    label="Download Full CSV Spreadsheet",
                    data=csv_data,
                    file_name='graded_flight_data.csv',
                    mime='text/csv',
                    use_container_width=True
                )
                
                pdf_path = generate_pdf(total_score, total_clock_time, df)
                with open(pdf_path, "rb") as pdf_file:
                    col_export2.download_button(
                        label="Download PDF Report Card",
                        data=pdf_file,
                        file_name="Flight_Grade_Report.pdf",
                        mime="application/pdf",
                        use_container_width=True
                    )