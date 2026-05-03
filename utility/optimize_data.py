import geopandas as gpd
import os
import pandas as pd
import requests

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
        print(f"PVGIS API Error: {e}. Using fallback yield.")
    return 1246.0  # Fallback

def optimize_buildings():
    print("Optimizing buildings...")
    path = "./data/output/merged_buildings.parquet"
    if not os.path.exists(path):
        print(f"Error: {path} not found.")
        return

    gdf = gpd.read_parquet(path)
    print(f"Loaded {len(gdf)} buildings.")

    if gdf.crs is None:
        gdf = gdf.set_crs(epsg=4326)
    else:
        gdf = gdf.to_crs(epsg=4326)

    # Simplify geometries - this is the biggest space saver
    print("Simplifying geometries...")
    gdf['geometry'] = gdf['geometry'].simplify(0.00001, preserve_topology=True)

    # Pre-calculate fields
    print("Pre-calculating fields...")
    bounds = gdf.total_bounds
    center_lat = (bounds[1] + bounds[3]) / 2
    center_lon = (bounds[0] + bounds[2]) / 2
    pvgis_yield = get_pvgis_yield(center_lat, center_lon)

    gdf['unique_id'] = range(len(gdf))
    gdf['unique_id'] = gdf['unique_id'].astype(str)
    gdf['peak_kwp'] = gdf['area'] * 0.14
    gdf['solar_potential_kwh'] = gdf['peak_kwp'] * pvgis_yield
    gdf['money_saved'] = gdf['solar_potential_kwh'] * 0.15
    gdf['co2_saved_tonnes'] = (gdf['solar_potential_kwh'] * 0.424) / 1000
    gdf['homes_powered'] = gdf['solar_potential_kwh'] / 7200
    gdf['evs_charged'] = gdf['solar_potential_kwh'] / 3040

    output_path = "./data/output/buildings_optimized.parquet"
    gdf.to_parquet(output_path)
    print(f"Saved optimized buildings to {output_path}. Size: {os.path.getsize(output_path)/1024/1024:.2f} MB")

def optimize_circuit():
    print("Optimizing circuit...")
    path = "./data/output/circuit_network.parquet"
    if not os.path.exists(path):
        print(f"Error: {path} not found.")
        return

    gdf = gpd.read_parquet(path)
    print(f"Loaded {len(gdf)} circuit elements.")

    if gdf.crs is None:
        gdf = gdf.set_crs(epsg=4326)
    else:
        gdf = gdf.to_crs(epsg=4326)

    # Simplify
    print("Simplifying geometries...")
    gdf['geometry'] = gdf['geometry'].simplify(0.00005, preserve_topology=True)

    # Filter columns to only what's needed for rendering and basic analytics
    useful_cols = [
        'geometry', 'element_type', 'name', 'vn_kv', 'p_mw', 'q_mvar', 
        'length_km', 'sn_mva', 'index', 'from_bus', 'to_bus', 'bus',
        'p_from_mw', 'q_from_mvar', 'p_to_mw', 'q_to_mvar', 'loading_percent', 
        'vm_pu', 'va_degree', 'p_hv_mw', 'q_hv_mvar', 'p_lv_mw', 'q_lv_mvar',
        'hv_bus', 'lv_bus', 'i_ka', 'i_from_ka', 'i_to_ka'
    ]
    cols_to_keep = [c for c in useful_cols if c in gdf.columns]
    gdf = gdf[cols_to_keep]

    output_path = "./data/output/circuit_optimized.parquet"
    gdf.to_parquet(output_path)
    print(f"Saved optimized circuit to {output_path}. Size: {os.path.getsize(output_path)/1024/1024:.2f} MB")

if __name__ == "__main__":
    optimize_buildings()
    optimize_circuit()
