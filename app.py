import streamlit as st
import folium
from streamlit_folium import st_folium
import geopandas as gpd
from shapely.geometry import box
import os
import pandas as pd
import requests
import pandapower as pp
import copy

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

# Initialize session state EARLY
if "center" not in st.session_state:
    st.session_state.center = (53.5461, -113.4938)
if "zoom" not in st.session_state:
    st.session_state.zoom = 18
if "selected_buildings" not in st.session_state:
    st.session_state.selected_buildings = set()
if "last_processed_click" not in st.session_state:
    st.session_state.last_processed_click = None
if "focused_building_id" not in st.session_state:
    st.session_state.focused_building_id = None
if "full_circuit_gdf" not in st.session_state:
    st.session_state.full_circuit_gdf = None
if "pinned_elements" not in st.session_state:
    st.session_state.pinned_elements = {}  # unique_id -> [lat, lon]

# Visibility Flags
if "show_buildings" not in st.session_state:
    st.session_state.show_buildings = True
if "show_loads" not in st.session_state:
    st.session_state.show_loads = True
if "show_buses" not in st.session_state:
    st.session_state.show_buses = True
if "show_lines" not in st.session_state:
    st.session_state.show_lines = True

st.markdown("""
    <style>
        .block-container { padding: 0rem !important; max-width: 100% !important; }
        header {visibility: hidden; height: 0px;}
        footer {visibility: hidden; height: 0px;}
        .stApp { overflow: hidden !important; }
        .stSpinner { position: fixed; top: 50%; left: 50%; z-index: 9999; }
        /* Force the map to fill the viewport height */
        iframe {
            height: 100vh !important;
            width: 100vw !important;
        }
        /* Remove any potential margins/padding from the streamlit elements */
        div[data-testid="stVerticalBlock"] > div {
            padding: 0 !important;
        }
        div[data-testid="stAppViewContainer"] > section > div {
            padding: 0 !important;
        }
        /* Additional resets for streamlit 1.30+ */
        [data-testid="stHeader"] {display: none;}
        .main .block-container {padding: 0;}

        /* Layer Control Panel Styling */
        [data-testid="stVerticalBlock"] > div:has(.layer-control-anchor) {
            position: fixed;
            bottom: 40px;
            left: 20px;
            width: 220px;
            background-color: rgba(255, 255, 255, 0.98);
            border: 1px solid #e0e0e0;
            z-index: 100000;
            font-family: sans-serif;
            border-radius: 12px;
            padding: 18px 20px;
            box-shadow: 0px 8px 24px rgba(0,0,0,0.15);
        }
        .layer-control-anchor {
            display: none;
        }
        /* Make sure the streamlit checkboxes inside the panel are legible */
        [data-testid="stVerticalBlock"] > div:has(.layer-control-anchor) .stCheckbox {
            margin-bottom: -15px;
        }
        [data-testid="stVerticalBlock"] > div:has(.layer-control-anchor) [data-testid="stWidgetLabel"] p {
            font-size: 14px !important;
            font-weight: 600 !important;
            color: #333 !important;
        }
    </style>
""", unsafe_allow_html=True)

@st.cache_resource
def get_base_net():
    """Loads the base pandapower network from JSON."""
    json_path = "./data/json/circuit_network.json"
    if os.path.exists(json_path):
        return pp.from_json(json_path)
    return None

