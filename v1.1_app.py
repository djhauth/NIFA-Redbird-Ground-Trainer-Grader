import streamlit as st
import pandas as pd
import xml.etree.ElementTree as ET
import numpy as np
from fpdf import FPDF
import tempfile

# --- APP CONFIGURATION ---
st.set_page_config(page_title="Flight Maneuver Grader", layout="wide")
st.title("✈️ Flight Maneuver Auto-Grader v2.0")
st.markdown("Upload your KML file, define your targets, set your parameters, and generate an automated grade report.")

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
    
    df['Turn_Rate_deg_sec'] = df['Course'].diff().abs().fillna(0)
    df['Climb_Rate_FPM'] = df['Altitude'].diff().fillna(0) * 60
    
    return df

# --- HELPER FUNCTION: GENERATE PDF ---
def generate_pdf(total_points, flight_time, df):
    pdf = FPDF()
    pdf.add_page()
    
    # Title
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, "Flight Maneuver Grade Report", ln=True, align="C")
    pdf.ln(10)
    
    # Summary Metrics
    pdf.set_font("Arial", "", 12)
    pdf.cell(0, 10, f"Total Penalty Points: {total_points:.2f}", ln=True)
    pdf.cell(0, 10, f"Evaluated Flight Time: {flight_time} seconds", ln=True)
    pdf.ln(10)
    
    # Deviations
    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 10, "Performance Summary (Max Deviations):", ln=True)
    pdf.set_font("Arial", "", 12)
    pdf.cell(0, 10, f"Max Altitude Deviation: {(df['Altitude_Penalty'].max() * 10):.0f} ft", ln=True)
    pdf.cell(0, 10, f"Max Heading Deviation: {df['Heading_Penalty'].max():.0f} degrees", ln=True)
    pdf.cell(0, 10, f"Max Airspeed Deviation: {df['Airspeed_Penalty'].max():.0f} knots", ln=True)
    
    # Save to a temporary file so Streamlit can download it
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        pdf.output(tmp.name)
        return tmp.name

# --- UI: FILE UPLOAD ---
st.header("1. Upload Flight Data")
uploaded_file = st.file_uploader("Upload Redbird KML File", type=['kml'])

# --- UI: CONFIGURATION & TRIMMING ---
st.header("2. Grading Parameters")
with st.expander("⚙️ Advanced Settings (Trimming & Grace Periods)", expanded=True):
    col1, col2, col3 = st.columns(3)
    trim_start = col1.number_input("Seconds to trim from START", min_value=0, value=0, help="Ignores data before the maneuver officially begins.")
    trim_end = col2.number_input("Seconds to trim from END", min_value=0, value=0, help="Ignores data after the maneuver ends.")
    grace_period = col3.number_input("Grace Period (seconds)", min_value=0, value=2, help="Seconds with zero penalty when a new leg begins.")

# --- UI: FLIGHT PLAN EDITOR ---
st.header("3. Define Flight Plan Targets")
default_plan = pd.DataFrame([
    {"Leg": 1, "Duration (sec)": 30, "Target Heading": 180, "Target Altitude": 3500, "Target Airspeed": 95},
    {"Leg": 2, "Duration (sec)": 120, "Target Heading": 180, "Target Altitude": 3500, "Target Airspeed": 85},
    {"Leg": 3, "Duration (sec)": 30, "Target Heading": 180, "Target Altitude": 3750, "Target Airspeed": 75}
])
edited_plan = st.data_editor(default_plan, num_rows="dynamic", use_container_width=True)

