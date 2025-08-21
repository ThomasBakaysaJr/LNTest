import glob
import csv
import time
import subprocess
import re
import json
from rich.live import Live
from rich.table import Table
from rich.console import Console
from rich import box

CC_MESSAGE_PREFIX = 'NodeManagerComms/status/cc_messageLog_*'
CC_STATUS_PREFIX = 'NodeManagerComms/status/status_*'
LOG_TRACKER = 'log_tracker.py'
UPDATE_INT = 0.1

# constants to make things easier to use
TIME = 0 
SHORT_ID = 1
CC_CONTAINER = 2
COUNTER = 3
MESSAGE = 4 

console = Console()

def create_table(data_headers, data, status_headers, status) -> Table:
    if not data:
        return
    # clear screen and update
    table = Table(show_header=True,
                  padding=(0, 1),
                  box=box.SQUARE,
                  border_style='grey70'
                  )
    
    # data is stored in ['Time', 'Short_ID', 'CC container', 'Message Counter', 'Message']
    for h in data_headers:
        table.add_column(h)

    interval, msg_propagated = get_time_interval(data)

    for row in data:
        row[TIME] = time.ctime(float(row[TIME]))
        table.add_row(*[str(word) for word in row])

    table.add_row(table.add_row("Propagation time", f'{interval:0.3f} s', (f'Propagated: {msg_propagated}')))

    # building status section

    table.add_section()

    # status is stored in ['short_id', 'state', 'capacity', 'our_amount']
    table.add_row(*[str(h) for h in status_headers])

    for row in status:
        if len(row) <= 3:
            table.add_section()
        table.add_row(*[str(word) for word in row])

    return table

def get_time_interval(data):
    # data is stored in ['Time', 'Short_ID', 'CC container', 'Message Counter', 'Message']
    top_count = max([row[COUNTER] for row in data])
    top_data = [row for row in data if row[COUNTER] == top_count]

    is_done = len(top_data) == len(data)

    times = [float(row[TIME]) for row in top_data]
    interval = max(times) - min(times)

    return interval, is_done

def display_messages():
    with Live(screen=True, refresh_per_second=4) as live:
        while True:
            data_headers, data, status_headers, status_data = update_data()

            table = create_table(data_headers, data, status_headers, status_data)
            live.update(table)

            time.sleep(UPDATE_INT)

def update_data():
    sorted_msg_files = sort_files(glob.glob(CC_MESSAGE_PREFIX))
    sorted_status_files = sort_files(glob.glob(CC_STATUS_PREFIX))
    headers = []
    all_data = []
    status_data = []

    # get all the data and find the one with the highest counter
    # the message_logs aren't alway going to be in order
    for msg_file, status_file in zip(sorted_msg_files, sorted_status_files):
        with open(msg_file, 'r') as of:
            reader = csv.reader(of)
            headers = next(reader)
            data = list(reader)
            sorted_data = sorted(data, key = lambda data : int(data[COUNTER]))

            all_data.append(sorted_data[-1])
        with open(status_file, 'r') as of:
            try:
                status = json.load(of)
            except Exception  as e:
                continue
            temp_data = [status.get('name'), status.get('state'), status.get('receiver')]
            temp_data.append(status.get('channels'))
            status_data.append(temp_data)

    status_headers, status_data = process_status(status_data)

    return headers, all_data, status_headers, status_data

def process_status(in_status):
    out_status = []
    headers = ['']
    for channel in in_status:
        temp_status_data = []
        for data_point in channel:
            if isinstance(data_point, str):
                temp_status_data.append(data_point)
            else:
                # pre-lim info should be loaded now, so append
                out_status.append(temp_status_data)
                for entry in data_point:
                    temp_channel_data = ['']
                    for key, value in data_point[entry].items():
                        if len(headers) <= len(data_point[entry]):
                            headers.append(key)
                        temp_channel_data.append(value)
                    out_status.append(temp_channel_data)

    return headers, out_status

def sort_files(in_files):
    '''
    Takes in a list of files and returns the list sorted alphabetically and numerically 
    (ensures that cc15 comes after cc9)
    '''
    file_dict = {}
    for file in in_files:
        idx = re.findall(r'\d+', file)
        idx = int(idx[0])
        file_dict[idx] = file

    sorted_files_dict = dict(sorted(file_dict.items()))
    return list(sorted_files_dict.values())

def main():
    subprocess.Popen(['python', LOG_TRACKER])
    display_messages() 
    # test()

def test():
    headers, all_data, status_headers, status_data = update_data()
    print(headers)
    print('next')
    print(all_data)
    print('next')
    print(status_headers)
    print('next')
    print(status_data)
    time.sleep(5)
    
if __name__ == "__main__":
    main()