def run_simulation(selected_bids, buildings_gdf):
    """
    Creates an in-memory copy of the network, adds solar sgens for 
    selected buildings, runs power flow, and updates the global circuit GDF.
    """
    base_net = get_base_net()
    if base_net is None or st.session_state.full_circuit_gdf is None:
        return
    
    # Work on a deep copy to avoid modifying the cached base_net
    net = copy.deepcopy(base_net)
    
    # Add sgens for each selected building and track solar per bus
    selected_data = buildings_gdf[buildings_gdf['unique_id'].isin(selected_bids)]
    bus_solar = {}
    for _, row in selected_data.iterrows():
        bus_id = row.get('bus_id')
        if bus_id is not None and not pd.isna(bus_id):
            # Convert peak_kwp to MW for pandapower
            p_mw = row.get('peak_kwp', 0) / 1000.0
            if p_mw > 0:
                pp.create_sgen(net, bus=int(bus_id), p_mw=p_mw, q_mvar=0, 
                               name=f"Solar_{row['unique_id']}")
                bid_int = int(bus_id)
                bus_solar[bid_int] = bus_solar.get(bid_int, 0) + p_mw
    
    # Run Power Flow
    try:
        pp.runpp(net, algorithm='nr', init='flat', numba=False)
        
        gdf = st.session_state.full_circuit_gdf.copy()
        
        # Initialize or reset solar_mw column
        gdf['solar_mw'] = 0.0
        
        # Update results for all elements using vectorized operations where possible
        for etype in ['line', 'trafo', 'bus', 'load', 'sgen']:
            res_table = f'res_{etype}'
            if hasattr(net, res_table):
                res = getattr(net, res_table)
                if not res.empty:
                    mask = (gdf['element_type'] == etype)
                    indices = gdf.loc[mask, 'index']
                    res_filtered = res.reindex(indices)
                    for col in res_filtered.columns:
                        if col in gdf.columns:
                            gdf.loc[mask, col] = res_filtered[col].values
        
        # Update solar_mw for buses that have injection
        if bus_solar:
            for b_id, s_mw in bus_solar.items():
                mask = (gdf['element_type'] == 'bus') & (gdf['index'] == b_id)
                gdf.loc[mask, 'solar_mw'] = s_mw

        # Tooltips are generated lazily in get_visible_data
        st.session_state.full_circuit_gdf = gdf
        return True
    except Exception as e:
        st.error(f"Simulation failed to converge: {e}")
        return False

@st.cache_data
def get_pvgis_yield(lat, lon):
    """Fetches annual energy yield (kWh per 1 kWp) from PVGIS v6 API."""
    try:
        url = "https://photovoltaic-geographic-information-system.ec.europa.eu/api/v6/performance/broadband"
        params = {
            "latitude": lat,
            "longitude": lon,
            "peak-power": 1,
            "system_efficiency": 0.86,
            "photovoltaic_module": "cSi:Integrated 2025",
            "surface_position_optimisation_mode": "Orientation & Tilt",
            "irradiance_source": "ERA5",
            "analysis": "Simple",
            "groupby": "Yearly",
            "frequency": "Yearly"
        }
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return data['Photovoltaic Performance']['Energy 🔌']['value']
    except Exception as e:
        st.warning(f"PVGIS API Error: {e}. Using fallback yield.")
    return 1246.0  # Fallback to previous heuristic value

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
            'loading_percent': 'Loading (%)',
            'p_from_mw': 'P (kW)',
            'q_from_mvar': 'Q (kVAR)',
            'i_ka': 'Current (A)'
        }
    elif etype == "trafo":
        field_labels = {
            'name': 'Name',
            'hv_bus': 'HV Bus',
            'lv_bus': 'LV Bus',
            'loading_percent': 'Loading (%)',
            'p_hv_mw': 'P HV (kW)',
            'q_hv_mvar': 'Q HV (kVAR)',
            'sn_mva': 'Rating (MVA)'
        }
    elif etype == "load":
        field_labels = {
            'name': 'Name',
            'bus': 'Bus',
            'p_mw': 'P (kW)',
            'q_mvar': 'Q (kVAR)'
        }
    elif etype == "bus":
        field_labels = {
            'name': 'Name',
            'vn_kv': 'Nominal Voltage (kV)',
            'vm_pu': 'Voltage (pu)',
            'p_mw': 'Net Power (kW)',
            'solar_mw': 'Solar Injection (kW)'
        }
    elif etype == "sgen":
        field_labels = {
            'name': 'Name',
            'bus': 'Bus',
            'p_mw': 'P (kW)',
            'q_mvar': 'Q (kVAR)'
        }
    else:
        field_labels = {
            'name': 'Name',
            'vn_kv': 'Voltage (kV)',
            'p_mw': 'P (kW)',
            'q_mvar': 'Q (kVAR)',
            'loading_percent': 'Loading (%)',
            'vm_pu': 'Voltage (pu)',
            'index': 'ID'
        }
    
    for col, label in field_labels.items():
        if col in row.index and pd.notnull(row[col]):
            val = row[col]
            if isinstance(val, str) and (val.strip() == "" or val.lower() == "nan"):
                continue
            if isinstance(val, (int, float)) and pd.isna(val):
                continue
            
            # Convert MW/MVAR to kW/kVAR
            if any(suffix in col for suffix in ['_mw', '_mvar']):
                val = abs(val) * 1000
            
            # Convert kA to A
            if '_ka' in col or col == 'i_ka':
                val = abs(val) * 1000

            if col == 'loading_percent':
                if val > 100:
                    html += f"<b>{label}:</b> <span style='color: #FF5252;'>{val:,.1f}% (OVERLOAD)</span><br>"
                else:
                    html += f"<b>{label}:</b> {val:,.1f}%<br>"
            elif col == 'vm_pu' and val < 0.95:
                html += f"<b>{label}:</b> <span style='color: #FF5252;'>{val:,.3f} pu (LOW)</span><br>"
            elif col in ['from_bus', 'to_bus', 'bus', 'index', 'hv_bus', 'lv_bus']:
                try:
                    html += f"<b>{label}:</b> {int(float(val))}<br>"
                except:
                    html += f"<b>{label}:</b> {val}<br>"
            elif isinstance(val, (int, float)):
                html += f"<b>{label}:</b> {val:,.2f}<br>"
            else:
                html += f"<b>{label}:</b> {val}<br>"
    
    html += "</div>"
    return html

