import logging
import docker

log = logging.getLogger(__name__)

def ensure_custom_image(image_name, cln_version):
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
                tag=image_name,
                buildargs={"CLN_VERSION" : cln_version})
            for chunk in build_logs:
                if 'stream' in chunk:
                    log.info(chunk['stream'].strip())
            log.info(f"Successfully built {image_name}.")
        except docker.errors.BuildError as e:
            log.error(f"Error building image: {e}")
            exit(1)