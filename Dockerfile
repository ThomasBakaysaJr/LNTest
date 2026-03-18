FROM elementsproject/lightningd:latest

# Install Python3
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# don't need a virtual environment
RUN pip3 install pyln-client --break-system-packages