@st.cache_data
def load_full_data():
    # Buildings
    optimized_buildings_path = "./data/output/buildings_optimized.parquet"
    if os.path.exists(optimized_buildings_path):
        gdf = gpd.read_parquet(optimized_buildings_path)
    else:
        # Fallback to original if optimized not found
        gdf = gpd.read_parquet("./data/output/merged_buildings.parquet")
        if gdf.crs is None:
            gdf = gdf.set_crs(epsg=4326)
        else:
            gdf = gdf.to_crs(epsg=4326)

        # Ensure unique ID for every building
        gdf['unique_id'] = range(len(gdf))
        gdf['unique_id'] = gdf['unique_id'].astype(str)

        if not gdf.empty:
            bounds = gdf.total_bounds
            center = ((bounds[1] + bounds[3]) / 2, (bounds[0] + bounds[2]) / 2)
        else:
            center = (53.5461, -113.4938)

        pvgis_yield = get_pvgis_yield(center[0], center[1])

        gdf['peak_kwp'] = gdf['area'] * 0.14
        gdf['solar_potential_kwh'] = gdf['peak_kwp'] * pvgis_yield
        gdf['money_saved'] = gdf['solar_potential_kwh'] * 0.15
        gdf['co2_saved_tonnes'] = (gdf['solar_potential_kwh'] * 0.424) / 1000
        gdf['homes_powered'] = gdf['solar_potential_kwh'] / 7200
        gdf['evs_charged'] = gdf['solar_potential_kwh'] / 3040
        
        gdf['geometry'] = gdf['geometry'].simplify(0.00001, preserve_topology=True)
    
    if not gdf.empty:
        bounds = gdf.total_bounds
        center = ((bounds[1] + bounds[3]) / 2, (bounds[0] + bounds[2]) / 2)
    else:
        center = (53.5461, -113.4938)

    # Grid
    optimized_circuit_path = "./data/output/circuit_optimized.parquet"
    if os.path.exists(optimized_circuit_path):
        circuit_path = optimized_circuit_path
    else:
        circuit_path = "./data/output/circuit_network.parquet"

    circuit_gdf = None
    if os.path.exists(circuit_path):
        circuit_gdf = gpd.read_parquet(circuit_path)
        if circuit_gdf.crs is None:
            circuit_gdf = circuit_gdf.set_crs(epsg=4326)
        else:
            circuit_gdf = circuit_gdf.to_crs(epsg=4326)
        
        if 'element_type' not in circuit_gdf.columns:
            circuit_gdf['element_type'] = 'line'
        
        # Initialize result columns for tooltips
        result_cols = ['p_mw', 'q_mvar', 'vm_pu', 'loading_percent', 'solar_mw']
        for col in result_cols:
            if col not in circuit_gdf.columns:
                circuit_gdf[col] = 0.0
        
        # Default voltage for buses
        bus_mask = circuit_gdf['element_type'] == 'bus'
        circuit_gdf.loc[bus_mask, 'vm_pu'] = 1.0

        # Ensure unique_id for circuit elements
        if 'unique_id' not in circuit_gdf.columns:
            circuit_gdf['unique_id'] = circuit_gdf['element_type'] + "_" + circuit_gdf['index'].astype(str)

    if 'bus_id' not in gdf.columns and circuit_gdf is not None:
        buses = circuit_gdf[(circuit_gdf['element_type'] == 'bus') & (circuit_gdf['vn_kv'] <= 25.0)]
        if not buses.empty:
            from scipy.spatial import KDTree
            import numpy as np
            
            centroids = gdf.geometry.centroid
            bld_coords = np.array([[c.x, c.y] for c in centroids])
            bus_coords = np.array([[g.x, g.y] for g in buses.geometry])
            bus_ids = buses['index'].values.astype(int)
            
            tree = KDTree(bus_coords)
            _, indices = tree.query(bld_coords)
            gdf['bus_id'] = [bus_ids[idx] for idx in indices]

    return gdf, circuit_gdf, center

