import json
import os

folder = "./data/geojsons"

all_features = []
merged_crs = None

for file in os.listdir(folder):
    if file.endswith(".geojson") and file != "merged_buildings.geojson":
        with open(os.path.join(folder, file)) as f:
            data = json.load(f)
            all_features.extend(data["features"])
            if "crs" in data and merged_crs is None:
                merged_crs = data["crs"]

merged = {
    "type": "FeatureCollection",
    "features": all_features
}

if merged_crs:
    merged["crs"] = merged_crs

with open("./data/geojsons/merged_buildings.geojson", "w") as f:
    json.dump(merged, f)