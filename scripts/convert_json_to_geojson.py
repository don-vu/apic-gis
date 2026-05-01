import pandapower as pp
import json
import os
import sys
import pandas as pd
from shapely.geometry import Point, LineString, shape

def convert_network_to_geojson(input_json, output_geojson):
    if not os.path.exists(input_json):
        print(f"Error: {input_json} does not exist.")
        return

    print(f"Loading network from {input_json}...")
    try:
        net = pp.from_json(input_json)
    except Exception as e:
        print(f"Error loading network: {e}")
        return

    features = []

    # Map to store bus coordinates for line reconstruction
    bus_coords = {}

    # Convert buses
    print("Processing buses...")
    if 'bus' in net is not None:
        has_geo = 'geo' in net.bus.columns
        for idx, row in net.bus.iterrows():
            geometry = None
            if has_geo and row['geo'] is not None:
                try:
                    geo_data = row['geo']
                    if isinstance(geo_data, str):
                        geometry = json.loads(geo_data)
                    else:
                        geometry = geo_data
                    
                    # Store coordinates for lines
                    if 'coordinates' in geometry:
                        bus_coords[idx] = geometry['coordinates']
                except Exception as e:
                    print(f"Warning: Could not parse geo for bus {idx}: {e}")

            properties = row.drop('geo').to_dict() if has_geo else row.to_dict()
            properties['element_type'] = 'bus'
            properties['index'] = int(idx)
            
            # Ensure all property values are JSON serializable (no NaNs)
            for k, v in properties.items():
                if isinstance(v, float) and (pd.isna(v) or v != v): # check for NaN
                    properties[k] = None

            if geometry:
                features.append({
                    "type": "Feature",
                    "geometry": geometry,
                    "properties": properties
                })

    # Convert lines
    print("Processing lines...")
    if 'line' in net is not None:
        has_geo = 'geo' in net.line.columns
        for idx, row in net.line.iterrows():
            geometry = None
            
            # Try to get geometry from 'geo' column first
            if has_geo and row['geo'] is not None:
                try:
                    geo_data = row['geo']
                    if isinstance(geo_data, str):
                        geometry = json.loads(geo_data)
                    else:
                        geometry = geo_data
                except:
                    pass
            
            # If no geometry in 'geo' column, reconstruct from buses
            if geometry is None:
                from_bus = row['from_bus']
                to_bus = row['to_bus']
                if from_bus in bus_coords and to_bus in bus_coords:
                    geometry = {
                        "type": "LineString",
                        "coordinates": [bus_coords[from_bus], bus_coords[to_bus]]
                    }

            properties = row.drop('geo').to_dict() if has_geo else row.to_dict()
            properties['element_type'] = 'line'
            properties['index'] = int(idx)
            
            for k, v in properties.items():
                if isinstance(v, float) and (pd.isna(v) or v != v):
                    properties[k] = None

            if geometry:
                features.append({
                    "type": "Feature",
                    "geometry": geometry,
                    "properties": properties
                })

    # Add Transformers (Trafo)
    print("Processing transformers...")
    if 'trafo' in net:
        for idx, row in net.trafo.iterrows():
            hv_bus = row['hv_bus']
            lv_bus = row['lv_bus']
            
            geometry = None
            if hv_bus in bus_coords and lv_bus in bus_coords:
                geometry = {
                    "type": "LineString",
                    "coordinates": [bus_coords[hv_bus], bus_coords[lv_bus]]
                }
            
            properties = row.to_dict()
            properties['element_type'] = 'trafo'
            properties['index'] = int(idx)
            
            for k, v in properties.items():
                if isinstance(v, float) and (pd.isna(v) or v != v):
                    properties[k] = None

            if geometry:
                features.append({
                    "type": "Feature",
                    "geometry": geometry,
                    "properties": properties
                })

    # Add Point elements (load, gen, sgen, ext_grid, shunt)
    for element_type in ['load', 'gen', 'sgen', 'ext_grid', 'shunt']:
        print(f"Processing {element_type}...")
        if element_type in net:
            for idx, row in net[element_type].iterrows():
                bus_idx = row['bus']
                
                geometry = None
                if bus_idx in bus_coords:
                    geometry = {
                        "type": "Point",
                        "coordinates": bus_coords[bus_idx]
                    }
                
                properties = row.to_dict()
                properties['element_type'] = element_type
                properties['index'] = int(idx)
                
                for k, v in properties.items():
                    if isinstance(v, float) and (pd.isna(v) or v != v):
                        properties[k] = None

                if geometry:
                    features.append({
                        "type": "Feature",
                        "geometry": geometry,
                        "properties": properties
                    })

    # Add Switch elements (Point on bus)
    print("Processing switch...")
    if 'switch' in net:
        for idx, row in net.switch.iterrows():
            bus_idx = row['bus']
            
            geometry = None
            if bus_idx in bus_coords:
                geometry = {
                    "type": "Point",
                    "coordinates": bus_coords[bus_idx]
                }
            
            properties = row.to_dict()
            properties['element_type'] = 'switch'
            properties['index'] = int(idx)
            
            for k, v in properties.items():
                if isinstance(v, float) and (pd.isna(v) or v != v):
                    properties[k] = None

            if geometry:
                features.append({
                    "type": "Feature",
                    "geometry": geometry,
                    "properties": properties
                })

    geojson_obj = {
        "type": "FeatureCollection",
        "features": features
    }

    print(f"Saving to {output_geojson}...")
    try:
        with open(output_geojson, 'w') as f:
            json.dump(geojson_obj, f, indent=2)
        print(f"Successfully converted {len(features)} features.")
    except Exception as e:
        print(f"Error saving GeoJSON: {e}")

if __name__ == "__main__":
    input_file = "data/circuit_network.json"
    output_file = "data/circuit_network.geojson"
    
    if len(sys.argv) > 1:
        input_file = sys.argv[1]
    if len(sys.argv) > 2:
        output_file = sys.argv[2]
        
    convert_network_to_geojson(input_file, output_file)