with st.spinner("Loading Edmonton Solar & Grid Data..."):
    full_gdf, initial_circuit_gdf, default_center = load_full_data()
    if st.session_state.full_circuit_gdf is None:
        st.session_state.full_circuit_gdf = initial_circuit_gdf

# Update Tooltips dynamically based on selection (NOT CACHED)
def make_building_tooltip(row):
    return f"""
    <div style='font-family: sans-serif; padding: 10px; min-width: 150px;'>
        <h4 style='margin-top: 0; color: #FF9F00;'>Building #{row.unique_id}</h4>
        <b>Roof Area:</b> {row.area:,.0f} m²<br>
        <b>Peak Solar:</b> {row.peak_kwp:,.1f} kWp<br>
        <b>Energy Potential:</b> {row.solar_potential_kwh:,.0f} kWh/yr<br>
        <b>Est. Savings:</b> <span style='color: green;'>${row.money_saved:,.0f}/yr</span>
        <hr style='margin: 8px 0; border: 0; border-top: 1px solid #eee;'>
        </div>
        """
# Viewport Filtering Logic
def get_visible_data(gdf, circuit_gdf, bounds):
    if bounds is None:
        lat, lon = st.session_state.center
        buffer = 0.005
        view_box = box(lon - buffer, lat - buffer, lon + buffer, lat + buffer)
    else:
        sw = bounds["_southWest"]
        ne = bounds["_northEast"]
        view_box = box(sw["lng"], sw["lat"], ne["lng"], ne["lat"])
    
    visible_gdf = gdf.iloc[:0].copy()
    if st.session_state.zoom >= 16:
        spatial_index = gdf.sindex
        possible_indices = list(spatial_index.intersection(view_box.bounds))
        visible_gdf = gdf.iloc[possible_indices].copy()
        visible_gdf = visible_gdf[visible_gdf.geometry.intersects(view_box)]
        # Generate tooltips lazily only for visible buildings
        if not visible_gdf.empty:
            visible_gdf["tooltip_html"] = visible_gdf.apply(make_building_tooltip, axis=1)
    
    visible_circuit = None
    if circuit_gdf is not None and st.session_state.zoom >= 16:
        c_spatial_index = circuit_gdf.sindex
        c_possible_indices = list(c_spatial_index.intersection(view_box.bounds))
        visible_circuit = circuit_gdf.iloc[c_possible_indices].copy()
        visible_circuit = visible_circuit[visible_circuit.geometry.intersects(view_box)]
        # Generate tooltips lazily only for visible grid elements
        if not visible_circuit.empty:
            visible_circuit['tooltip_html'] = visible_circuit.apply(make_circuit_tooltip, axis=1)
        
    return visible_gdf, visible_circuit

last_map_data = st.session_state.get("last_map_data", None)
current_bounds = last_map_data.get("bounds") if last_map_data else None

# Filter data for the CURRENT render
visible_buildings, visible_grid = get_visible_data(full_gdf, st.session_state.full_circuit_gdf, current_bounds)

m = folium.Map(
    location=st.session_state.center,
    zoom_start=st.session_state.zoom,
    tiles="http://mt0.google.com/vt/lyrs=s&hl=en&x={x}&y={y}&z={z}",
    attr="Google Satellite",
    max_zoom=20,
    zoom_control=True,
    prefer_canvas=True
)

