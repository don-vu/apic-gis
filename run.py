#!/usr/bin/env python3
"""
Edmonton GIS & Power Flow Data Pipeline Runner

This script automates the 8-stage data pipeline for downloading orthophotos,
extracting building footprints, compiling the power distribution network, 
simulating power flow, and optimizing datasets for rendering.

Stages:
1. Download TIFs (utility/download.py)
2. Extract buildings (filters/building_extractor.py)
3. Aggregate GeoJSONs (utility/aggregator.py)
4. Build circuit network (filters/circuit_to_pandapower.py)
5. Perform power flow (filters/perform_power_flow.py)
6. Export network to GeoJSON (filters/json_to_geojson.py)
7. Encode to Parquet (utility/encoder.py)
8. Optimize Parquets (utility/optimize_data.py)
"""

import os
import sys
import time
import argparse
import subprocess
import logging
from typing import List, Dict, Tuple

# Set up logging format
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("data_pipeline.log", mode="a", encoding="utf-8")
    ]
)
logger = logging.getLogger("pipeline")

# Define step registry
STEPS = {
    1: {
        "name": "download",
        "desc": "Download orthophoto tiles from Google Drive",
        "script": "utility/download.py",
        "inputs": ["data/csv/Orthophoto_Repository_2023_20260603.csv"],
        "outputs": ["data/tif"]
    },
    2: {
        "name": "extract",
        "desc": "Extract building footprints from TIFs using semantic segmentation ML model",
        "script": "filters/building_extractor.py",
        "inputs": ["data/tif"],
        "outputs": ["data/geojsons"]
    },
    3: {
        "name": "aggregate",
        "desc": "Merge individual building GeoJSON files into a single merged file",
        "script": "utility/aggregator.py",
        "inputs": ["data/geojsons"],
        "outputs": ["data/output/merged_buildings.geojson"]
    },
    4: {
        "name": "network",
        "desc": "Convert distribution circuit layer CSV and buildings GeoJSON to Pandapower JSON",
        "script": "filters/circuit_to_pandapower.py",
        "inputs": ["data/csv/Circuit_Layer_20260430.csv", "data/output/merged_buildings.geojson"],
        "outputs": ["data/json/circuit_network.json"]
    },
    5: {
        "name": "powerflow",
        "desc": "Perform AC/DC power flow simulation on the network model",
        "script": "filters/perform_power_flow.py",
        "inputs": ["data/json/circuit_network.json"],
        "outputs": ["data/json/circuit_network.json"]
    },
    6: {
        "name": "export",
        "desc": "Convert the grid network (including power flow results) to GeoJSON",
        "script": "filters/json_to_geojson.py",
        "inputs": ["data/json/circuit_network.json"],
        "outputs": ["data/output/circuit_network.geojson"]
    },
    7: {
        "name": "encode",
        "desc": "Convert massive GeoJSON layers into highly efficient Parquet files",
        "script": "utility/encoder.py",
        "inputs": ["data/output/merged_buildings.geojson", "data/output/circuit_network.geojson"],
        "outputs": ["data/output/merged_buildings.parquet", "data/output/circuit_network.parquet"]
    },
    8: {
        "name": "optimize",
        "desc": "Simplify geometries and pre-compute solar yield / saved CO2 stats",
        "script": "utility/optimize_data.py",
        "inputs": ["data/output/merged_buildings.parquet", "data/output/circuit_network.parquet"],
        "outputs": ["data/output/buildings_optimized.parquet", "data/output/circuit_optimized.parquet"]
    }
}

def print_banner(title: str, character: str = "=") -> None:
    """Print a visually prominent section banner to standard output."""
    logger.info(f"\n{character * 70}\n  {title}\n{character * 70}")

def setup_directories() -> None:
    """Ensure all expected input/output directories exist in the workspace."""
    dirs = [
        "data",
        "data/csv",
        "data/tif",
        "data/geojsons",
        "data/json",
        "data/output"
    ]
    for d in dirs:
        if not os.path.exists(d):
            os.makedirs(d, exist_ok=True)
            logger.info(f"Created directory: {d}")

