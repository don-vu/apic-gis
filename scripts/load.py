import pandapower as pp
import pandapower.plotting as plot
import matplotlib.pyplot as plt

def main():
    # 1. Load the network from a .json file
    json_file_path = "/Users/donvu/Developer/apic/Solar-Labs-LTD/data/circuit_network.json"  # Replace with your actual file path
    print(f"Loading the network from {json_file_path}...")
    
    try:
        net = pp.from_json(json_file_path)
    except FileNotFoundError:
        print(f"Error: The file '{json_file_path}' was not found.")
        return
    except Exception as e:
        print(f"An error occurred while loading the JSON: {e}")
        return

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
    print("\nPre-processing network: solving islands individually...")
    import pandapower.topology as top
    
    # Find all connected components
    mg = top.create_nxgraph(net)
    islands = list(top.connected_components(mg))
    
    # Identify buses with external grid (slack buses)
    slack_buses = set(net.ext_grid.bus.values)
    
    # Track which islands we can solve
    solvable_islands = []
    for island in islands:
        if any(bus in slack_buses for bus in island):
            solvable_islands.append(island)
            
    print(f"Found {len(solvable_islands)} solvable islands (with slack buses).")
    
    # Initialize result columns in net.res_bus and net.res_line
    # (otherwise they might not exist if no power flow was ever successful)
    import numpy as np
    for table in ['bus', 'line']:
        res_table = f"res_{table}"
        if res_table not in net:
            net[res_table] = pd.DataFrame(index=net[table].index, columns=[])

    # Deactivate everything first
    net.bus.in_service = False
    
    converged_count = 0
    for i, island in enumerate(solvable_islands):
        # Activate only this island
        net.bus.loc[list(island), 'in_service'] = True
        
        # We need to make sure lines between these buses are also in_service
        # (Usually lines are in_service=True by default, but we should be careful)
        
        try:
            pp.runpp(net, max_iteration=50)
            converged_count += 1
            if converged_count % 10 == 0 or i == 0:
                print(f"  Island {i+1}/{len(solvable_islands)} converged.")
        except:
            # print(f"  Island {i+1} failed to converge.")
            pass
        
        # Deactivate again for the next island
        net.bus.loc[list(island), 'in_service'] = False

    # Reactivate all solvable islands for visualization (optional)
    for island in solvable_islands:
        net.bus.loc[list(island), 'in_service'] = True
        
    print(f"\nPower flow completed. {converged_count}/{len(solvable_islands)} islands converged.")

    # 5. View the Power Flow Results
    if converged_count > 0:
        print("\n--- Power Flow Results Summary ---")
        if 'vm_pu' in net.res_bus.columns:
            valid_res = net.res_bus[net.res_bus.vm_pu.notna()]
            print(f"Buses with valid results: {len(valid_res)}")
            print(f"Average Voltage: {valid_res.vm_pu.mean():.4f} pu")

    # 6. Visually plot the network topology
    print("\nGenerating network plot. A window should appear shortly.")
    print("Close the matplotlib plot window to end the script.")
    
    # simple_plot creates a straightforward node-breaker/bus-branch diagram
    plot.simple_plot(net, show_plot=True)

if __name__ == "__main__":
    main()