import docker
from multiprocessing import shared_memory
import json

def retrieve_all_status():
    cc_containers = get_cc_containers()
    all_status = []

    for cont in cc_containers:
        node_name = f'{cont.name}_status'
        try:
            shm = shared_memory.SharedMemory(name=node_name)
            data = shm.buf.tobytes().split(b'\x00', 1)[0]
            shm.close()

            if not data:
                continue

            status = json.loads(data.decode('utf-8'))
            all_status.append(status)
        except Exception as e:
            print(f'so the try block failed for {node_name} for reason {e}')
            continue
    return all_status

def get_cc_containers():
    '''
    Get the set of all CC server docker containers
    '''
    try:
        client = docker.from_env()
        containers = set(client.containers.list(filters={'status' : 'running', 'name': '^CC'}))
    except docker.errors.DockerException as e:
        print(f'get_cc_containers: Error with docker module. Error: {e}')
        return set()

    return containers

def print_topology():
    topology = retrieve_all_status()
    if topology:
        for node in topology:
            print(f'{node.get('name')} : {node.get('short id')} : {node.get('state')} : channel count = {len(node.get('channels'))}')
            for channel in node.get('channels'):
                print(node.get('channels')[channel])
            print('')

def main():
    print_topology()

if __name__ == '__main__':
    main()