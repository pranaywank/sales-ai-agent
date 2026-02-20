#!/usr/bin/env python3
"""
Setup Cron Job Helper
Generates the cron line needed to run the sales agent automatically.
"""
import os
import sys

def main():
    cwd = os.getcwd()
    python_path = sys.executable
    script_path = os.path.join(cwd, "zoho_agent.py")
    
    print("\n--- Sales Agent Scheduler Setup ---")
    print("To run this agent automatically (e.g., every weekday at 9 AM),")
    print("add the following line to your crontab.")
    
    print("\n1. Open Terminal and type:")
    print("   crontab -e")
    
    print("\n2. Paste this line at the bottom:")
    # Cron format: m h  dom mon dow   command
    # 0 9 * * 1-5  (9:00 AM Mon-Fri)
    print(f"   0 9 * * 1-5 cd '{cwd}' && '{python_path}' '{script_path}' --auto >> agent.log 2>&1")
    
    print("\nNote: The '--auto' flag ensures the script runs without waiting for user input.")

if __name__ == "__main__":
    main()
