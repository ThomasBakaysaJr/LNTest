import glob
import csv
import time
import os
from rich.live import Live
from rich.table import Table
from rich.console import Console

CC_MESSAGE_PREFIX = 'NodeManagerComms/cc_messageLog_CC*'
UPDATE_INT = 1
console = Console()

def create_table(headers, data) -> Table:
    # clear screen and update
    table = Table(show_header=True)
            
    for h in headers:
        table.add_column(h)

    for row in data:
        row[0] = time.ctime(float(row[0]))
        table.add_row(*[str(word) for word in row])

    return table

def display_messages():
    with Live(screen=True, refresh_per_second=4) as live:
        while True:
            headers, data = update_data()
            table = create_table(headers, data)
            live.update(table)

            time.sleep(UPDATE_INT)

def update_data():
    message_files = glob.glob(CC_MESSAGE_PREFIX)
    headers = []
    all_data = []
    # we are going to read the last line for each file and display those
    for file in message_files:
        with open(file, 'r') as of:
            reader = csv.reader(of)

            if not headers:
                headers = next(reader)

            data = list(reader)
            if data and len(data) > 1:
                all_data.append(data[-1])
    return headers, all_data

def main():
    display_messages() 

            
    
if __name__ == "__main__":
    main()
