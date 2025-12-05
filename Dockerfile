# Version of lightningd that we're using
ARG CLN_VERSION=v25.09
FROM elementsproject/lightningd:${CLN_VERSION}

# Install Python3
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# don't need a virtual environment
RUN pip3 install pyln-client --break-system-packages