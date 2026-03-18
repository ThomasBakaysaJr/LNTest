import logging
import re

import docker

log = logging.getLogger(__name__)


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


def ensure_custom_image(image_name):
    """
    Checks if the custom Docker image exists. If not, builds it from the Dockerfile.
    """
    client = docker.from_env()

    try:
        log.info(f"Checking for image {image_name}...")
        client.images.get(image_name)
        log.info(f"Image {image_name} found.")
    except docker.errors.ImageNotFound:
        log.info(f"Image {image_name} not found. Building... (This may take a minute)")
        try:
            # Assumes Dockerfile is in the same directory as lntest.py
            # 'path' is the directory containing the Dockerfile
            image, build_logs = client.images.build(
                path=".",
                tag=image_name)
            for chunk in build_logs:
                if 'stream' in chunk:
                    log.info(chunk['stream'].strip())
            log.info(f"Successfully built {image_name}.")
        except docker.errors.BuildError as e:
            log.error(f"Error building image: {e}")
            exit(1)
