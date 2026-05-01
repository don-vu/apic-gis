import streamlit as st
import folium
from streamlit_folium import st_folium
import geopandas as gpd
from shapely.geometry import box
import os
import pandas as pd

# Grid Configuration
ELEMENT_CONFIG = {
    "bus": {"color": "#2979FF", "radius": 3, "label": "Bus"},
    "load": {"color": "#FF5252", "radius": 4, "label": "Load"},
    "sgen": {"color": "#FFEA00", "radius": 4, "label": "Static Gen"},
    "gen": {"color": "#FFAB40", "radius": 5, "label": "Generators"},
    "switch": {"color": "#B0BEC5", "radius": 3, "label": "Switches"},
    "shunt": {"color": "#7C4DFF", "radius": 4, "label": "Shunts"},
    "ext_grid": {"color": "#00E676", "radius": 6, "label": "External Grid"},
    "line": {"color": "#00E5FF", "weight": 3, "label": "Line"},
    "trafo": {"color": "#F50057", "weight": 4, "label": "Transformers"},
}
ELEMENT_TYPES = ["bus", "load", "sgen", "gen", "switch", "shunt", "ext_grid", "line", "trafo"]

st.set_page_config(page_title="Solar Labs Ltd.", layout="wide")

st.markdown("""
    <style>
        .block-container { padding: 0rem !important; max-width: 100% !important; }
        header {visibility: hidden;}
        footer {visibility: hidden;}
        .stApp { overflow: hidden !important; }
        .stSpinner { position: fixed; top: 50%; left: 50%; z-index: 9999; }
    </style>
""", unsafe_allow_html=True)

@st.cache_data
def load_full_data():
    # Buildings
    gdf = gpd.read_parquet("./data/output/merged_buildings.parquet")
    if gdf.crs is None:
        gdf = gdf.set_crs(epsg=4326)
    else:
        gdf = gdf.to_crs(epsg=4326)

    # Calculations
    gdf['solar_potential_kwh'] = gdf['area'] * 0.7 * 1246 * 0.2
    gdf['money_saved'] = gdf['solar_potential_kwh'] * 0.15
    gdf['co2_saved_tonnes'] = (gdf['solar_potential_kwh'] * 0.424) / 1000
    gdf['homes_powered'] = gdf['solar_potential_kwh'] / 7200
    gdf['evs_charged'] = gdf['solar_potential_kwh'] / 3040
    
    gdf['geometry'] = gdf['geometry'].simplify(0.00001, preserve_topology=True)
    
    # Tooltip
    gdf["tooltip_html"] = gdf.apply(
        lambda row: f"""
        <div style='font-family: sans-serif; padding: 10px; min-width: 150px;'>
            <h4 style='margin-top: 0; color: #FF9F00;'>Building #{row.building_id}</h4>
            <b>Roof Area:</b> {row.area:,.0f} m²<br>
            <b>Energy Potential:</b> {row.solar_potential_kwh:,.0f} kWh/yr<br>
            <b>Est. Savings:</b> <span style='color: green;'>${row.money_saved:,.0f}/yr</span>
        </div>
        """,
        axis=1
    )
    
    # Center calculation
    if not gdf.empty:
        try:
            # Use the center of the bounding box for a better default view
            bounds = gdf.total_bounds
            center = ((bounds[1] + bounds[3]) / 2, (bounds[0] + bounds[2]) / 2)
        except Exception:
            # Fallback to Edmonton center
            center = (53.5461, -113.4938)
    else:
        center = (53.5461, -113.4938)
    
    # Grid
    circuit_path = "./data/output/circuit_network.parquet"
    circuit_gdf = None
    if os.path.exists(circuit_path):
        circuit_gdf = gpd.read_parquet(circuit_path)
        if circuit_gdf.crs is None:
            circuit_gdf = circuit_gdf.set_crs(epsg=4326)
        else:
            circuit_gdf = circuit_gdf.to_crs(epsg=4326)
        circuit_gdf['geometry'] = circuit_gdf['geometry'].simplify(0.00005, preserve_topology=True)
        if 'element_type' not in circuit_gdf.columns:
            circuit_gdf['element_type'] = 'line'
        
        useful_cols = [
            'geometry', 'element_type', 'name', 'vn_kv', 'p_mw', 'q_mvar', 
            'length_km', 'sn_mva', 'index', 'from_bus', 'to_bus', 
            'r_ohm_per_km', 'x_ohm_per_km', 'c_nf_per_km', 'g_us_per_km', 'bus'
        ]
        cols_to_keep = [c for c in useful_cols if c in circuit_gdf.columns]
        
        # Dynamic Tooltip for Grid
        def make_circuit_tooltip(row):
            etype = row.get('element_type', 'element')
            config = ELEMENT_CONFIG.get(etype, {"color": "#2979FF", "label": etype.capitalize()})
            color = config.get("color", "#2979FF")
            label_text = config.get("label", etype.capitalize())
            
            html = f"<div style='font-family: sans-serif; padding: 10px; min-width: 150px;'>"
            html += f"<h4 style='margin-top: 0; color: {color};'>{label_text}</h4>"
            
            if etype == "line":
                field_labels = {
                    'name': 'Name',
                    'from_bus': 'From Bus',
                    'to_bus': 'To Bus',
                    'length_km': 'Length (km)',
                    'r_ohm_per_km': 'R (Ohm/km)',
                    'x_ohm_per_km': 'X (Ohm/km)',
                    'c_nf_per_km': 'C (nF/km)'
                }
            elif etype == "load":
                field_labels = {
                    'name': 'Name',
                    'bus': 'Bus',
                    'p_mw': 'Active Power (MW)',
                    'q_mvar': 'Reactive Power (MVAR)'
                }
            elif etype == "bus":
                field_labels = {
                    'name': 'Name',
                    'vn_kv': 'Voltage (kV)'
                }
            else:
                field_labels = {
                    'name': 'Name',
                    'vn_kv': 'Voltage (kV)',
                    'p_mw': 'Active Power (MW)',
                    'q_mvar': 'Reactive Power (MVAR)',
                    'length_km': 'Length (km)',
                    'sn_mva': 'Rated Power (MVA)',
                    'index': 'ID'
                }
            
            for col, label in field_labels.items():
                if col in row.index and pd.notnull(row[col]):
                    val = row[col]
                    if isinstance(val, str) and (val.strip() == "" or val.lower() == "nan"):
                        continue
                    if isinstance(val, (int, float)) and pd.isna(val):
                        continue
                    
                    # Format IDs/Buses as integers with no commas
                    if col in ['from_bus', 'to_bus', 'bus', 'index']:
                        try:
                            html += f"<b>{label}:</b> {int(float(val))}<br>"
                        except (ValueError, TypeError):
                            html += f"<b>{label}:</b> {val}<br>"
                    elif isinstance(val, (int, float)):
                        html += f"<b>{label}:</b> {val:,.2f}<br>"
                    else:
                        html += f"<b>{label}:</b> {val}<br>"
            
            html += "</div>"
            return html

        circuit_gdf['tooltip_html'] = circuit_gdf.apply(make_circuit_tooltip, axis=1)
        circuit_gdf = circuit_gdf[cols_to_keep + ['tooltip_html']]

    return gdf, circuit_gdf, center

