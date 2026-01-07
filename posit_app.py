#!/usr/bin/env python3
"""
Node Selector Script for Posit Workbench
This script selects between P (Primary) and V (Alternative) nodes
"""

import sys
import random
import json
from datetime import datetime
import argparse

def select_node(choice=None):
    """
    Select a node based on choice or auto-select logic
    
    Args:
        choice (str): 'P' for Primary, 'V' for Alternative, None for auto-select
    
    Returns:
        tuple: (selected_node, node_details)
    """
    # Node configurations
    node_configs = {
        "P": {
            "display_name": "Primary Node",
            "cluster": "Primary",
            "resources": {
                "cpu": "High performance CPUs",
                "memory": "Large memory pool",
                "gpu": "Available",
                "storage": "Fast SSD storage"
            },
            "priority": "high"
        },
        "V": {
            "display_name": "Alternative Node",
            "cluster": "Alternative",
            "resources": {
                "cpu": "Standard CPUs",
                "memory": "Medium memory",
                "gpu": "Not available",
                "storage": "Standard storage"
            },
            "priority": "normal"
        }
    }
    
    # If choice is provided, use it if valid
    if choice and choice.upper() in ["P", "V"]:
        selected = choice.upper()
    else:
        # Auto-selection logic
        # Example: Based on time of day, round-robin, or load
        hour = datetime.now().hour
        
        # Business hours (9 AM - 5 PM) -> Primary node
        if 9 <= hour < 17:
            selected = "P"
        else:
            # Outside business hours -> 70% chance of Primary, 30% Alternative
            selected = "P" if random.random() < 0.7 else "V"
    
    # Get node details
    node_details = node_configs[selected].copy()
    
    return selected, node_details

def main():
    """Main function to handle command line arguments"""
    parser = argparse.ArgumentParser(description="Select node for Posit Workbench")
    parser.add_argument("choice", nargs="?", choices=["P", "V", "p", "v"], 
                       help="Optional: P for Primary, V for Alternative")
    
    args = parser.parse_args()
    
    # Get user choice from args
    user_choice = args.choice.upper() if args.choice else None
    
    # Select node
    selected_node, node_details = select_node(user_choice)
    
    # Output as JSON
    output = {
        "selected_node": selected_node,
        "node_details": node_details,
        "timestamp": datetime.now().isoformat(),
        "selection_method": "user_choice" if user_choice else "auto_select"
    }
    
    # Print JSON output
    print(json.dumps(output, indent=2))
    
    # Also print human-readable output
    print(f"\nSelected node: {selected_node} - {node_details['display_name']}")
    print(f"Resources: {', '.join(node_details['resources'].values())}")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
