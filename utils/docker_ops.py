import re


def get_all_nodes(nodes):
    '''
    Return a snapshot of running containers from the nodes dict.
    '''
    return [node.container for node in nodes.values() if node.is_running]


def get_cc_nodes(nodes, prefix='CC'):
    '''
    Return a list of all active CC node containers.
    '''
    return [c for c in get_all_nodes(nodes) if c.name.startswith(prefix)]


def sort_containers(containers):
    '''
    Takes in a set of containers and returns the list sorted alphabetically and numerically
    (ensures that cc15 comes after cc9) and non numbered containers at the end.
    '''
    container_dict = {}
    non_numbered_containers = list()

    for container in containers:
        if 'CC' not in container.name:
            non_numbered_containers.append(container)
            continue

        idx = re.findall(r'\d+', container.name)
        idx = int(idx[0])
        container_dict[idx] = container

    sorted_container_dict = dict(sorted(container_dict.items()))
    return_set = list(sorted_container_dict.values())
    return_set = return_set + non_numbered_containers

    # check to make sure we're not losing any containers
    assert len(return_set) == len(containers)

    return return_set
