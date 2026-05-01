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
            
            converged_islands = 0
            for i in islands_with_ext_grid:
                island_buses = list(islands[i])
                net.bus['in_service'] = False
                net.bus.loc[net.bus.index.isin(island_buses), 'in_service'] = True
                
                # Also ensure lines/trafos/loads connected to these buses are in service
                # Pandapower usually handles this if buses are out of service, but let's be explicit
                
                try:
                    pp.runpp(net, algorithm='nr', max_iteration=100, numba=False)
                    print(f"  Island {i} ({len(island_buses)} buses) converged.")
                    converged_islands += 1
                    
                    # Store results, dropping rows that are not in this island
                    all_res_line.append(net.res_line.dropna(subset=['loading_percent']))
                    all_res_trafo.append(net.res_trafo.dropna(subset=['loading_percent']))
                    all_res_bus.append(net.res_bus.dropna(subset=['vm_pu']))
                    success = True 
                except Exception as e:
                    print(f"  Island {i} ({len(island_buses)} buses) failed to converge: {e}")
                    if len(island_buses) > 1000:
                        print("  Performing diagnostics on this large island...")
                        # Check for loops
                        import networkx as nx
                        # Convert MultiGraph to Graph for cycle_basis
                        simple_sub_mg = nx.Graph(mg.subgraph(island_buses))
                        if not nx.is_tree(simple_sub_mg):
                            cycles = nx.cycle_basis(simple_sub_mg)
                            print(f"    Island has {len(cycles)} cycles. (Not strictly radial)")
                        else:
                            print("    Island is strictly radial (a tree).")
                        
                        # Check for multiple ext_grids
                        island_ext_grids = net.ext_grid[net.ext_grid.bus.isin(island_buses)]
                        print(f"    Island has {len(island_ext_grids)} external grids.")

            if success:
                print(f"\nSuccessfully converged {converged_islands} islands out of {len(islands_with_ext_grid)}.")
                # Combine results
                net.res_line = pd.concat(all_res_line).sort_index() if all_res_line else pd.DataFrame()
                net.res_trafo = pd.concat(all_res_trafo).sort_index() if all_res_trafo else pd.DataFrame()
                net.res_bus = pd.concat(all_res_bus).sort_index() if all_res_bus else pd.DataFrame()
                
                # Ensure we only have unique indices (in case of overlap, though there shouldn't be any)
                net.res_line = net.res_line[~net.res_line.index.duplicated(keep='first')]
                net.res_trafo = net.res_trafo[~net.res_trafo.index.duplicated(keep='first')]
                net.res_bus = net.res_bus[~net.res_bus.index.duplicated(keep='first')]
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
        print("\nTransformer Loadings:")
        for idx, row in top_trafos.iterrows():
            print(f"  Trafo {idx} ({row['name']}): {top_trafos_loadings[idx]:.2f}%")

    # Overall loss
    p_loss_mw = net.res_line.p_from_mw.sum() - net.res_line.p_to_mw.sum() + net.res_trafo.p_hv_mw.sum() - net.res_trafo.p_lv_mw.sum()
    print(f"\nTotal Network Losses: {p_loss_mw:.4f} MW")

if __name__ == "__main__":
    path = "./data/json/circuit_network.json"
    if len(sys.argv) > 1:
        path = sys.argv[1]
    run_power_flow(path)