# Add grid layers
if visible_grid is not None and len(visible_grid) > 0:
    for etype in ELEMENT_TYPES:
        # Visibility Filtering
        if etype == "bus" and not st.session_state.show_buses: continue
        if etype == "load" and not st.session_state.show_loads: continue
        if etype in ["line", "trafo"] and not st.session_state.show_lines: continue
        
        subset = visible_grid[visible_grid['element_type'] == etype]
        if not subset.empty:
            config = ELEMENT_CONFIG.get(etype, {"color": "#999999", "label": etype.capitalize()})
            fg = folium.FeatureGroup(name=config["label"]).add_to(m)
            
            def style_fn(feature):
                props = feature['properties']
                loading = props.get('loading_percent', 0)
                if loading is None or (isinstance(loading, float) and pd.isna(loading)):
                    loading = 0
                
                color = config.get("color", "#2979FF")
                weight = config.get("weight", 2)
                
                if etype == 'line':
                    if loading > 100: color = "#FF1744"
                    elif loading > 80: color = "#FFFF00"
                    elif loading > 30: color = "#00FF00"
                    else: color = "#00E5FF"
                    weight = 4 + (loading / 20.0)
                elif etype == 'trafo' and loading > 100:
                    color = "#FF1744"
                
                return {
                    "color": color,
                    "weight": weight,
                    "opacity": 0.9,
                }

            folium.GeoJson(
                subset,
                style_function=style_fn,
                marker=folium.CircleMarker(
                    radius=config.get("radius", 3) + 1,
                    color=config["color"],
                    fill=True,
                    fill_opacity=1.0
                ) if "radius" in config else None,
                tooltip=folium.GeoJsonTooltip(
                    fields=["tooltip_html"],
                    aliases=[""],
                    labels=False,
                    sticky=True,
                    max_width=300
                ),
                popup=folium.GeoJsonPopup(
                    fields=["tooltip_html"],
                    aliases=[""],
                    labels=False,
                    max_width=300
                )
            ).add_to(fg)
            
            # Power Labels for lines
            if etype == "line" and st.session_state.zoom >= 17:
                for _, row in subset.iterrows():
                    if row.geometry.geom_type == 'LineString':
                        midpoint = row.geometry.interpolate(0.5, normalized=True)
                        p_kw = abs(row.get('p_from_mw', 0)) * 1000
                        ld = row.get('loading_percent', 0)
                        if p_kw > 0.1:
                            folium.Marker(
                                location=[midpoint.y, midpoint.x],
                                icon=folium.DivIcon(
                                    html=f"""<div style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
                                             font-size: 11px; font-weight: bold; color: white; 
                                             background: rgba(0,0,0,0.8); padding: 2px 6px; border-radius: 4px;
                                             white-space: nowrap; border: 1px solid rgba(255,255,255,0.4);
                                             box-shadow: 0px 2px 4px rgba(0,0,0,0.5); transform: translate(-50%, -50%);">
                                             {p_kw:,.0f} kW | {ld:.1f}%
                                             </div>"""
                                )
                            ).add_to(fg)

# Add buildings
if len(visible_buildings) > 0 and st.session_state.show_buildings:
    folium.GeoJson(
        visible_buildings[['geometry', 'tooltip_html', 'unique_id']],
        name="Solar Potential",
        tooltip=folium.GeoJsonTooltip(
            fields=["tooltip_html"],
            aliases=[""],
            labels=False,
            sticky=True,
            max_width=300
        ),
        popup=folium.GeoJsonPopup(
            fields=["tooltip_html"],
            aliases=[""],
            labels=False,
            max_width=300
        ),
        style_function=lambda x: {
            "fillColor": "#FFD54F",
            "color": "#00FF00" if x['properties'].get('unique_id') in st.session_state.selected_buildings else "#FFA000",
            "weight": 3 if x['properties'].get('unique_id') in st.session_state.selected_buildings else 1,
            "fillOpacity": 0.2,
        },
    ).add_to(m)

# Add pinned data boxes with internal close buttons
if st.session_state.pinned_elements:
    pinned_fg = folium.FeatureGroup(name="Pinned Info").add_to(m)
    for pid, pos in st.session_state.pinned_elements.items():
        feat = full_gdf[full_gdf['unique_id'] == pid]
        is_grid = False
        if feat.empty:
            feat = st.session_state.full_circuit_gdf[st.session_state.full_circuit_gdf['unique_id'] == pid]
            is_grid = True
        
        if not feat.empty:
            row = feat.iloc[0]
            inner_html = make_building_tooltip(row) if not is_grid else make_circuit_tooltip(row)
            
            # Render a marker whose icon IS the entire data box with an 'X' button
            folium.GeoJson(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [pos[1], pos[0]]},
                    "properties": {"unique_id": f"unpin_{pid}"}
                },
                marker=folium.Marker(
                    location=pos,
                    icon=folium.DivIcon(
                        icon_size=(250, 200),
                        icon_anchor=(250, 180), # Position box above and to the left of the point
                        html=f"""
                            <div style="
                                background: white;
                                border: 1px solid #ccc;
                                border-radius: 12px;
                                box-shadow: 0 4px 20px rgba(0,0,0,0.3);
                                width: 250px;
                                position: relative;
                                font-family: sans-serif;
                                cursor: pointer;
                                overflow: hidden;
                            ">
                                <div style="
                                    position: absolute;
                                    top: 8px;
                                    right: 8px;
                                    width: 20px;
                                    height: 20px;
                                    background: #444;
                                    color: white;
                                    border-radius: 50%;
                                    display: flex;
                                    align-items: center;
                                    justify-content: center;
                                    font-size: 14px;
                                    font-weight: bold;
                                    border: 1.5px solid white;
                                    box-shadow: 0 1px 3px rgba(0,0,0,0.2);
                                    z-index: 1000;
                                ">×</div>
                                <div style="padding: 2px;">
                                    {inner_html}
                                </div>
                            </div>
                        """
                    )
                )
            ).add_to(pinned_fg)

