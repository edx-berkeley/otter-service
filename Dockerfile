FROM ubuntu:22.04
ARG DEBIAN_FRONTEND=noninteractive
ARG OTTER_SERVICE_VERSION

ENV DOCKER_VERSION=5:24.0.4-1~ubuntu.22.04~jammy

RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y \
        python3 \
        python3-pip \
        curl \
        ca-certificates \
        gnupg \
        lsb-release && \
    rm -rf /var/lib/apt/lists/*

# Install docker cli from upstream apt repo
RUN mkdir -p /etc/apt/keyrings && \
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
        | tee /etc/apt/sources.list.d/docker.list > /dev/null && \
    apt-get update && \
    apt-get -y install docker-ce-cli=${DOCKER_VERSION} && \
    rm -rf /var/lib/apt/lists/*

# Stable deps (rarely change) — kept on a separate layer so otter-grader bumps don't reinstall them
COPY ./requirements/requirements-base.txt /tmp/requirements-base.txt
RUN python3 -m pip install --no-cache-dir -r /tmp/requirements-base.txt

# Volatile deps (otter-grader changes more frequently with upstream issues)
COPY ./requirements/requirements-grader.txt /tmp/requirements-grader.txt
RUN python3 -m pip install --no-cache-dir -r /tmp/requirements-grader.txt

# otter-service itself — last layer, rebuilds on every release
COPY . /opt/otter-service-src/
RUN if [ -n "${OTTER_SERVICE_VERSION}" ]; then \
      python3 -m pip install --no-cache-dir otter-service==${OTTER_SERVICE_VERSION}; \
    else \
      python3 -m pip install --no-cache-dir /opt/otter-service-src/; \
    fi

WORKDIR /opt

EXPOSE 10101
ENTRYPOINT ["otter_service"]
