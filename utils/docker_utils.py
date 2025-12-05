import docker

def ensure_custom_image(image_name, cln_version):
    """
    Checks if the custom Docker image exists. If not, builds it from the Dockerfile.
    """
    client = docker.from_env()
    
    try:

        print(f"Checking for image {image_name}...")
        client.images.get(image_name)
        print(f"Image {image_name} found.")
    except docker.errors.ImageNotFound:
        print(f"Image {image_name} not found. Building... (This may take a minute)")
        try:
            # Assumes Dockerfile is in the same directory as lntest.py
            # 'path' is the directory containing the Dockerfile
            image, build_logs = client.images.build(
                path=".", 
                tag=image_name,
                buildargs={"CLN_VERSION" : cln_version})
            for chunk in build_logs:
                if 'stream' in chunk:
                    print(chunk['stream'].strip())
            print(f"Successfully built {image_name}.")
        except docker.errors.BuildError as e:
            print(f"Error building image: {e}")
            exit(1)