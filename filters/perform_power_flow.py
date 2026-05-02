import pandapower as pp
import pandas as pd
import numpy as np
import sys
import os

def run_power_flow(json_path):
    if not os.path.exists(json_path):
        print(f"Error: File {json_path} not found.")
        return

    print(f"Loading network from {json_path}...")
    try:
        net = pp.from_json(json_path)
    except Exception as e:
        print(f"Error loading network: {e}")
        return

    print("Network loaded successfully.")
    print(f"Buses: {len(net.bus)}")
    print(f"Lines: {len(net.line)}")
    print(f"Loads: {len(net.load)}")
    print(f"Ext Grids: {len(net.ext_grid)}")
    print(f"Transformers: {len(net.trafo)}")

    print("\nRunning power flow...")
    
    # Pre-checks
    if (net.bus.vn_kv <= 0).any():
        print(f"Warning: {(net.bus.vn_kv <= 0).sum()} buses have zero or negative nominal voltage. Setting to 14.4 kV default.")
        net.bus.loc[net.bus.vn_kv <= 0, 'vn_kv'] = 14.4

    # Try different algorithms
    success = False
    for algo in ['nr', 'bfsw']:
        print(f"Attempting with algorithm: {algo}...")
        try:
            pp.runpp(net, algorithm=algo, init='flat', max_iteration=100, numba=False)
            print(f"Power flow converged with {algo}!")
            success = True
            break
        except Exception as e:
            print(f"Algorithm {algo} failed: {e}")

    if not success:
        print("\nAll standard power flow attempts failed. Analyzing islands...")
        import pandapower.topology as top
        mg = top.create_nxgraph(net)
        islands = list(top.connected_components(mg))
        print(f"Number of islands: {len(islands)}")
        
        # Check which islands have external grids
        islands_with_ext_grid = []
        for i, island in enumerate(islands):
            has_ext_grid = any(net.ext_grid.bus.isin(island))
            if has_ext_grid:
                islands_with_ext_grid.append(i)
        
        print(f"Islands with external grids: {len(islands_with_ext_grid)}")
        
        if islands_with_ext_grid:
            print(f"Attempting to run power flow on each island with an external grid separately...")
            
            # Dataframes to accumulate results
            all_res_line = []
            all_res_trafo = []
            all_res_bus = []
            all_res_load = []
            all_res_ext_grid = []
            
            converged_islands = 0
            for i in islands_with_ext_grid:
                island_buses = list(islands[i])
                net.bus['in_service'] = False
                net.bus.loc[net.bus.index.isin(island_buses), 'in_service'] = True
                
                try:
                    pp.runpp(net, algorithm='nr', max_iteration=100, numba=False)
                    print(f"  Island {i} ({len(island_buses)} buses) converged.")
                    converged_islands += 1
                    
                    # Store results
                    all_res_line.append(net.res_line.dropna(subset=['loading_percent']))
                    all_res_trafo.append(net.res_trafo.dropna(subset=['loading_percent']))
                    all_res_bus.append(net.res_bus.dropna(subset=['vm_pu']))
                    all_res_load.append(net.res_load.dropna(subset=['p_mw']))
                    all_res_ext_grid.append(net.res_ext_grid.dropna(subset=['p_mw']))
                    success = True 
                except Exception as e:
                    print(f"  Island {i} ({len(island_buses)} buses) failed to converge: {e}")

            if success:
                print(f"\nSuccessfully converged {converged_islands} islands out of {len(islands_with_ext_grid)}.")
                # Combine results
                net.res_line = pd.concat(all_res_line).sort_index() if all_res_line else pd.DataFrame()
                net.res_trafo = pd.concat(all_res_trafo).sort_index() if all_res_trafo else pd.DataFrame()
                net.res_bus = pd.concat(all_res_bus).sort_index() if all_res_bus else pd.DataFrame()
                net.res_load = pd.concat(all_res_load).sort_index() if all_res_load else pd.DataFrame()
                net.res_ext_grid = pd.concat(all_res_ext_grid).sort_index() if all_res_ext_grid else pd.DataFrame()
                
                # Ensure we only have unique indices
                net.res_line = net.res_line[~net.res_line.index.duplicated(keep='first')]
                net.res_trafo = net.res_trafo[~net.res_trafo.index.duplicated(keep='first')]
                net.res_bus = net.res_bus[~net.res_bus.index.duplicated(keep='first')]
                net.res_load = net.res_load[~net.res_load.index.duplicated(keep='first')]
                net.res_ext_grid = net.res_ext_grid[~net.res_ext_grid.index.duplicated(keep='first')]
        else:
            print("No islands have external grids! Cannot run power flow.")
            return

    # Extract results
    if hasattr(net, 'res_line') and not net.res_line.empty:
        line_loading = net.res_line.loading_percent
        print(f"\nLine Loading Statistics:")
        print(f"  Max Loading: {line_loading.max():.2f}%")
        print(f"  Mean Loading: {line_loading.mean():.2f}%")
        print(f"  Lines > 80%: {len(line_loading[line_loading > 80])}")
        print(f"  Lines > 100%: {len(line_loading[line_loading > 100])}")
        
        # Top 10 loaded lines
        top_lines = net.line.iloc[line_loading.sort_values(ascending=False).head(10).index]
        top_loadings = line_loading.sort_values(ascending=False).head(10)
        print("\nTop 10 Most Loaded Lines:")
        for idx, row in top_lines.iterrows():
            print(f"  Line {idx} ({row['name']}): {top_loadings[idx]:.2f}%")

    if hasattr(net, 'res_trafo') and not net.res_trafo.empty:
        trafo_loading = net.res_trafo.loading_percent
        print(f"\nTransformer Loading Statistics:")
        print(f"  Max Loading: {trafo_loading.max():.2f}%")
        print(f"  Mean Loading: {trafo_loading.mean():.2f}%")
        print(f"  Trafos > 80%: {len(trafo_loading[trafo_loading > 80])}")
        
        # Top transformers
        top_trafos = net.trafo.iloc[trafo_loading.sort_values(ascending=False).index]
        top_trafos_loadings = trafo_loading.sort_values(ascending=False)
        print("\nTop 10 Transformer Loadings:")
        for idx, row in top_trafos.head(10).iterrows():
            print(f"  Trafo {idx} ({row['name']}): {top_trafos_loadings[idx]:.2f}%")

    if hasattr(net, 'res_bus') and not net.res_bus.empty:
        vm_pu = net.res_bus.vm_pu
        print(f"\nBus Voltage Statistics:")
        print(f"  Min Voltage: {vm_pu.min():.4f} pu")
        print(f"  Max Voltage: {vm_pu.max():.4f} pu")
        print(f"  Mean Voltage: {vm_pu.mean():.4f} pu")
        print(f"  Buses < 0.95 pu: {len(vm_pu[vm_pu < 0.95])}")
        print(f"  Buses > 1.05 pu: {len(vm_pu[vm_pu > 1.05])}")

    # Overall summary
    total_served_load_mw = net.res_load.p_mw.sum() if hasattr(net, 'res_load') else 0
    total_configured_load_mw = net.load.p_mw.sum()
    total_gen_mw = net.res_ext_grid.p_mw.sum() if hasattr(net, 'res_ext_grid') else 0
    
    # Active power loss is the sum of P at all ports of branches (since P into branch is positive)
    line_loss = (net.res_line.p_from_mw + net.res_line.p_to_mw).sum() if hasattr(net, 'res_line') else 0
    trafo_loss = (net.res_trafo.p_hv_mw + net.res_trafo.p_lv_mw).sum() if hasattr(net, 'res_trafo') else 0
    total_loss_mw = line_loss + trafo_loss

    print(f"\nNetwork Summary Statistics:")
    print(f"  Total Configured Load: {total_configured_load_mw * 1000:.2f} kW")
    print(f"  Total Served Load:     {total_served_load_mw * 1000:.2f} kW ({total_served_load_mw/total_configured_load_mw*100:.2f}%)")
    print(f"  Total Generation:      {total_gen_mw * 1000:.2f} kW")
    print(f"  Total Network Loss:    {total_loss_mw * 1000:.4f} kW ({total_loss_mw/total_served_load_mw*100:.2f}% of served load if > 0 else 0)")

    # Save results back to JSON
    if success:
        print(f"\nSaving results to {json_path}...")
        pp.to_json(net, json_path)
        print("Done.")

if __name__ == "__main__":
    path = "./data/json/circuit_network.json"
    if len(sys.argv) > 1:
        path = sys.argv[1]
    run_power_flow(path)