# --- PROCESSING & GRADING LOGIC ---
if uploaded_file is not None:
    if st.button("Run Analysis & Calculate Grade", type="primary"):
        with st.spinner("Crunching the numbers and building charts..."):
            df = parse_kml(uploaded_file)
            
            # Trim the data based on user input
            if trim_start > 0:
                df = df.iloc[trim_start:]
            if trim_end > 0:
                df = df.iloc[:-trim_end]
            df = df.reset_index(drop=True)
            
            total_seconds = len(df)
            
            # Map targets and determine grace periods
            target_headings = []
            target_altitudes = []
            target_airspeeds = []
            grace_mask = []
            
            for index, row in edited_plan.iterrows():
                duration = int(row['Duration (sec)'])
                target_headings.extend([row['Target Heading']] * duration)
                target_altitudes.extend([row['Target Altitude']] * duration)
                target_airspeeds.extend([row['Target Airspeed']] * duration)
                
                # Build Grace Period mask (True = no penalty, False = standard penalty)
                leg_grace = [True] * min(grace_period, duration) + [False] * max(0, duration - grace_period)
                grace_mask.extend(leg_grace)
            
            # Match lengths
            if len(target_headings) < total_seconds:
                diff = total_seconds - len(target_headings)
                target_headings.extend([target_headings[-1]] * diff)
                target_altitudes.extend([target_altitudes[-1]] * diff)
                target_airspeeds.extend([target_airspeeds[-1]] * diff)
                grace_mask.extend([False] * diff)
            else:
                target_headings = target_headings[:total_seconds]
                target_altitudes = target_altitudes[:total_seconds]
                target_airspeeds = target_airspeeds[:total_seconds]
                grace_mask = grace_mask[:total_seconds]
                
            df['Target_Heading'] = target_headings
            df['Target_Altitude'] = target_altitudes
            df['Target_Airspeed'] = target_airspeeds
            df['In_Grace_Period'] = grace_mask
            
            # Calculate Base Penalties
            df['Heading_Penalty'] = (df['Course'] - df['Target_Heading']).abs() * 1
            df['Altitude_Penalty'] = (df['Altitude'] - df['Target_Altitude']).abs() / 10
            df['Airspeed_Penalty'] = (df['Airspeed'] - df['Target_Airspeed']).abs() * 1
            
            TARGET_TURN_RATE = 3.0 
            TARGET_CLIMB_RATE = 500.0
            
            df['Turn_Penalty'] = np.where(df['Turn_Rate_deg_sec'] > 0.5, (df['Turn_Rate_deg_sec'] - TARGET_TURN_RATE).abs() * 25, 0)
            df['Climb_Penalty'] = np.where(df['Climb_Rate_FPM'].abs() > 100, (df['Climb_Rate_FPM'].abs() - TARGET_CLIMB_RATE).abs() / 100 * 2, 0)
            
            # Apply Grace Period (Zero out penalties if In_Grace_Period is True)
            penalty_cols = ['Heading_Penalty', 'Altitude_Penalty', 'Airspeed_Penalty', 'Turn_Penalty', 'Climb_Penalty']
            df.loc[df['In_Grace_Period'] == True, penalty_cols] = 0
            
            df['Total_Penalty'] = df[penalty_cols].sum(axis=1)
            total_score = df['Total_Penalty'].sum()
            
            # --- RESULTS DASHBOARD ---
            st.success("Analysis Complete!")
            st.divider()
            
            # Top Level Metrics
            st.header("📊 Performance Results")
            col_metric1, col_metric2 = st.columns(2)
            col_metric1.metric("Total Penalty Points", round(total_score, 2))
            col_metric2.metric("Total Flight Time Evaluated", f"{total_seconds} seconds")
            
            # Visual Charts
            st.subheader("Flight Telemetry vs Targets")
            
            tab1, tab2, tab3 = st.tabs(["Altitude", "Heading", "Airspeed"])
            
            with tab1:
                st.line_chart(df[['Altitude', 'Target_Altitude']])
            with tab2:
                st.line_chart(df[['Course', 'Target_Heading']])
            with tab3:
                st.line_chart(df[['Airspeed', 'Target_Airspeed']])
                
            st.subheader("Raw Data Table")
            st.dataframe(df)
            
            st.divider()
            st.header("📥 Export Reports")
            
            col_export1, col_export2 = st.columns(2)
            
            # 1. CSV Download
            csv_data = df.to_csv(index=False).encode('utf-8')
            col_export1.download_button(
                label="Download Full CSV Spreadsheet",
                data=csv_data,
                file_name='graded_flight_data.csv',
                mime='text/csv',
                use_container_width=True
            )
            
            # 2. PDF Download
            pdf_path = generate_pdf(total_score, total_seconds, df)
            with open(pdf_path, "rb") as pdf_file:
                col_export2.download_button(
                    label="Download PDF Report Card",
                    data=pdf_file,
                    file_name="Flight_Grade_Report.pdf",
                    mime="application/pdf",
                    use_container_width=True
                )