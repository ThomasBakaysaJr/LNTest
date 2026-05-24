import json
import logging
import re
import urllib.request

import docker

log = logging.getLogger(__name__)

# LNTest builds its container image on top of the most recent tag of this
# upstream Core Lightning image, looked up from Docker Hub at build time.
_CLN_IMAGE = 'elementsproject/lightningd'
_DOCKERHUB_TAGS_URL = (
    f'https://hub.docker.com/v2/repositories/{_CLN_IMAGE}/tags/'
    '?page_size=100&ordering=last_updated')
# Matches release tags (v26.04, v26.04.1, v26.06rc1) while rejecting meta tags
# (latest, stable) and variant tags (e.g. v26.06rc1-vls).
_CLN_TAG_RE = re.compile(r'^v(\d+)\.(\d+)(?:\.(\d+))?(?:rc(\d+))?$')


def get_all_nodes(nodes):
    '''
    Cached container objects for tracked nodes -- no per-node container.reload().
    Killed nodes are dropped from the tracker, so all returned nodes are live.
    '''
    containers = []
    for node in nodes.values():
        container = node.container  # lazy-loaded, then cached on the Node
        if container is not None:
            containers.append(container)
    return containers


def get_cc_nodes(nodes, prefix='CC'):
    '''
    Return a list of all active CC node containers.
    '''
    return [c for c in get_all_nodes(nodes) if c.name.startswith(prefix)]


def get_latest_cln_tag():
    """
    Query Docker Hub for the newest Core Lightning release tag of the
    elementsproject/lightningd image, release candidates included.

    Returns a tag string such as 'v26.06rc1', or None when Docker Hub cannot
    be reached (for example when running offline).
    """
    try:
        request = urllib.request.Request(
            _DOCKERHUB_TAGS_URL, headers={'User-Agent': 'LNTest'})
        with urllib.request.urlopen(request, timeout=10) as response:
            results = json.load(response).get('results', [])
    except Exception as e:
        log.warning(f"Could not query Docker Hub for the latest CLN tag: {e}")
        return None

    latest_tag = None
    latest_key = None
    for entry in results:
        match = _CLN_TAG_RE.match(entry.get('name', ''))
        if match is None:
            continue
        major, minor, patch, rc = match.groups()
        # A final release outranks its own release candidates.
        key = (int(major), int(minor), int(patch or 0),
               0 if rc else 1, int(rc or 0))
        if latest_key is None or key > latest_key:
            latest_key = key
            latest_tag = entry['name']

    if latest_tag is None:
        log.warning("No Core Lightning release tags found on Docker Hub.")
    return latest_tag


def image_exists(image_name):
    """True if the LNTest Docker image has already been built locally."""
    try:
        docker.from_env().images.get(image_name)
        return True
    except docker.errors.ImageNotFound:
        return False


def ensure_custom_image(image_name):
    """
    Build the LNTest Docker image on top of the most recent Core Lightning
    release published to Docker Hub.

    A rebuild is skipped when the image already exists and was built from the
    current latest tag; when a newer tag is published the image is rebuilt
    against it. If Docker Hub is unreachable, an existing image is reused.
    """
    client = docker.from_env()

    cln_tag = get_latest_cln_tag()
    online = cln_tag is not None

    try:
        existing = client.images.get(image_name)
    except docker.errors.ImageNotFound:
        existing = None

    if not online:
        if existing is not None:
            log.warning(f"Could not reach Docker Hub; reusing existing "
                        f"image {image_name}.")
            return
        cln_tag = 'latest'
        log.warning("Could not reach Docker Hub and no LNTest image exists; "
                    "building from elementsproject/lightningd:latest.")
    elif existing is not None and existing.labels.get('cln_tag') == cln_tag:
        log.info(f"Image {image_name} already built from Core Lightning "
                 f"{cln_tag}.")
        return

    log.info(f"Building {image_name} from elementsproject/lightningd:"
             f"{cln_tag}... (this may take a minute)")
    try:
        # 'path' is the directory containing the Dockerfile (the LNTest root).
        _image, build_logs = client.images.build(
            path=".",
            tag=image_name,
            buildargs={'CLN_TAG': cln_tag},
            pull=online)
        for chunk in build_logs:
            if 'stream' in chunk:
                log.info(chunk['stream'].strip())
        log.info(f"Successfully built {image_name} (Core Lightning {cln_tag}).")
    except docker.errors.BuildError as e:
        log.error(f"Error building image: {e}")
        exit(1)


if __name__ == "__main__":
    # Invoked by setup.sh: build (or rebuild against a newer CLN release) the
    # image once at setup, so the CLN version stays fixed across test runs.
    from utils.config import cfg
    cfg.load()
    ensure_custom_image(cfg.LNTEST_VERSION)
