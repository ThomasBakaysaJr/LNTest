ARG CLN_TAG=latest
FROM elementsproject/lightningd:${CLN_TAG}

# cln_tag label lets ensure_custom_image() detect when a newer CLN tag exists.
ARG CLN_TAG=latest
LABEL cln_tag="${CLN_TAG}"

# Install Python3
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# don't need a virtual environment
RUN pip3 install pyln-client --break-system-packages