def validate_step_assets(step_idx: int, check_outputs: bool = False) -> Tuple[bool, List[str]]:
    """
    Validate that all required input or output assets for a step are present.
    Returns (is_valid, list_of_missing_assets).
    """
    step = STEPS[step_idx]
    missing = []
    
    # Check script
    if not os.path.exists(step["script"]):
        missing.append(f"Script: {step['script']}")
        
    # Check assets
    target_keys = ["outputs"] if check_outputs else ["inputs"]
    for key in target_keys:
        for path in step[key]:
            if not os.path.exists(path):
                missing.append(path)
            elif os.path.isdir(path) and not os.listdir(path):
                # Directory exists but is empty
                # We warn but don't strictly fail for directory targets unless they are inputs
                if key == "inputs":
                    missing.append(f"{path} (empty directory)")
                    
    return len(missing) == 0, missing

def run_step(step_idx: int) -> bool:
    """Execute a single pipeline step in a clean subprocess."""
    step = STEPS[step_idx]
    script_path = step["script"]
    
    print_banner(f"Stage {step_idx}: {step['name'].upper()} - {step['desc']}", "-")
    
    # Validate inputs
    valid_inputs, missing_inputs = validate_step_assets(step_idx, check_outputs=False)
    if not valid_inputs:
        logger.error(f"Cannot run Stage {step_idx} ('{step['name']}'). Missing required inputs:")
        for mi in missing_inputs:
            logger.error(f"  - {mi}")
        return False
        
    start_time = time.time()
    logger.info(f"Executing: {sys.executable} {script_path}")
    
    try:
        # Run script in a subprocess to separate memory and environment
        process = subprocess.run(
            [sys.executable, script_path],
            stdout=sys.stdout,
            stderr=sys.stderr,
            check=True
        )
        elapsed = time.time() - start_time
        logger.info(f"Stage {step_idx} completed successfully in {elapsed:.2f} seconds.")
        
        # Verify outputs were created
        valid_outputs, missing_outputs = validate_step_assets(step_idx, check_outputs=True)
        if not valid_outputs:
            logger.warning(f"Stage {step_idx} finished but some expected outputs are missing:")
            for mo in missing_outputs:
                logger.warning(f"  - {mo}")
        return True
        
    except subprocess.CalledProcessError as e:
        elapsed = time.time() - start_time
        logger.error(f"Stage {step_idx} failed with exit code {e.returncode} after {elapsed:.2f} seconds.")
        return False
    except Exception as e:
        logger.error(f"Stage {step_idx} encountered an unexpected error: {e}")
        return False

def list_steps() -> None:
    """List all available pipeline steps with descriptions."""
    print("\nAvailable Pipeline Steps:")
    for idx, step in STEPS.items():
        print(f"  {idx}. {step['name']:<12} : {step['desc']}")
        print(f"       Script : {step['script']}")
        print(f"       Inputs : {', '.join(step['inputs'])}")
        print(f"       Outputs: {', '.join(step['outputs'])}")
        print()

def parse_steps_arg(arg: str) -> List[int]:
    """Parse steps command-line argument to a list of step indices."""
    indices = []
    # Support names or integers
    name_to_idx = {step["name"].lower(): idx for idx, step in STEPS.items()}
    
    parts = [p.strip().lower() for p in arg.split(",") if p.strip()]
    for part in parts:
        if part.isdigit():
            idx = int(part)
            if idx in STEPS:
                indices.append(idx)
            else:
                raise argparse.ArgumentTypeError(f"Invalid step index: {idx}. Must be 1 to 8.")
        elif part in name_to_idx:
            indices.append(name_to_idx[part])
        else:
            raise argparse.ArgumentTypeError(
                f"Unknown step identifier: '{part}'. Choose from: "
                f"{', '.join(name_to_idx.keys())} or 1-8"
            )
            
    # Remove duplicates but preserve order
    seen = set()
    return [x for x in indices if not (x in seen or seen.add(x))]