folium.LayerControl(position='bottomright', collapsed=False).add_to(m)

# Render map
map_output = st_folium(
    m, 
    width="100%", 
    height=1200, 
    key="solar_map", 
    returned_objects=["bounds", "center", "zoom", "last_active_drawing", "last_object_clicked"]
)

# Update session state and rerun if the view changed
if map_output:
    changed = False
    
    # Handle element pinning/toggle/unpin
    new_drawing = map_output.get("last_active_drawing")
    new_click_point = map_output.get("last_object_clicked")
    
    click_id = (str(new_drawing), str(new_click_point)) if new_drawing and new_click_point else None
    
    if click_id and click_id != st.session_state.last_processed_click:
        props = new_drawing.get("properties")
        if props and "unique_id" in props:
            pid = props["unique_id"]
            
            # Check for Unpin Click (triggered by clicking the box)
            if pid.startswith("unpin_"):
                real_id = pid.replace("unpin_", "")
                if real_id in st.session_state.pinned_elements:
                    del st.session_state.pinned_elements[real_id]
                    # Also unselect building if needed
                    if real_id in st.session_state.selected_buildings:
                        st.session_state.selected_buildings.remove(real_id)
                        with st.spinner("Recalculating Power Flow..."):
                            run_simulation(st.session_state.selected_buildings, full_gdf)
                    changed = True
            else:
                # Toggle Pinning
                if pid in st.session_state.pinned_elements:
                    # If already pinned, unpin it
                    del st.session_state.pinned_elements[pid]
                    if pid in st.session_state.selected_buildings:
                        st.session_state.selected_buildings.remove(pid)
                        with st.spinner("Recalculating Power Flow..."):
                            run_simulation(st.session_state.selected_buildings, full_gdf)
                else:
                    st.session_state.pinned_elements[pid] = [new_click_point['lat'], new_click_point['lng']]
                    
                    # If it's a building, also toggle solar selection
                    is_building = any(full_gdf['unique_id'] == pid)
                    if is_building:
                        if pid in st.session_state.selected_buildings:
                            st.session_state.selected_buildings.remove(pid)
                        else:
                            st.session_state.selected_buildings.add(pid)
                        
                        # TRIGGER SIMULATION
                        with st.spinner("Recalculating Power Flow..."):
                            run_simulation(st.session_state.selected_buildings, full_gdf)
                changed = True

            st.session_state.last_processed_click = click_id

    if map_output.get("center"):
        new_center = (map_output["center"]["lat"], map_output["center"]["lng"])
        old_center = st.session_state.center
        if (round(new_center[0], 6) != round(old_center[0], 6) or 
            round(new_center[1], 6) != round(old_center[1], 6)):
            st.session_state.center = new_center
            changed = True
            
    if map_output.get("zoom") is not None:
        new_zoom = map_output["zoom"]
        if new_zoom != st.session_state.zoom:
            st.session_state.zoom = new_zoom
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
     {f'<div style="color: #FF5252; font-size: 12px; font-weight: 600; margin-bottom: 8px;">⚠️ Zoom in to see buildings & grid</div>' if st.session_state.zoom < 16 else ''}
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

# Layer Visibility Toggles (Bottom Left Floating Panel)
with st.container():
    st.markdown('<div class="layer-control-anchor"></div>', unsafe_allow_html=True)
    st.checkbox("Buildings", key="show_buildings")
    st.checkbox("Loads", key="show_loads")
    st.checkbox("Buses", key="show_buses")
    st.checkbox("Lines", key="show_lines")
    st.markdown('<div style="height: 10px;"></div>', unsafe_allow_html=True)