with st.spinner("Loading Edmonton Solar & Grid Data..."):
    full_gdf, full_circuit_gdf, default_center = load_full_data()

# Initialize session state for viewport
if "center" not in st.session_state:
    st.session_state.center = default_center
if "zoom" not in st.session_state:
    st.session_state.zoom = 18

# Viewport Filtering Logic
def get_visible_data(gdf, circuit_gdf, bounds):
    if bounds is None:
        # Default view: small box around center
        lat, lon = st.session_state.center
        buffer = 0.005
        view_box = box(lon - buffer, lat - buffer, lon + buffer, lat + buffer)
    else:
        sw = bounds["_southWest"]
        ne = bounds["_northEast"]
        view_box = box(sw["lng"], sw["lat"], ne["lng"], ne["lat"])
    
    # Filter buildings
    spatial_index = gdf.sindex
    possible_indices = list(spatial_index.intersection(view_box.bounds))
    visible_gdf = gdf.iloc[possible_indices].copy()
    visible_gdf = visible_gdf[visible_gdf.geometry.intersects(view_box)]
    
    # Filter circuit (only if zoomed in)
    visible_circuit = None
    if circuit_gdf is not None and st.session_state.zoom >= 16:
        c_spatial_index = circuit_gdf.sindex
        c_possible_indices = list(c_spatial_index.intersection(view_box.bounds))
        visible_circuit = circuit_gdf.iloc[c_possible_indices].copy()
        visible_circuit = visible_circuit[visible_circuit.geometry.intersects(view_box)]
        
    return visible_gdf, visible_circuit

last_map_data = st.session_state.get("last_map_data", None)
current_bounds = last_map_data.get("bounds") if last_map_data else None

# Filter data for the CURRENT render
visible_buildings, visible_grid = get_visible_data(full_gdf, full_circuit_gdf, current_bounds)

m = folium.Map(
    location=st.session_state.center,
    zoom_start=st.session_state.zoom,
    tiles="http://mt0.google.com/vt/lyrs=s&hl=en&x={x}&y={y}&z={z}",
    attr="Google Satellite",
    max_zoom=20,
    zoom_control=True
)

# Add grid layers
if visible_grid is not None and len(visible_grid) > 0:
    for etype in ELEMENT_TYPES:
        subset = visible_grid[visible_grid['element_type'] == etype]
        if not subset.empty:
            config = ELEMENT_CONFIG.get(etype, {"color": "#999999", "label": etype.capitalize()})
            fg = folium.FeatureGroup(name=config["label"]).add_to(m)
            
            folium.GeoJson(
                subset[['geometry', 'tooltip_html']],
                style_function=lambda x, color=config["color"], weight=config.get("weight", 2): {
                    "color": color,
                    "weight": weight,
                    "opacity": 0.8,
                },
                marker=folium.CircleMarker(
                    radius=config.get("radius", 3),
                    color=config["color"],
                    fill=True,
                    fill_opacity=0.9
                ) if "radius" in config else None,
                tooltip=folium.GeoJsonTooltip(
                    fields=["tooltip_html"],
                    aliases=[""],
                    labels=False,
                    sticky=True,
                    max_width=300
                )
            ).add_to(fg)

