import json
import os

folder = "./data/geojsons"

all_features = []

for file in os.listdir(folder):
    if file.endswith(".geojson"):
        with open(os.path.join(folder, file)) as f:
            data = json.load(f)
            all_features.extend(data["features"])

merged = {
    "type": "FeatureCollection",
    "features": all_features
}

with open("./data/final/merged_buildings.geojson", "w") as f:
    json.dump(merged, f)