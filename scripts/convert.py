import pandas as pd
import pandapower as pp
import pandapower.plotting as plot
from shapely import wkt
import re

def clean_voltage(val):
    """Extracts numeric value from strings like '25 kV' or '15 kV'"""
    if pd.isna(val):
        return 13.8 # Default fallback voltage
    # Remove everything except digits and decimal points
    numeric_part = re.sub(r'[^0-9.]', '', str(val))
    return float(numeric_part) if numeric_part else 13.8

def create_net_from_csv(file_path):
    df = pd.read_csv(file_path)
    net = pp.create_empty_network()
    coords_to_bus = {}

    print(f"Processing {len(df)} segments...")

    for index, row in df.iterrows():
        try:
            # 1. CLEAN THE VOLTAGE DATA
            vn_kv = clean_voltage(row['Voltage'])
            
            # 2. PARSE GEOMETRY
            line_geom = wkt.loads(row['Geometry'])
            # Take start/end of the first segment in the MULTILINESTRING
            coords = list(line_geom.geoms[0].coords)
            
            # 3. ROUND COORDINATES (Snapping)
            # Rounding to 6 decimal places (~10cm precision) helps lines connect
            start_pt = tuple(round(c, 6) for c in coords[0])
            end_pt = tuple(round(c, 6) for c in coords[-1])

            def get_or_create_bus(coord, voltage):
                if coord not in coords_to_bus:
                    b_idx = pp.create_bus(net, vn_kv=voltage, geodata=coord)
                    coords_to_bus[coord] = b_idx
                return coords_to_bus[coord]

            from_bus = get_or_create_bus(start_pt, vn_kv)
            to_bus = get_or_create_bus(end_pt, vn_kv)

            # 4. CREATE THE LINE
            # Distance estimate: 1 degree approx 111km
            dist_km = ((start_pt[0]-end_pt[0])**2 + (start_pt[1]-end_pt[1])**2)**0.5 * 111

            pp.create_line_from_parameters(
                net, from_bus=from_bus, to_bus=to_bus, length_km=max(dist_km, 0.001),
                r_ohm_per_km=0.1, x_ohm_per_km=0.1, c_nf_per_km=150, max_i_ka=0.4,
                name=f"Line_{index}"
            )

        except Exception as e:
            print(f"Error on row {index}: {e}")

    return net

if __name__ == "__main__":
    net = create_net_from_csv("/Users/donvu/Developer/apic/Solar-Labs-LTD/data/Circuit_Layer_20260430.csv")
    print(f"Success! Created {len(net.bus)} buses and {len(net.line)} lines.")
    
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
    plot.simple_plot(net, show_plot=True)