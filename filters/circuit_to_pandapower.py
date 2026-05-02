import time
import pandas as pd
import numpy as np
import pandapower as pp
import pandapower.topology as top
from shapely.geometry import LineString, MultiLineString
from shapely.wkt import loads
import json
import geopandas as gpd
from scipy.spatial import KDTree

COORD_PRECISION = 5  # ~1 m at Edmonton's latitude
_stage_start: float = 0.0
_run_start: float = 0.0

def _elapsed(since: float) -> str:
    s = time.time() - since
    if s < 60:
        return f"{s:.1f}s"
    return f"{int(s // 60)}m {int(s % 60)}s"

def stage(name: str, detail: str = "") -> None:
    global _stage_start
    _stage_start = time.time()
    suffix = f"  ({detail})" if detail else ""
    print(f"\n{'='*60}", flush=True)
    print(f"  STAGE: {name}{suffix}", flush=True)
    print(f"  Total elapsed: {_elapsed(_run_start)}", flush=True)
    print(f"{'='*60}", flush=True)

def progress(current: int, total: int, label: str = "") -> None:
    pct = current / total * 100
    bar_len = 30
    filled = int(bar_len * current / total)
    bar = "█" * filled + "░" * (bar_len - filled)
    suffix = f"  {label}" if label else ""
    print(f"\r  [{bar}] {pct:5.1f}%  {current:,}/{total:,}{suffix}",
          end="", flush=True)
    if current >= total:
        print(f"  (stage done in {_elapsed(_stage_start)})", flush=True)

def info(msg: str) -> None:
    print(f"  → {msg}", flush=True)



def round_coord(coord: tuple) -> tuple:
    return (round(coord[0], COORD_PRECISION), round(coord[1], COORD_PRECISION))


def parse_multilinestring(geom_str: str) -> list:
    try:
        geom = loads(geom_str)
        if isinstance(geom, MultiLineString):
            return list(geom.geoms)
        elif isinstance(geom, LineString):
            return [geom]
    except Exception:
        pass
    return []


def extract_endpoints(line: LineString):
    coords = list(line.coords)
    if len(coords) >= 2:
        return coords[0], coords[-1]
    return None


def haversine_km(lon1, lat1, lon2, lat2) -> float:
    """Great-circle distance in km between two WGS-84 points."""
    R = 6371.0
    phi1, phi2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlam = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(phi1) * np.cos(phi2) * np.sin(dlam / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(a))


def map_voltage_kv(voltage_str: str) -> float:
    """
    Map GIS voltage label to nominal phase-to-phase kV used in the model.
    """
    v = str(voltage_str)
    if "25" in v:
        return 25.0
    if "15" in v or "14.4" in v:
        return 14.4
    if "5" in v:
        return 4.16
    return 14.4

