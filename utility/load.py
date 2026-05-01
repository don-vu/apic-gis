import pandapower as pp
import pandapower.plotting as plot
import matplotlib.pyplot as plt

def main():
    json_file_path = "./data/json/circuit_network.json"
    print(f"Loading the network from {json_file_path}...")
    
    try:
        net = pp.from_json(json_file_path)
    except FileNotFoundError:
        print(f"Error: The file '{json_file_path}' was not found.")
        return
    except Exception as e:
        print(f"An error occurred while loading the JSON: {e}")
        return

    print("\n--- Network Summary ---")
    print(net)

    print("\n--- Bus Data ---")
    print(net.bus)
    
    print("\n--- Line Data ---")
    print(net.line)
    
    print("\n--- Load Data ---")
    print(net.load)

    print("\nPre-processing network: solving islands individually...")
    import pandapower.topology as top
    
    # Find all connected components
    mg = top.create_nxgraph(net)
    islands = list(top.connected_components(mg))
    
    slack_buses = set(net.ext_grid.bus.values)
    
    solvable_islands = []
    for island in islands:
        if any(bus in slack_buses for bus in island):
            solvable_islands.append(island)
            
    print(f"Found {len(solvable_islands)} solvable islands (with slack buses).")
    
    import numpy as np
    for table in ['bus', 'line']:
        res_table = f"res_{table}"
        if res_table not in net:
            net[res_table] = pd.DataFrame(index=net[table].index, columns=[])

    net.bus.in_service = False
    
    converged_count = 0
    for i, island in enumerate(solvable_islands):
        net.bus.loc[list(island), 'in_service'] = True
        
        try:
            pp.runpp(net, max_iteration=50)
            converged_count += 1
            if converged_count % 10 == 0 or i == 0:
                print(f"  Island {i+1}/{len(solvable_islands)} converged.")
        except:
            pass
        
        net.bus.loc[list(island), 'in_service'] = False

    for island in solvable_islands:
        net.bus.loc[list(island), 'in_service'] = True
        
    print(f"\nPower flow completed. {converged_count}/{len(solvable_islands)} islands converged.")

    if converged_count > 0:
        print("\n--- Power Flow Results Summary ---")
        if 'vm_pu' in net.res_bus.columns:
            valid_res = net.res_bus[net.res_bus.vm_pu.notna()]
            print(f"Buses with valid results: {len(valid_res)}")
            print(f"Average Voltage: {valid_res.vm_pu.mean():.4f} pu")

    print("\nGenerating network plot. A window should appear shortly.")
    print("Close the matplotlib plot window to end the script.")
    
    plot.simple_plot(net, show_plot=True)

if __name__ == "__main__":
    main()