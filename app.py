import streamlit as st
import pandas as pd
import xml.etree.ElementTree as ET
import numpy as np

# --- APP CONFIGURATION ---
st.set_page_config(page_title="Flight Maneuver Grader", layout="wide")
st.title("✈️ Flight Maneuver Auto-Grader")
st.markdown("Upload your ForeFlight/Redbird KML file, define your flight plan targets, and generate an automated grade report.")

# --- HELPER FUNCTION: PARSE KML ---
def parse_kml(uploaded_file):
    tree = ET.parse(uploaded_file)
    root = tree.getroot()
    
    # KML Namespaces
    ns = {'kml': 'http://www.opengis.net/kml/2.2', 'gx': 'http://www.google.com/kml/ext/2.2'}
    
    # Extract Timestamps
    times = [elem.text for elem in root.findall('.//kml:when', ns)]
    
    # Helper to extract gx:SimpleArrayData by name
    def get_array_data(name):
        path = f".//gx:SimpleArrayData[@name='{name}']/gx:value"
        return [float(elem.text) for elem in root.findall(path, ns)]
    
    # Extract required telemetry
    data = {
        'Timestamp': times,
        'Course': get_array_data('course'),
        'Altitude': get_array_data('altitude'),
        'Airspeed': get_array_data('speed_kts')
    }
    
    # Ensure all columns are the same length
    min_len = min(len(times), len(data['Course']), len(data['Altitude']), len(data['Airspeed']))
    for key in data.keys():
        data[key] = data[key][:min_len]
        
    df = pd.DataFrame(data)
    df['Timestamp'] = pd.to_datetime(df['Timestamp'])
    
    # Calculate Rates
    df['Turn_Rate_deg_sec'] = df['Course'].diff().abs().fillna(0)
    df['Climb_Rate_FPM'] = df['Altitude'].diff().fillna(0) * 60
    
    return df

# --- UI: FILE UPLOAD ---
st.header("1. Upload Flight Data")
uploaded_file = st.file_uploader("Upload Redbird KML File", type=['kml'])

# --- UI: FLIGHT PLAN EDITOR ---
st.header("2. Define Flight Plan (Targets)")
st.markdown("Enter the duration (in seconds) and the targets for each leg of the maneuver.")

# Default flight plan matching the start of your rubric
default_plan = pd.DataFrame([
    {"Leg": 1, "Duration (sec)": 30, "Target Heading": 180, "Target Altitude": 3500, "Target Airspeed": 95},
    {"Leg": 2, "Duration (sec)": 120, "Target Heading": 180, "Target Altitude": 3500, "Target Airspeed": 85},
    {"Leg": 3, "Duration (sec)": 30, "Target Heading": 180, "Target Altitude": 3750, "Target Airspeed": 75}
])

edited_plan = st.data_editor(default_plan, num_rows="dynamic", use_container_width=True)

# --- PROCESSING & GRADING LOGIC ---
if uploaded_file is not None:
    if st.button("Run Analysis & Calculate Grade"):
        with st.spinner("Crunching the numbers..."):
            # Parse KML
            df = parse_kml(uploaded_file)
            
            # Map targets to second-by-second data
            target_headings = []
            target_altitudes = []
            target_airspeeds = []
            
            for index, row in edited_plan.iterrows():
                duration = int(row['Duration (sec)'])
                target_headings.extend([row['Target Heading']] * duration)
                target_altitudes.extend([row['Target Altitude']] * duration)
                target_airspeeds.extend([row['Target Airspeed']] * duration)
            
            # Trim or pad targets to match the length of the telemetry data
            total_seconds = len(df)
            if len(target_headings) < total_seconds:
                # Pad with the last known target if telemetry outlasts the flight plan
                target_headings.extend([target_headings[-1]] * (total_seconds - len(target_headings)))
                target_altitudes.extend([target_altitudes[-1]] * (total_seconds - len(target_altitudes)))
                target_airspeeds.extend([target_airspeeds[-1]] * (total_seconds - len(target_airspeeds)))
            else:
                # Trim if flight plan outlasts telemetry
                target_headings = target_headings[:total_seconds]
                target_altitudes = target_altitudes[:total_seconds]
                target_airspeeds = target_airspeeds[:total_seconds]
                
            df['Target_Heading'] = target_headings
            df['Target_Altitude'] = target_altitudes
            df['Target_Airspeed'] = target_airspeeds
            
            # Calculate Penalties based on rubric
            df['Heading_Penalty'] = (df['Course'] - df['Target_Heading']).abs() * 1
            df['Altitude_Penalty'] = (df['Altitude'] - df['Target_Altitude']).abs() / 10
            df['Airspeed_Penalty'] = (df['Airspeed'] - df['Target_Airspeed']).abs() * 1
            
            # Standard rates (assuming all maneuvers default to these when turning/climbing)
            # For a more advanced app, you'd map specific turn/climb targets per leg
            TARGET_TURN_RATE = 3.0 
            TARGET_CLIMB_RATE = 500.0
            
            # Only penalize rate deviations if the aircraft is actively turning or climbing/descending
            df['Turn_Penalty'] = np.where(df['Turn_Rate_deg_sec'] > 0.5, (df['Turn_Rate_deg_sec'] - TARGET_TURN_RATE).abs() * 25, 0)
            df['Climb_Penalty'] = np.where(df['Climb_Rate_FPM'].abs() > 100, (df['Climb_Rate_FPM'].abs() - TARGET_CLIMB_RATE).abs() / 100 * 2, 0)
            
            df['Total_Penalty'] = df['Heading_Penalty'] + df['Altitude_Penalty'] + df['Airspeed_Penalty'] + df['Turn_Penalty'] + df['Climb_Penalty']
            
            # --- RESULTS DASHBOARD ---
            st.success("Analysis Complete!")
            st.header("📊 Results")
            
            col1, col2 = st.columns(2)
            col1.metric("Total Penalty Points", round(df['Total_Penalty'].sum(), 2))
            col2.metric("Total Flight Time Evaluated", f"{total_seconds} seconds")
            
            st.subheader("Second-by-Second Breakdown")
            st.dataframe(df)
            
            # Download button
            csv = df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Download Full Report as CSV",
                data=csv,
                file_name='graded_flight_data.csv',
                mime='text/csv',
            )