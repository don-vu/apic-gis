import pandas as pd
import pandapower as pp
from shapely import wkt
import re
import numpy as np

def clean_voltage(val):
    if pd.isna(val): return 13.8
    numeric_part = re.sub(r'[^0-9.]', '', str(val))
    return float(numeric_part) if numeric_part else 13.8

def create_complete_network(file_path):
    df = pd.read_csv(file_path)
    net = pp.create_empty_network()
    coords_to_bus = {}

    print("Building topology...")
    for index, row in df.iterrows():
        try:
            vn_kv = clean_voltage(row['Voltage'])
            line_geom = wkt.loads(row['Geometry'])
            
            # Extract coordinates from the first part of the multiline
            coords = list(line_geom.geoms[0].coords)
            start_pt = tuple(round(c, 6) for c in coords[0])
            end_pt = tuple(round(c, 6) for c in coords[-1])

            def get_or_create_bus(coord, voltage):
                if coord not in coords_to_bus:
                    b_idx = pp.create_bus(net, vn_kv=voltage, geodata=coord)
                    coords_to_bus[coord] = b_idx
                return coords_to_bus[coord]

            from_bus = get_or_create_bus(start_pt, vn_kv)
            to_bus = get_or_create_bus(end_pt, vn_kv)

            # Create Line
            dist_km = ((start_pt[0]-end_pt[0])**2 + (start_pt[1]-end_pt[1])**2)**0.5 * 111
            pp.create_line_from_parameters(
                net, from_bus=from_bus, to_bus=to_bus, length_km=max(dist_km, 0.001),
                r_ohm_per_km=0.1, x_ohm_per_km=0.1, c_nf_per_km=150, max_i_ka=0.4
            )
        except Exception:
            continue

    # --- NEW: POPULATING FUNCTIONAL TABLES ---

    # 1. External Grid (The Connection to Alberta's Main Grid)
    # We'll attach this to the first bus created as our "Slack" bus
    if len(net.bus) > 0:
        pp.create_ext_grid(net, bus=0, vm_pu=1.0, name="Main Substation Connection")

    # 2. Loads (Representing Edmonton neighborhoods/customers)
    # We will add a small load (e.g., 0.1 MW) to every "leaf" bus (buses with only 1 connection)
    print("Populating loads and generators...")
    for b_idx in net.bus.index:
        # Count connections to this bus
        connections = len(net.line[net.line.from_bus == b_idx]) + len(net.line[net.line.to_bus == b_idx])
        
        if connections == 1: # It's an endpoint/cul-de-sac
            pp.create_load(net, bus=b_idx, p_mw=0.1, q_mvar=0.02, name=f"Load_at_Bus_{b_idx}")

    # 3. Generators (Representing local Distributed Energy Resources)
    # Let's place a few solar-sized generators (0.05 MW) randomly at 5% of the buses
    gen_indices = np.random.choice(net.bus.index, size=int(len(net.bus)*0.05), replace=False)
    for g_idx in gen_indices:
        pp.create_sgen(net, bus=g_idx, p_mw=0.05, q_mvar=0, name=f"Solar_Gen_{g_idx}")

    return net

if __name__ == "__main__":
    net = create_complete_network("/Users/donvu/Developer/apic/Solar-Labs-LTD/data/Circuit_Layer_20260430.csv")
    print("\n--- Final Data Table Counts ---")
    print(f"Buses: {len(net.bus)}")
    print(f"Lines: {len(net.line)}")
    print(f"Loads (Consumers): {len(net.load)}")
    print(f"Static Generators (Solar/Local): {len(net.sgen)}")
    print(f"External Grid: {len(net.ext_grid)}")
    
        # 2. View the basic network architecture
    print("\n--- Network Summary ---")
    print(net)

    # 3. Inspect specific component data tables
    print("\n--- Bus Data ---")
    print(net.bus)
    
    print("\n--- Line Data ---")
    print(net.line)
    
    print("\n--- Load Data ---")
    print(net.load)

    # 4. Run a Power Flow Calculation 
    # This solves the network to give us voltages, line loadings, etc.
    print("\nRunning power flow calculation (Newton-Raphson)...")
    pp.runpp(net)

    # 5. View the Power Flow Results
    print("\n--- Power Flow Results: Bus Voltages ---")
    print(net.res_bus[['vm_pu', 'va_degree', 'p_mw', 'q_mvar']])
    
    print("\n--- Power Flow Results: Line Loading ---")
    print(net.res_line[['loading_percent', 'p_from_mw', 'p_to_mw']])

    # 6. Visually plot the network topology
    print("\nGenerating network plot. A window should appear shortly.")
    print("Close the matplotlib plot window to end the script.")
    
    # simple_plot creates a straightforward node-breaker/bus-branch diagram
    # plot.simple_plot(net, show_plot=True)
    pp.to_json(net, "edmonton_grid.json")