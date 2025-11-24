# Version of lightningd that we're using
FROM elementsproject/lightningd:v25.09

# Install Python3
RUN apt-get update && apt-get install -y \
    python3 \
    && rm -rf /var/lib/apt/lists/*