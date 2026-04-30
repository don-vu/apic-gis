import pandas as pd
import pandapower as pp
import pandapower.topology as top
import re
from shapely.geometry import shape, LineString, MultiLineString
from shapely.wkt import loads
import json

def parse_multilinestring(geom_str):
    """Parse MULTILINESTRING WKT to extract start and end points."""
    try:
        geom = loads(geom_str)
        if isinstance(geom, MultiLineString):
            return list(geom.geoms)
        elif isinstance(geom, LineString):
            return [geom]
        else:
            return []
    except Exception as e:
        return []

def extract_endpoints(line):
    """Extract start and end coordinates from a LineString."""
    coords = list(line.coords)
    if len(coords) >= 2:
        return coords[0], coords[-1]
    return None

def create_network_from_csv(csv_path, output_path, max_lines=None):
    """Convert circuit CSV to pandapower network file."""

    # Read the CSV
    df = pd.read_csv(csv_path)
    if max_lines:
        df = df.head(max_lines)
    print(f"Loaded {len(df)} rows from {csv_path}")

    # Create empty pandapower network
    net = pp.create_empty_network()

    # Parse all line segments and extract unique bus locations
    bus_coords = {}  # coordinate tuple -> bus_id mapping
    lines_data = []

    for idx, row in df.iterrows():
        if (idx + 1) % 100 == 0:
            print(f"  Processing row {idx + 1}/{len(df)}...")

        geom_str = row['Geometry']
        service = row.get('Service', 'Unknown')
        voltage_str = row.get('Voltage', '0')
        phase = row.get('Phase', '1')

        # Extract voltage in kV
        voltage_match = re.search(r'(\d+(?:\.\d+)?)', voltage_str)
        voltage_kv = float(voltage_match.group(1)) if voltage_match else 0.0

        # Parse multilinestring geometry
        line_segments = parse_multilinestring(geom_str)

        for segment in line_segments:
            endpoints = extract_endpoints(segment)
            if endpoints:
                start_coord, end_coord = endpoints
                start_tuple = tuple(start_coord)
                end_tuple = tuple(end_coord)

                # Register endpoints as buses
                if start_tuple not in bus_coords:
                    bus_coords[start_tuple] = len(bus_coords)
                if end_tuple not in bus_coords:
                    bus_coords[end_tuple] = len(bus_coords)

                start_bus = bus_coords[start_tuple]
                end_bus = bus_coords[end_tuple]

                # Skip self-loops
                if start_bus == end_bus:
                    continue

                # Calculate length in km (rough estimate from lat/lon difference)
                lat_diff = abs(start_coord[1] - end_coord[1])
                lon_diff = abs(start_coord[0] - end_coord[0])
                length_km = (lat_diff**2 + lon_diff**2)**0.5 * 111  # rough conversion

                lines_data.append({
                    'from_bus': start_bus,
                    'to_bus': end_bus,
                    'length_km': length_km,
                    'voltage_kv': voltage_kv,
                    'service': service,
                    'phase': phase,
                    'start_coord': start_coord,
                    'end_coord': end_coord
                })

    print(f"Created {len(bus_coords)} buses and {len(lines_data)} lines")
    print("Creating buses in network...")

    # Create buses
    for i, (coord, bus_id) in enumerate(bus_coords.items()):
        if (i + 1) % 1000 == 0:
            print(f"  Created {i + 1}/{len(bus_coords)} buses...")
        pp.create_bus(net, name=f"Bus_{bus_id}", vn_kv=15.0,
                      geodata=coord)

    # Get voltage levels from the data
    voltage_levels = set()
    for line in lines_data:
        if line['voltage_kv'] > 0:
            voltage_levels.add(line['voltage_kv'])

    # Update bus voltage levels based on connected lines
    print("Updating bus voltage levels...")
    for i, line in enumerate(lines_data):
        if (i + 1) % 5000 == 0:
            print(f"  Updated {i + 1}/{len(lines_data)} lines...")
        if line['voltage_kv'] > 0:
            net.bus.loc[line['from_bus'], 'vn_kv'] = line['voltage_kv']
            net.bus.loc[line['to_bus'], 'vn_kv'] = line['voltage_kv']

    # Create lines
    print("Creating lines in network...")
    for i, line_data in enumerate(lines_data):
        if (i + 1) % 5000 == 0:
            print(f"  Created {i + 1}/{len(lines_data)} lines...")
        pp.create_line(net,
                       from_bus=line_data['from_bus'],
                       to_bus=line_data['to_bus'],
                       length_km=max(0.001, line_data['length_km']),
                       std_type='NAYY 4x50 SE',
                       name=f"Line_{line_data['from_bus']}_{line_data['to_bus']}")

    # Create external grid (slack bus for power flow)
    print("Creating external grid and loads/generators...")
    if len(net.bus) > 0:
        # Create slack buses for the largest islands
        mg = top.create_nxgraph(net)
        islands = sorted(top.connected_components(mg), key=len, reverse=True)
        
        # Add a slack bus to the first 50 largest islands to ensure most of the network is solvable
        for i in range(min(50, len(islands))):
            island_buses = list(islands[i])
            if island_buses:
                pp.create_ext_grid(net, bus=island_buses[0], vm_pu=1.0, va_degree=0)

    # Add a few sample loads (distributed)
    bus_indices = net.bus.index.tolist()
    num_loads = len(bus_indices) // 10
    if num_loads > 0:
        import numpy as np
        load_buses = np.random.choice(bus_indices, num_loads, replace=False)
        for i, bus_idx in enumerate(load_buses):
            pp.create_load(net, bus=bus_idx, p_mw=0.01, q_mvar=0.005,
                           name=f"Load_{bus_idx}")

    # Add a few sample generators (distributed)
    num_gens = len(bus_indices) // 50
    if num_gens > 0:
        gen_buses = np.random.choice(bus_indices, num_gens, replace=False)
        for i, bus_idx in enumerate(gen_buses):
            # Check if bus already has a load or is a slack bus
            if bus_idx in net.ext_grid.bus.values:
                continue
            pp.create_gen(net, bus=bus_idx, p_mw=0.05, vm_pu=1.0,
                          name=f"Gen_{bus_idx}")

    # Add transformers connecting different voltage levels if applicable
    print("Adding transformers and other elements...")
    if len(voltage_levels) > 1:
        voltage_list = sorted(voltage_levels)
        # Connect buses of different voltages with transformers
        for i in range(0, len(net.bus), max(1, len(net.bus)//5)):
            bus_idx = net.bus.index[i] if i < len(net.bus) else net.bus.index[0]
            if i + 1 < len(net.bus):
                bus_idx2 = net.bus.index[i + 1]
                try:
                    pp.create_transformer(net, hv_bus=bus_idx, lv_bus=bus_idx2,
                                         std_type='0.4 MVA 10/0.4 kV',
                                         name=f"Trafo_{bus_idx}_{bus_idx2}")
                except:
                    pass

    # Add shunt elements to some buses
    num_shunts = max(1, len(net.bus) // 15)
    for i, bus_idx in enumerate(net.bus.index[-num_shunts:]):
        pp.create_shunt(net, bus=bus_idx, p_mw=0.01, q_mvar=0.02,
                        name=f"Shunt_{bus_idx}")

    # Add switches at some lines
    for i, line_idx in enumerate(net.line.index[::max(1, len(net.line)//10)]):
        pp.create_switch(net, bus=net.line.loc[line_idx, 'from_bus'],
                         element=line_idx, et='l', closed=True,
                         name=f"Switch_{line_idx}")

    # Add impedance elements
    if len(net.bus) > 1:
        try:
            pp.create_impedance(net, from_bus=net.bus.index[0],
                               to_bus=net.bus.index[1],
                               rft_pu=0.01, xft_pu=0.01, snt_mva=10,
                               name="Impedance_1")
        except:
            pass

    # Create a static generator on a bus
    if len(net.bus) > 2:
        try:
            pp.create_sgen(net, bus=net.bus.index[2], p_mw=0.5, q_mvar=0.1,
                          name="SGen_1")
        except:
            pass

    # Save to file
    print(f"Saving network to {output_path}...")
    pp.to_json(net, output_path)
    print(f"Network saved to {output_path}")

    # Print network summary
    print("\nNetwork Summary:")
    print(f"  Buses: {len(net.bus)}")
    print(f"  Lines: {len(net.line)}")
    print(f"  Loads: {len(net.load)}")
    print(f"  Generators: {len(net.gen)}")
    print(f"  External Grids: {len(net.ext_grid)}")
    print(f"  Transformers: {len(net.trafo)}")
    print(f"  Shunts: {len(net.shunt)}")
    print(f"  Switches: {len(net.switch)}")
    print(f"  Impedances: {len(net.impedance)}")
    print(f"  Static Generators: {len(net.sgen)}")

    return net

if __name__ == "__main__":
    csv_path = "/Users/donvu/Developer/apic/Solar-Labs-LTD/data/Circuit_Layer_20260430.csv"
    output_path = "/Users/donvu/Developer/apic/Solar-Labs-LTD/data/circuit_network.json"

    net = create_network_from_csv(csv_path, output_path, max_lines=None)
