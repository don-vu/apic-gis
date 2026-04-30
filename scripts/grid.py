import pandapower as pp
import pandapower.networks as pn
import pandapower.plotting as plot
import matplotlib.pyplot as plt

def main():
    # 1. Load the Grainger & Stevenson 4-bus network
    print("Loading the case4gs network...")
    net = pn.create_cigre_network_mv(with_der="all") 


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

if __name__ == "__main__":
    main()