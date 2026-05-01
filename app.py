import streamlit as st
import folium
from streamlit_folium import st_folium
import geopandas as gpd
from shapely.geometry import box
import os
import pandas as pd

# Setup
st.set_page_config(page_title="Solar Intelligence", layout="wide")

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
    """Load the full datasets into memory once."""
    # Buildings
    gdf = gpd.read_parquet("data/data.parquet")
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
    
    # Simplify once for performance
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
    center_point = gdf.to_crs(epsg=26912).geometry.centroid.to_crs(epsg=4326).iloc[0]
    center = (center_point.y, center_point.x)
    
    # Grid
    circuit_path = "data/circuit_network.geojson"
    circuit_gdf = None
    if os.path.exists(circuit_path):
        circuit_gdf = gpd.read_file(circuit_path)
        if circuit_gdf.crs is None:
            circuit_gdf = circuit_gdf.set_crs(epsg=4326)
        else:
            circuit_gdf = circuit_gdf.to_crs(epsg=4326)
        circuit_gdf['geometry'] = circuit_gdf['geometry'].simplify(0.00005, preserve_topology=True)
        if 'element_type' not in circuit_gdf.columns:
            circuit_gdf['element_type'] = 'line'
        
        # Keep useful properties for tooltips
        useful_cols = ['geometry', 'element_type', 'name', 'vn_kv', 'p_mw', 'q_mvar', 'length_km', 'sn_mva', 'index']
        cols_to_keep = [c for c in useful_cols if c in circuit_gdf.columns]
        circuit_gdf = circuit_gdf[cols_to_keep]

    return gdf, circuit_gdf, center

with st.spinner("Loading Edmonton Solar & Grid Data..."):
    full_gdf, full_circuit_gdf, default_center = load_full_data()

# Initialize session state for viewport
if "center" not in st.session_state:
    st.session_state.center = default_center
if "zoom" not in st.session_state:
    st.session_state.zoom = 18

# Viewport Filtering Logic
# We use a placeholder or the last known bounds to filter data BEFORE sending to folium
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

# Get bounds from st_folium (it returns the bounds of the PREVIOUS render)
last_map_data = st.session_state.get("last_map_data", None)
current_bounds = last_map_data.get("bounds") if last_map_data else None

# Filter data for the CURRENT render
visible_buildings, visible_grid = get_visible_data(full_gdf, full_circuit_gdf, current_bounds)

# Create the map
m = folium.Map(
    location=st.session_state.center,
    zoom_start=st.session_state.zoom,
    tiles="http://mt0.google.com/vt/lyrs=s&hl=en&x={x}&y={y}&z={z}",
    attr="Google Satellite",
    max_zoom=20,
    zoom_control=True
)

# Grid Configuration
ELEMENT_CONFIG = {
    "bus": {"color": "#2979FF", "radius": 3, "label": "Buses"},
    "load": {"color": "#FF5252", "radius": 4, "label": "Loads"},
    "sgen": {"color": "#FFEA00", "radius": 4, "label": "Static Gen"},
    "gen": {"color": "#FFAB40", "radius": 5, "label": "Generators"},
    "switch": {"color": "#B0BEC5", "radius": 3, "label": "Switches"},
    "shunt": {"color": "#7C4DFF", "radius": 4, "label": "Shunts"},
    "ext_grid": {"color": "#00E676", "radius": 6, "label": "External Grid"},
    "line": {"color": "#00E5FF", "weight": 3, "label": "Lines"},
    "trafo": {"color": "#F50057", "weight": 4, "label": "Transformers"},
}
ELEMENT_TYPES = ["bus", "load", "sgen", "gen", "switch", "shunt", "ext_grid", "line", "trafo"]

# Add grid layers
if visible_grid is not None and len(visible_grid) > 0:
    for etype in ELEMENT_TYPES:
        subset = visible_grid[visible_grid['element_type'] == etype]
        if not subset.empty:
            config = ELEMENT_CONFIG.get(etype, {"color": "#999999", "label": etype.capitalize()})
            fg = folium.FeatureGroup(name=config["label"]).add_to(m)
            
            tooltip_cols = [c for c in ['name', 'vn_kv', 'p_mw', 'q_mvar', 'length_km', 'sn_mva', 'index'] if c in subset.columns]
            
            folium.GeoJson(
                subset,
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
                tooltip=folium.GeoJsonTooltip(fields=tooltip_cols) if tooltip_cols else None
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

# Add layer control
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

# --- Analytics for visible buildings ---
total_buildings = len(visible_buildings)
total_kwh = visible_buildings['solar_potential_kwh'].sum()
total_savings = visible_buildings['money_saved'].sum()
total_co2 = visible_buildings['co2_saved_tonnes'].sum()
total_homes = visible_buildings['homes_powered'].sum()
total_evs = visible_buildings['evs_charged'].sum()

# --- THE FINAL POLISHED LEGEND ---
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