# Add buildings
if len(visible_buildings) > 0:
    folium.GeoJson(
        visible_buildings[['geometry', 'tooltip_html']],
        name="Solar Potential",
        tooltip=folium.GeoJsonTooltip(
            fields=["tooltip_html"],
            aliases=[""],
            labels=False,
            sticky=True,
            max_width=300
        ),
        style_function=lambda x: {
            "fillColor": "#FFB300",
            "color": "#FF8F00",
            "weight": 2,
            "fillOpacity": 0.5,
        },
    ).add_to(m)

folium.LayerControl(position='bottomright', collapsed=False).add_to(m)

# Render map
map_output = st_folium(
    m, 
    width="100%", 
    height=900, 
    key="solar_map", 
    returned_objects=["bounds", "center", "zoom"]
)

# Update session state and rerun if the view changed
if map_output:
    changed = False
    if map_output.get("center") and map_output["center"] != st.session_state.center:
        st.session_state.center = (map_output["center"]["lat"], map_output["center"]["lng"])
        changed = True
    if map_output.get("zoom") and map_output["zoom"] != st.session_state.zoom:
        st.session_state.zoom = map_output["zoom"]
        changed = True
    
    if changed:
        st.session_state.last_map_data = map_output
        st.rerun()

total_buildings = len(visible_buildings)
total_kwh = visible_buildings['solar_potential_kwh'].sum()
total_savings = visible_buildings['money_saved'].sum()
total_co2 = visible_buildings['co2_saved_tonnes'].sum()
total_homes = visible_buildings['homes_powered'].sum()
total_evs = visible_buildings['evs_charged'].sum()

st.markdown(f'''
     <div style="
     position: fixed; top: 20px; right: 20px; width: 320px; 
     background-color: rgba(255, 255, 255, 0.98); 
     border: 1px solid #e0e0e0; z-index: 99999; font-family: sans-serif;
     border-radius: 12px; padding: 18px 20px; 
     box-shadow: 0px 8px 24px rgba(0,0,0,0.15);
     ">
     
     <h4 style="color: #111; margin: 0 0 4px 0; font-size: 15px; font-weight: 800; letter-spacing: 0.5px; text-transform: uppercase;">
         Live Screen Analytics
     </h4>
     
     <div style="display: flex; align-items: baseline; margin-bottom: 2px;">
         <span style="font-weight: 600; color: #555; font-size: 14px; width: 140px;">Buildings Detected:</span>
         <span style="font-size: 18px; font-weight: 700; color: #1a1a1a;">{total_buildings}</span>
     </div>
     
     <div style="display: flex; align-items: baseline; margin-bottom: 2px;">
         <span style="font-weight: 600; color: #555; font-size: 14px; width: 140px;">Energy Potential:</span>
         <span style="font-size: 18px; font-weight: 700; color: #FF9F00;">{total_kwh:,.0f} <span style="font-size: 12px;">kWh/yr</span></span>
     </div>
     
     <div style="display: flex; align-items: baseline; margin-bottom: 12px; border-bottom: 1px solid #f0f0f0; padding-bottom: 12px;">
         <span style="font-weight: 600; color: #555; font-size: 14px; width: 140px;">Est. Savings:</span>
         <span style="font-size: 18px; font-weight: 700; color: #10B981;">${total_savings:,.0f}<span style="font-size: 12px;">/yr</span></span>
     </div>
     
     <h4 style="color: #111; margin: 0 0 4px 0; font-size: 15px; font-weight: 800; letter-spacing: 0.5px; text-transform: uppercase;">
         Environmental Impact
     </h4>
     
     <div style="display: flex; align-items: baseline; margin-bottom: 2px;">
         <span style="font-weight: 600; color: #555; font-size: 14px; width: 140px;">CO₂ Prevented:</span>
         <span style="font-size: 18px; font-weight: 700; color: #1a1a1a;">{total_co2:,.0f} <span style="font-size: 12px;">Tonnes</span></span>
     </div>
     
     <div style="display: flex; align-items: baseline; margin-bottom: 2px;">
         <span style="font-weight: 600; color: #555; font-size: 14px; width: 140px;">Homes Powered:</span>
         <span style="font-size: 18px; font-weight: 700; color: #1a1a1a;">{total_homes:,.0f}</span>
     </div>
     
     <div style="display: flex; align-items: baseline; margin-bottom: 0px;">
         <span style="font-weight: 600; color: #555; font-size: 14px; width: 140px;">EVs Charged:</span>
         <span style="font-size: 18px; font-weight: 700; color: #1a1a1a;">{total_evs:,.0f}</span>
     </div>
     
     </div>
''', unsafe_allow_html=True)