def perform_dry_run() -> bool:
    """Validate script paths, inputs, and directories without running pipeline."""
    print_banner("DRY-RUN VALIDATION REPORT", "*")
    all_ok = True
    
    # Check directory structure
    setup_directories()
    
    for idx, step in STEPS.items():
        # Check script existence
        script_ok = os.path.exists(step["script"])
        
        # Check inputs
        inputs_present = []
        inputs_missing = []
        for inp in step["inputs"]:
            if os.path.exists(inp):
                if os.path.isdir(inp) and not os.listdir(inp):
                    inputs_missing.append(f"{inp} (empty dir)")
                else:
                    inputs_present.append(inp)
            else:
                inputs_missing.append(inp)
                
        # Check outputs
        outputs_present = [out for out in step["outputs"] if os.path.exists(out)]
        
        status = "OK"
        if not script_ok:
            status = "FAILED (Script Missing)"
            all_ok = False
        elif inputs_missing:
            status = "WARNING (Inputs Missing)"
            # Note: Later stages might depend on early stage outputs which aren't built yet
            # We don't fail the dry-run if input is generated by a previous stage, but we label it
            
        print(f"Step {idx}: {step['name'].upper()}")
        print(f"  Description: {step['desc']}")
        print(f"  Script:      {step['script']} [{'Exists' if script_ok else 'MISSING'}]")
        print(f"  Inputs:      {len(inputs_present)} present, {len(inputs_missing)} missing")
        for mi in inputs_missing:
            print(f"    - MISSING: {mi}")
        print(f"  Outputs:     {len(outputs_present)}/{len(step['outputs'])} present")
        print(f"  Status:      {status}\n")
        
    return all_ok

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run GIS and Power Flow Data Processing Pipeline.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--all",
        action="store_true",
        help="Run the complete data pipeline (stages 1 to 8)."
    )
    group.add_argument(
        "--steps",
        type=parse_steps_arg,
        help="Comma-separated list of step numbers (1-8) or names to run.\n"
             "Example: --steps download,aggregate,network\n"
             "Example: --steps 3,4,5,6"
    )
    
    parser.add_argument(
        "--skip",
        type=parse_steps_arg,
        help="Steps to skip. Valid with --all or --steps."
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all pipeline steps and descriptions, then exit."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Verify directories, scripts, and inputs without executing stages."
    )
    
    args = parser.parse_args()
    
    if args.list:
        list_steps()
        return

    setup_directories()

    if args.dry_run:
        perform_dry_run()
        return

    # Determine steps to execute
    steps_to_run = []
    if args.all:
        steps_to_run = list(STEPS.keys())
    elif args.steps:
        steps_to_run = args.steps
    else:
        # Default behavior: Show usage and list steps
        parser.print_help()
        list_steps()
        return

    # Handle skips
    if args.skip:
        steps_to_run = [s for s in steps_to_run if s not in args.skip]

    if not steps_to_run:
        logger.warning("No steps selected for execution.")
        return

    print_banner("STARTING DATA PIPELINE EXECUTION", "=")
    logger.info(f"Target steps: {', '.join(STEPS[s]['name'] for s in steps_to_run)} ({steps_to_run})")
    
    pipeline_start = time.time()
    failed_steps = []
    
    for step_idx in steps_to_run:
        success = run_step(step_idx)
        if not success:
            logger.error(f"Pipeline execution halted due to failure in Stage {step_idx} ('{STEPS[step_idx]['name']}').")
            failed_steps.append(step_idx)
            break

    pipeline_elapsed = time.time() - pipeline_start
    print_banner("PIPELINE EXECUTION SUMMARY", "=")
    
    if failed_steps:
        logger.error(f"Pipeline failed at Stage {failed_steps[0]} ('{STEPS[failed_steps[0]]['name']}') after {pipeline_elapsed:.2f} seconds.")
        sys.exit(1)
    else:
        logger.info(f"All selected stages completed successfully in {pipeline_elapsed:.2f} seconds.")
        sys.exit(0)

if __name__ == "__main__":
    main()
