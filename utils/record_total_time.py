import time
import datetime
import json
import os

def record_total_time(total_time, config, output_suffix="total_times_log.json"):
    '''
    Create a running record of the total time taken for test runs 
    along with their configuration(s) to a JSON file.
    Parameters:
        total_time (float): The total time taken for the test run in seconds.
        config (dict): The configuration dictionary used for the test run.
            Can be a single config or a list of configs.
        output_file (str): The path to the JSON file where the log will be stored.'''
    os.makedirs("data", exist_ok=True)
    filename = datetime.datetime.now().strftime(f"data/%Y-%m-%d_{output_suffix}")

    log_entry = {
        'timestamp': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_time": total_time,
        "config": config
    }
    
    # Append the log entry to the JSON file
    with open(filename, 'a') as f:
        # Write the line + a newline character
        f.write(json.dumps(log_entry) + "\n")

    print(f'Recorded total time: {total_time} seconds to {filename}')