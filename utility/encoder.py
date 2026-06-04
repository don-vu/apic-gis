import geopandas as gpd

gdf = gpd.read_file("./data/output/merged_buildings.geojson")
gdf.to_parquet("./data/output/merged_buildings.parquet", index=False)

gdf = gpd.read_file("./data/output/circuit_network.geojson")
gdf.to_parquet("./data/output/circuit_network.parquet", index=False)