def create_network_from_csv(csv_path: str, output_path: str, buildings_path: str | None = None, max_lines: int | None = None):
    global _run_start
    _run_start = time.time()

    print(f"\n{'#'*60}")
    print(f"  Edmonton Distribution Network Builder")
    print(f"  Input : {csv_path}")
    if buildings_path:
        print(f"  Buildings: {buildings_path}")
    print(f"  Output: {output_path}")
    print(f"{'#'*60}\n")

    stage("Load CSV")
    df = pd.read_csv(csv_path)
    if max_lines:
        df = df.head(max_lines)
    info(f"Rows loaded: {len(df):,}")
    info(f"Service types: {df['Service'].value_counts().to_dict()}")
    info(f"Voltage classes: {df['Voltage'].value_counts().to_dict()}")
    info(f"Phase counts: {df['Phase'].value_counts().to_dict()}")

    stage("Initialise pandapower network")
    net = pp.create_empty_network(f_hz=60.0, sn_mva=100.0)
    info("Frequency: 60 Hz  (North American standard)")
    info("Base MVA : 100 MVA")

    # OH 336 ACSR
    pp.create_std_type(net, {
        "r_ohm_per_km": 0.188,
        "x_ohm_per_km": 0.400,
        "c_nf_per_km":  10.0,
        "max_i_ka":     0.520,
    }, name="Edmonton_OH_336ACSR", element="line")

    # UG 500 AL
    pp.create_std_type(net, {
        "r_ohm_per_km": 0.125,
        "x_ohm_per_km": 0.110, 
        "c_nf_per_km":  300.0,
        "max_i_ka":     0.480,
    }, name="Edmonton_UG_500AL", element="line")

    # OH 1/0 ACSR
    pp.create_std_type(net, {
        "r_ohm_per_km": 0.306,
        "x_ohm_per_km": 0.430,
        "c_nf_per_km":  8.0,
        "max_i_ka":     0.270,
    }, name="Edmonton_OH_1_0ACSR", element="line")

    # 4/0 AL XLPE URD
    pp.create_std_type(net, {
        "r_ohm_per_km": 0.227,
        "x_ohm_per_km": 0.120,
        "c_nf_per_km":  250.0,
        "max_i_ka":     0.270,
    }, name="Edmonton_UG_4_0AL", element="line")

    # Substation transformers
    # AESO 138 kV
    pp.create_std_type(net, {
        "sn_mva":          50.0,
        "vn_hv_kv":       138.0,
        "vn_lv_kv":        14.4,
        "vk_percent":      12.0,
        "vkr_percent":      0.35,
        "pfe_kw":          35.0,
        "i0_percent":       0.06,
        "shift_degree":    30.0,
    }, name="Edmonton_Sub_138_14", element="trafo")

    # Distribution: 72 kV → 14.4 kV 
    pp.create_std_type(net, {
        "sn_mva":          25.0,
        "vn_hv_kv":        72.0,
        "vn_lv_kv":        14.4,
        "vk_percent":      12.5,
        "vkr_percent":      0.40,
        "pfe_kw":          15.0,
        "i0_percent":       0.08,
        "shift_degree":    30.0,
    }, name="Edmonton_Sub_72_14", element="trafo")

    info("Standard types registered (OH/UG lines, 138/14.4 kV and 72/14.4 kV transformers)")

    stage("Parse geometry", f"{len(df):,} rows")
    bus_coords: dict[tuple, int] = {}
    lines_data: list[dict] = []
    skipped = 0

    for idx, row in df.iterrows():
        if (idx + 1) % max(1, len(df) // 20) == 0 or (idx + 1) == len(df):
            progress(idx + 1, len(df))

        geom_str  = row["Geometry"]
        service   = str(row.get("Service", "Overhead")).strip()
        volt_str  = str(row.get("Voltage", "25 kV")).strip()
        raw_phase = row.get("Phase", 1)
        phase     = int(raw_phase) if pd.notna(raw_phase) else 1

        voltage_kv   = map_voltage_kv(volt_str)
        line_segments = parse_multilinestring(geom_str)

        for seg in line_segments:
            endpoints = extract_endpoints(seg)
            if not endpoints:
                skipped += 1
                continue

            raw_start, raw_end = endpoints
            start = round_coord(raw_start)
            end   = round_coord(raw_end)

            if start == end:
                skipped += 1
                continue

            if start not in bus_coords:
                bus_coords[start] = len(bus_coords)
            if end not in bus_coords:
                bus_coords[end] = len(bus_coords)

            length_km = max(0.005, haversine_km(
                start[0], start[1], end[0], end[1]
            ))

            lines_data.append({
                "from_bus":  bus_coords[start],
                "to_bus":    bus_coords[end],
                "length_km": length_km,
                "voltage_kv": voltage_kv,
                "service":   service,
                "phase":     phase,
                "start_coord": start,
                "end_coord":   end,
            })

    info(f"Buses (unique nodes): {len(bus_coords):,}")
    info(f"Line segments:        {len(lines_data):,}")
    info(f"Skipped (degenerate): {skipped:,}")

    stage("Create buses", f"{len(bus_coords):,} nodes")
    # Convert bus_coords to lists for bulk creation
    bus_list = list(bus_coords.items())
    coords = [c for c, _ in bus_list]
    ids = [i for _, i in bus_list]
    
    # Pre-calculate geo for bulk creation
    geos = [json.dumps({"type": "Point", "coordinates": list(c)}) for c in coords]
    
    pp.create_buses(net, 
                    nr_buses=len(bus_list), 
                    name=[f"Bus_{i}" for i in ids],
                    vn_kv=14.4, 
                    type="b",
                    geodata=coords)
    # Bulk set 'geo' column
    net.bus['geo'] = geos

    # Set correct voltage per bus from line data
    # (Doing this in bulk as well)
    from_buses = [ld["from_bus"] for ld in lines_data]
    to_buses = [ld["to_bus"] for ld in lines_data]
    voltages = [ld["voltage_kv"] for ld in lines_data]
    
    # Efficiently update bus voltages
    bus_v_updates = pd.DataFrame({'bus': from_buses + to_buses, 'v': voltages + voltages})
    max_v = bus_v_updates.groupby('bus')['v'].max()
    net.bus.loc[max_v.index, 'vn_kv'] = max_v.values

    kv_counts = net.bus["vn_kv"].value_counts().sort_index().to_dict()
    info(f"Bus voltage distribution (kV): {kv_counts}")

    stage("Create lines", f"{len(lines_data):,} segments")

    def pick_std_type(service: str, phase: int) -> str:
        if service.lower() == "overhead":
            return "Edmonton_OH_336ACSR" if phase == 3 else "Edmonton_OH_1_0ACSR"
        else:
            return "Edmonton_UG_500AL" if phase == 3 else "Edmonton_UG_4_0AL"

    std_types = [pick_std_type(ld["service"], ld["phase"]) for ld in lines_data]
    names = [f"{ld['service']}_{ld['voltage_kv']}kV_Ph{ld['phase']}" for ld in lines_data]
    
    pp.create_lines(net, 
                    from_buses=[ld["from_bus"] for ld in lines_data],
                    to_buses=[ld["to_bus"] for ld in lines_data],
                    length_km=[ld["length_km"] for ld in lines_data],
                    std_type=std_types,
                    name=names)

    oh_count = sum(1 for ld in lines_data if ld["service"].lower() == "overhead")
    ug_count = len(lines_data) - oh_count
    info(f"Overhead lines: {oh_count:,}  |  Underground cables: {ug_count:,}")
    total_km = sum(ld["length_km"] for ld in lines_data)
    info(f"Total conductor length: {total_km:.2f} km")

    stage("Build network graph and find islands")
    mg      = top.create_nxgraph(net)
    islands = sorted(top.connected_components(mg), key=len, reverse=True)
    info(f"Connected islands found: {len(islands)}")
    for k, isl in enumerate(islands[:5]):
        info(f"  Island {k}: {len(isl):,} buses")
    if len(islands) > 5:
        info(f"  … and {len(islands) - 5} smaller islands")

    stage("Connect islands to transmission grid",
          "attaching substations proportional to island size")

    trafos_count = 0
    # Process islands in order of size. 
    # We need enough capacity for ~1100 MW peak load.
    for i, island_buses_set in enumerate(islands):
        island_buses = list(island_buses_set)
        if not island_buses:
            continue

        # Determine number of substations for this island based on size
        if len(island_buses) > 15000:
            n_subs = 30  # Very large city-scale components
        elif len(island_buses) > 5000:
            n_subs = 10
        elif len(island_buses) > 1000:
            n_subs = 3
        elif i < 50: 
            n_subs = 1
        else:
            # For small islands, only add a source if there is actually load
            island_has_load = any(net.load.bus.isin(island_buses))
            n_subs = 1 if island_has_load else 0

        for j in range(n_subs):
            # Spread substations across the island
            sub_lv_bus = island_buses[(j * len(island_buses)) // n_subs]

            # Retrieve coordinates
            geo_val = net.bus.at[sub_lv_bus, "geo"] if "geo" in net.bus.columns else None
            if geo_val is None:
                continue
            try:
                if isinstance(geo_val, str):
                    gj = json.loads(geo_val)
                    coord_x, coord_y = gj["coordinates"]
                else:
                    coord_x, coord_y = float(geo_val[0]), float(geo_val[1])
            except Exception:
                continue

            # Alternate substation types
            # Bulk (138kV) for very large islands or primary connections
            if i == 0 or (i < 5 and j % 2 == 0):
                hv_kv    = 138.0
                trafo_type = "Edmonton_Sub_138_14"
                sub_label  = "138kV_Bulk"
                vm_pu      = 1.03
            else:
                hv_kv    = 72.0
                trafo_type = "Edmonton_Sub_72_14"
                sub_label  = "72kV_Dist"
                vm_pu      = 1.02

            lv_kv = net.bus.at[sub_lv_bus, "vn_kv"]

            sub_hv_bus = pp.create_bus(
                net,
                name=f"Island_{i}_Sub_{j}_{sub_label}",
                vn_kv=hv_kv,
                type="b",
                geodata=(coord_x, coord_y),
                geo=json.dumps({"type": "Point", "coordinates": [coord_x, coord_y]}),
            )

            pp.create_ext_grid(net, bus=sub_hv_bus, vm_pu=vm_pu,
                               name=f"AESO_EPCOR_Source_{i}_{j}")

            # Only add transformer if LV bus is on 25 kV or 14.4 kV
            if lv_kv <= 25.0:
                pp.create_transformer(
                    net,
                    hv_bus=sub_hv_bus,
                    lv_bus=sub_lv_bus,
                    std_type=trafo_type,
                    name=f"Sub_Trafo_{i}_{j}_{sub_label}",
                )
                trafos_count += 1

    info(f"Substations attached: {trafos_count}")

    info(f"External grids: {len(net.ext_grid)}")
    info(f"Transformers:   {len(net.trafo)}")

    stage("Add loads")

    dist_buses = net.bus[net.bus["vn_kv"] <= 25.0].index.tolist()
    rng = np.random.default_rng(seed=42)

    # Build a phase lookup from line data so we know each bus's dominant phase
    bus_phase: dict[int, int] = {}
    for ld in lines_data:
        for b in (ld["from_bus"], ld["to_bus"]):
            if b not in bus_phase or ld["phase"] > bus_phase[b]:
                bus_phase[b] = ld["phase"]

    if buildings_path:
        info(f"Assigning loads based on buildings: {buildings_path}")
        try:
            bld_gdf = gpd.read_file(buildings_path)
            
            # Get centroids in projected CRS (EPSG:3776) for accuracy
            bld_gdf['centroid'] = bld_gdf.geometry.centroid
            
            # Reproject to WGS84
            if bld_gdf.crs != "EPSG:4326":
                info(f"Reprojecting buildings from {bld_gdf.crs} to EPSG:4326")
                # Reproject both geometry and centroid
                bld_gdf = bld_gdf.to_crs("EPSG:4326")
                bld_gdf['centroid'] = bld_gdf['centroid'].to_crs("EPSG:4326")
            
            # Get coordinates
            bld_coords = np.array([[c.x, c.y] for c in bld_gdf['centroid']])
            
            # Get distribution bus coordinates
            dist_bus_ids = dist_buses
            # Mapping from bus_id to its (lon, lat) from the original bus_coords dict
            id_to_coords = {v: k for k, v in bus_coords.items()}
            bus_points = np.array([id_to_coords[b] for b in dist_bus_ids])
            
            # Build KDTree and query
            tree = KDTree(bus_points)
            dist, indices = tree.query(bld_coords)
            
            # Aggregate area by bus
            bus_area = {}
            for i, bus_idx_in_list in enumerate(indices):
                bus_id = dist_bus_ids[bus_idx_in_list]
                area = bld_gdf.iloc[i].get('area', 100.0) # Default if missing
                bus_area[bus_id] = bus_area.get(bus_id, 0) + area
            
            info(f"Mapped {len(bld_gdf)} buildings to {len(bus_area)} buses.")
            
            # Target total peak demand for Edmonton is ~1100 MW
            TARGET_PEAK_MW = 1100.0
            total_area = sum(bus_area.values())
            mw_per_m2 = TARGET_PEAK_MW / total_area
            
            info(f"Total building area: {total_area:,.2f} m^2")
            info(f"Calculated power density: {mw_per_m2 * 1e6:.2f} W/m^2 to reach {TARGET_PEAK_MW} MW")
            
            for bus_id, area in bus_area.items():
                ph = bus_phase.get(int(bus_id), 1)
                p_mw = area * mw_per_m2
                # Distinguish load type for naming/analysis
                load_type = "commercial" if area > 1000 or ph == 3 else "residential"
                
                pp.create_load(net,
                               bus=bus_id,
                               p_mw=float(p_mw),
                               q_mvar=float(p_mw * 0.22), # PF ≈ 0.977
                               name=f"{load_type}_Load_{bus_id}")
        except Exception as e:
            info(f"Error processing buildings: {e}. Falling back to random loads.")
            buildings_path = None

    if not buildings_path:
        load_bus_arr = rng.choice(dist_buses,
                                  size=min(int(len(dist_buses) * 0.30), len(dist_buses)),
                                  replace=False)

        n_loads = len(load_bus_arr)
        for i, bus_idx in enumerate(load_bus_arr):
            if (i + 1) % max(1, n_loads // 20) == 0 or (i + 1) == n_loads:
                progress(i + 1, n_loads)

            ph = bus_phase.get(int(bus_idx), 1)

            if ph == 3:
                # Commercial / industrial: median 40 kW, heavy tail
                p_mw = float(np.clip(rng.lognormal(np.log(0.040), 1.1), 0.005, 2.0))
                load_type = "commercial"
            else:
                # Residential: median 10 kW, tighter spread
                p_mw = float(np.clip(rng.lognormal(np.log(0.010), 0.70), 0.002, 0.100))
                load_type = "residential"

            pp.create_load(net,
                           bus=bus_idx,
                           p_mw=p_mw,
                           q_mvar=p_mw * 0.22,   # PF ≈ 0.977 (typical Edmonton mix)
                           name=f"{load_type}_Load_{bus_idx}")

    total_p = net.load["p_mw"].sum()
    info(f"Loads created:  {len(net.load):,}")
    info(f"Total demand P: {total_p:.2f} MW  "
         f"(target 900–1 300 MW for Edmonton peak)")
    if total_p < 900:
        info("  ⚠ Total demand is below expected peak range — "
             "consider increasing load_fraction or median load sizes.")

    print(f"\n{'#'*60}")
    print(f"  NETWORK SUMMARY")
    print(f"{'#'*60}")
    print(f"  Frequency    : {net.f_hz} Hz")
    print(f"  Base MVA     : {net.sn_mva} MVA")
    print(f"  Buses        : {len(net.bus):,}")
    print(f"  Lines        : {len(net.line):,}")
    print(f"  Loads        : {len(net.load):,}")
    print(f"  Load (kW)    : {net.load['p_mw'].sum() * 1000:.2f}")
    print(f"  Solar SGen   : {len(net.sgen):,}")
    print(f"  Solar (kW)   : {net.sgen['p_mw'].sum() * 1000:.2f}")
    print(f"  Transformers : {len(net.trafo):,}")
    print(f"  Ext grids    : {len(net.ext_grid):,}")
    print(f"  Switches     : {len(net.switch):,}")
    print(f"  Shunts       : {len(net.shunt):,}")
    print(f"\n  Total wall time: {_elapsed(_run_start)}")
    print(f"{'#'*60}\n")

    return net

if __name__ == "__main__":
    csv_path    = "./data/csv/Circuit_Layer_20260430.csv"
    buildings_path = "./data/geojson/merged_buildings.geojson"
    output_path = "./data/output/circuit_network.geojson"
    json_path   = "./data/json/circuit_network.json"

    net = create_network_from_csv(csv_path, output_path, buildings_path=buildings_path, max_lines=None)
    
    print(f"Saving network to {json_path}...")
    pp.to_json(net, json_path)
    print("Done.")
